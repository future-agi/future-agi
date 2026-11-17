"""Background analysis of a trace whose root names a hidden simulation project.

Real PostgreSQL rows. The ClickHouse root reader and the analysis agent are
stood in; no provider is called and nothing is charged.
"""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.ai_model import AIModel
from simulate.models.agent_definition import AgentDefinition
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.background_trace_analysis_state import (
    BackgroundTraceAnalysisState,
)
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.services.analysis_result_binding import current_attempt
from tracer.tasks import error_analysis

pytestmark = pytest.mark.django_db(transaction=True)

AGENT = "ee.agenthub.traceerroragent.traceerror.TraceErrorAnalysisAgent"
CHARGE = "ee.usage.utils.usage_entries.log_and_deduct_cost_for_api_request"


def failed(trace_id):
    return {"success": False, "trace_id": trace_id, "code": "analysis_failed"}


def on_demand(trace_id):
    # The feed's dispatch for a trace that has no lifecycle row yet.
    return error_analysis.run_deep_analysis_on_demand(trace_id, False)


def forced_on_demand(trace_id):
    # The same dispatch when the person asks for a fresh run.
    return error_analysis.run_deep_analysis_on_demand(trace_id, True)


def batch_trace(trace_id):
    # One trace of a scheduled batch, called as the batch's thread pool does.
    return error_analysis.analyze_single_trace(trace_id, str(uuid.uuid4()))


@pytest.fixture
def projects(organization, workspace):
    def create(manager, name, **extra):
        return manager.create(
            name=name,
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            **extra,
        )

    agent = AgentDefinition.objects.create(
        agent_name="Simulated support agent",
        description="Simulated support agent description",
        agent_type="text",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    hidden = create(
        Project.with_simulation, "Simulated support runs", simulation_agent=agent
    )
    ordinary = create(Project.objects, "Ordinary support agent")
    # A worker runs these entries without a request's workspace.
    clear_workspace_context()
    return SimpleNamespace(hidden=hidden, ordinary=ordinary)


@contextmanager
def stand_ins(roots):
    """Stand in the root reader for ``roots``; record the agent and the charge."""

    @contextmanager
    def reader():
        yield SimpleNamespace(
            root_ids_by_trace_ids=lambda ids: {
                trace_id: roots[trace_id] for trace_id in ids if trace_id in roots
            }
        )

    with (
        patch("tracer.services.clickhouse.v2.get_reader", reader),
        patch(AGENT) as agent,
        patch(CHARGE) as charge,
    ):
        yield SimpleNamespace(agent=agent, charge=charge)


@pytest.mark.parametrize(
    "enter",
    [on_demand, forced_on_demand, batch_trace],
    ids=["on_demand", "forced_on_demand", "batch_trace"],
)
def test_hidden_project_trace_answers_as_unknown_and_writes_no_state(enter, projects):
    hidden = projects.hidden
    assert not Project.objects.filter(pk=hidden.pk).exists()
    assert Project.with_simulation.filter(pk=hidden.pk).exists()
    hidden_trace, unknown_trace = str(uuid.uuid4()), str(uuid.uuid4())

    with stand_ins({hidden_trace: ("root-span", str(hidden.pk))}) as seen:
        unknown_answer = enter(unknown_trace)
        hidden_answer = enter(hidden_trace)

    assert unknown_answer == failed(unknown_trace)
    assert hidden_answer == failed(hidden_trace)
    assert not BackgroundTraceAnalysisState.objects.filter(
        project_id=hidden.pk
    ).exists()
    assert not BackgroundTraceAnalysisState.objects.filter(
        trace_id=hidden_trace
    ).exists()
    assert not BackgroundTraceAnalysisState.objects.filter(
        trace_id=unknown_trace
    ).exists()
    seen.agent.assert_not_called()
    seen.charge.assert_not_called()


@pytest.mark.parametrize("succeeds", [True, False], ids=["completes", "aborts"])
def test_ordinary_project_trace_still_begins_admits_and_settles(succeeds, projects):
    ordinary = projects.ordinary
    trace = Trace.objects.create(project=ordinary, name="Ordinary trace")
    trace_id = str(trace.pk)
    inside = []

    # Stands in for the analysis body. A real success needs a validated
    # publication bound to the attempt, which only the agent can produce.
    def analysis(trace_id, task_id, ingest_embeddings=True):
        row = BackgroundTraceAnalysisState.objects.get(trace_id=trace_id)
        inside.append((current_attempt(), row.project_id, row.status))
        if not succeeds:
            return {"success": False, "trace_id": trace_id, "error": "no findings"}
        TraceErrorAnalysis.drafts.create(
            project=ordinary, trace=trace, overall_score=4, total_errors=0
        )
        return {"success": True, "trace_id": trace_id, "error_count": 0}

    with (
        stand_ins({trace_id: ("root-span", str(ordinary.pk))}),
        patch.object(error_analysis, "_analyze_single_trace_in_scope", analysis),
    ):
        answer = on_demand(trace_id)

    [(identity, project_id, status)] = inside
    assert identity[:2] == (str(ordinary.pk), trace_id)
    assert project_id == ordinary.pk
    assert status == "running"
    row = BackgroundTraceAnalysisState.objects.get(trace_id=trace_id)
    assert row.project_id == ordinary.pk
    assert str(row.generation) == identity[2]
    if succeeds:
        assert answer == {"success": True, "trace_id": trace_id, "error_count": 0}
        assert row.status == "completed"
        assert str(row.result_analysis_id) == identity[2]
        assert str(TraceErrorAnalysis.objects.get(trace=trace).pk) == identity[2]
    else:
        assert answer == failed(trace_id)
        assert row.status == "aborted"
        assert row.reason_code == "analysis_failed"
