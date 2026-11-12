"""Tenant isolation through the real background agent and registered tools."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from ai_tools.base import ToolContext
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.tasks.error_analysis import _falcon_analyze_single_trace
from tracer.tests.test_falcon_error_analysis import (
    falcon_boundaries as falcon_boundaries,
)

pytestmark = pytest.mark.requires_ee


@pytest.fixture(params=["organization", "workspace", "project"])
def other_project(request, project):
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    org = project.organization
    workspace = project.workspace
    if request.param == "organization":
        org = Organization.objects.create(name="Other organization")
    if request.param != "project":
        user = User.objects.create_user(
            email=f"other-{uuid.uuid4().hex}@example.invalid", organization=org
        )
        workspace = Workspace.objects.create(
            name="Other workspace", organization=org, created_by=user
        )
    return Project.objects.create(
        name="Other project",
        organization=org,
        workspace=workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )


def score_params(trace_id):
    return {
        "trace_id": trace_id,
        "overall_score": 2.0,
        "factual_grounding_score": 2.0,
        "factual_grounding_reason": "Own score",
        "privacy_and_safety_score": 2.0,
        "privacy_and_safety_reason": "Own score",
        "instruction_adherence_score": 2.0,
        "instruction_adherence_reason": "Own score",
        "optimal_plan_execution_score": 2.0,
        "optimal_plan_execution_reason": "Own score",
        "insights": "Requesting project only",
        "recommended_priority": "MEDIUM",
    }


def finding_params(trace_id):
    return {
        "trace_id": trace_id,
        "category": "Thinking > Hallucination",
        "location_spans": ["shared-span"],
        "evidence_snippets": ["Own evidence"],
        "description": "Own finding",
        "impact": "MEDIUM",
        "root_causes": ["Own cause"],
        "recommendation": "Own fix",
        "confidence": 0.9,
    }


def install_reader(monkeypatch, project, trace_id, *, foreign=None):
    reader = MagicMock()

    def roots(trace_ids, project_ids=None):
        # Reproduce CH's first-root-wins behavior when the project is omitted.
        selected = foreign if foreign is not None and project_ids is None else project
        if project_ids is not None and str(selected.id) not in project_ids:
            return {}
        return {trace_id: ("shared-span", str(selected.id))}

    reader.root_ids_by_trace_ids.side_effect = roots
    cm = MagicMock()
    cm.__enter__.return_value = reader
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: cm)
    return reader


def install_tool_stream(monkeypatch, tool_name, params, *, fail_after_tool=False):
    from ee.falcon_ai.llm_client import FalconLLMClient

    calls = []

    async def stream(self, messages, tools=None):
        calls.append(messages)
        if len(calls) == 1:
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "tenant-call",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(params),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        elif fail_after_tool:
            raise httpx.RemoteProtocolError("Interrupted after scoring")
        else:
            yield {
                "choices": [
                    {"delta": {"content": "Finished."}, "finish_reason": "stop"}
                ]
            }

    monkeypatch.setattr(FalconLLMClient, "stream_completion", stream)
    return calls


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("explicit_analysis_id", [False, True])
@pytest.mark.parametrize("existing_analysis", [False, True])
def test_background_scoring_never_updates_foreign_analysis(
    project,
    other_project,
    falcon_boundaries,
    monkeypatch,
    explicit_analysis_id,
    existing_analysis,
):
    trace_id = str(uuid.uuid4())
    own = None
    if existing_analysis:
        own = TraceErrorAnalysis.objects.create(trace_id=trace_id, project=project)
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project, insights="Private foreign analysis"
    )
    before = TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get()
    install_reader(monkeypatch, project, trace_id)
    ingest = Mock()
    monkeypatch.setattr(
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        ingest,
    )
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.cluster_project_errors.delay", Mock()
    )
    params = score_params(trace_id)
    if explicit_analysis_id:
        params["analysis_id"] = str(foreign.id)
    calls = install_tool_stream(monkeypatch, "submit_trace_scores", params)

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert result["success"] is True
    assert len(calls) == 2
    assert TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get() == before
    analysis = TraceErrorAnalysis.objects.get(trace_id=trace_id, project=project)
    assert analysis.overall_score == 2.0
    assert analysis.insights == params["insights"]
    if own:
        assert analysis.pk == own.pk


@pytest.mark.django_db(transaction=True)
def test_background_scoring_resolves_colliding_root_in_requesting_project(
    project,
    other_project,
    falcon_boundaries,
    monkeypatch,
):
    trace_id = str(uuid.uuid4())
    reader = install_reader(monkeypatch, project, trace_id, foreign=other_project)
    monkeypatch.setattr(
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        Mock(),
    )
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.cluster_project_errors.delay", Mock()
    )
    install_tool_stream(monkeypatch, "submit_trace_scores", score_params(trace_id))

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert result["success"] is True
    analysis = TraceErrorAnalysis.objects.get(trace_id=trace_id, project=project)
    assert analysis.overall_score == 2.0
    assert not TraceErrorAnalysis.objects.filter(project=other_project).exists()
    for call in reader.root_ids_by_trace_ids.call_args_list:
        assert call.kwargs["project_ids"] == [str(project.id)]


@pytest.mark.django_db(transaction=True)
def test_failed_stream_after_scoring_does_not_publish_completion(
    project,
    falcon_boundaries,
    monkeypatch,
):
    trace_id = str(uuid.uuid4())
    install_reader(monkeypatch, project, trace_id)
    ingest = Mock()
    cluster = Mock()
    monkeypatch.setattr(
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        ingest,
    )
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.cluster_project_errors.delay", cluster
    )
    install_tool_stream(
        monkeypatch, "submit_trace_scores", score_params(trace_id), fail_after_tool=True
    )

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert result["success"] is False
    assert "Interrupted after scoring" in result["error"]
    clear, failed, clickhouse = falcon_boundaries
    clear.assert_not_called()
    failed.assert_called_once_with(trace_id, project_id=str(project.id))
    ingest.assert_not_called()
    cluster.assert_not_called()
    clickhouse.return_value.insert.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_background_cannot_invoke_unscoped_registry_tools(
    project, falcon_boundaries, monkeypatch
):
    from ai_tools.registry import registry

    tool = registry.get("analyze_project_traces")
    execute = Mock()
    monkeypatch.setattr(tool, "execute", execute)
    install_tool_stream(
        monkeypatch, "analyze_project_traces", {"project_id": str(project.id)}
    )

    _falcon_analyze_single_trace(str(uuid.uuid4()), str(project.id))

    execute.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "tool_name", ["read_trace_span", "explore_trace_legacy", "submit_trace_finding"]
)
def test_analysis_tools_use_project_scoped_ch_and_analysis_rows(
    project,
    other_project,
    monkeypatch,
    tool_name,
):
    from ai_tools.registry import registry

    trace_id = str(uuid.uuid4())
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project
    )
    before = TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get()
    context = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
    )
    context.project = project
    reader = install_reader(monkeypatch, project, trace_id, foreign=other_project)
    span = SimpleNamespace(
        id="shared-span",
        name="Own span",
        observation_type="llm",
        status="OK",
        model="test",
        latency_ms=1,
        total_tokens=1,
        parent_span_id=None,
        start_time=None,
        input="Own evidence",
        output="Own output",
        metadata={},
        span_events=[],
    )
    reader.list_by_trace.return_value = [span] if tool_name == "read_trace_span" else []
    params = {"trace_id": trace_id}
    if tool_name == "read_trace_span":
        params["span_id"] = "shared-span"
    elif tool_name == "submit_trace_finding":
        params = finding_params(trace_id) | {"analysis_id": str(foreign.id)}

    result = registry.get(tool_name).run(params, context)

    assert not result.is_error, result.content
    assert TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get() == before
    reader.root_ids_by_trace_ids.assert_called_once_with(
        [trace_id], project_ids=[str(project.id)]
    )
    if tool_name == "submit_trace_finding":
        own = TraceErrorAnalysis.objects.get(project=project, trace_id=trace_id)
        assert own.total_errors == 1
        assert TraceErrorDetail.objects.get(analysis=own).description == "Own finding"
        assert not TraceErrorDetail.objects.filter(analysis=foreign).exists()
    else:
        reader.list_by_trace.assert_called_once_with(
            trace_id, project_id=str(project.id)
        )
        if tool_name == "read_trace_span":
            assert "Own evidence" in result.content


@pytest.mark.django_db
def test_embedding_ingestion_selects_only_requesting_project(
    project, other_project, monkeypatch
):
    from agentic_eval.core.embeddings.embedding_manager import (
        EmbeddingManager,
        model_manager,
    )
    from ee.agenthub.traceerroragent.error_cluster import ErrorEmbeddingClusterer
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    trace_id = str(uuid.uuid4())
    own = TraceErrorAnalysis.objects.create(trace_id=trace_id, project=project)
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project
    )
    detail = TraceErrorDetail.objects.create(
        analysis=own, error_id="E001", category="Own", description="Own evidence"
    )
    TraceErrorDetail.objects.create(
        analysis=foreign, error_id="E001", category="Foreign", description="Private"
    )
    embed = Mock(return_value=[0.1, 0.2])
    monkeypatch.setattr(type(model_manager), "text_model", property(lambda self: embed))
    db_client = MagicMock()
    monkeypatch.setattr(
        EmbeddingManager, "__init__", lambda self: setattr(self, "db_client", db_client)
    )
    monkeypatch.setattr(EmbeddingManager, "close", Mock())
    monkeypatch.setattr(
        TraceErrorAnalysisDB, "_embedding_exists_for_detail", lambda *args: False
    )
    build = Mock(side_effect=lambda row: row.description)
    monkeypatch.setattr(
        ErrorEmbeddingClusterer, "_build_embedding_text_from_detail", build
    )

    count = TraceErrorAnalysisDB().ingest_trace_error_embeddings(
        trace_id,
        project_id=str(project.id),
        organization_id=str(project.organization_id),
    )

    assert count == 1
    embed.assert_called_once_with("Own evidence")
    assert build.call_args.args[0].id == detail.id
    assert db_client.bulk_upsert_vectors.call_args.kwargs["eval_id"] == str(project.id)
    assert db_client.bulk_upsert_vectors.call_args.kwargs["unique_keys"] == [
        "project_id",
        "error_detail_pk",
    ]


def test_project_markers_do_not_collide_and_failure_wins_over_partial_analysis():
    from django.core.cache import cache

    from tracer.queries import deep_analysis_state as state

    trace_id = str(uuid.uuid4())
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        assert state.set_running(trace_id, project_id=first)
        assert state.set_running(trace_id, project_id=second)
        state.set_failed(trace_id, project_id=first)
        assert state.status(trace_id, project_id=first, has_analysis=True) == "failed"
        assert (
            state.status(trace_id, project_id=second, has_analysis=False) == "running"
        )
        state.clear(trace_id, project_id=second)
        assert state.status(trace_id, project_id=first, has_analysis=False) == "failed"
    finally:
        cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "missing_scope", ["organization", "workspace", "foreign_project"]
)
def test_trace_access_fails_closed_before_clickhouse(
    project, other_project, monkeypatch, missing_scope
):
    from ai_tools.tools.tracing.access import get_trace_project

    context = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
        project=project,
    )
    if missing_scope == "foreign_project":
        context.organization = other_project.organization
        context.workspace = other_project.workspace
        # Even in the same workspace, bind an unavailable project explicitly.
        context.project = SimpleNamespace(pk=uuid.uuid4())
    else:
        setattr(context, missing_scope, None)
    reader = Mock()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", reader)

    assert get_trace_project(str(uuid.uuid4()), context) is None
    reader.assert_not_called()


@pytest.mark.django_db
def test_dispatch_preserves_default_workspace_legacy_project(project, monkeypatch):
    from ai_tools.tools.tracing.analyze_project_traces import AnalyzeProjectTracesTool

    context = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
    )
    project.workspace = None
    project.save(update_fields=["workspace"])
    trace_id = str(uuid.uuid4())
    install_reader(monkeypatch, project, trace_id)
    dispatch = Mock()
    running = Mock()
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.analyze_traces_on_demand.delay", dispatch
    )
    monkeypatch.setattr("tracer.queries.deep_analysis_state.set_running", running)

    result = AnalyzeProjectTracesTool().run(
        {"project_id": str(project.id), "trace_ids": [trace_id]}, context
    )

    assert not result.is_error, result.content
    dispatch.assert_called_once_with(str(project.id), [trace_id])
    running.assert_called_once_with(trace_id, project_id=str(project.id))
