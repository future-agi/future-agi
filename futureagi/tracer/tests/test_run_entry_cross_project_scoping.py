"""run_entry scopes its ClickHouse target load by the eval TASK's project, not
by the attached config's. A task may borrow configs authored in a sibling
project (the M2M has no same-project constraint) while its spans / traces /
sessions live in the task's own project: scoping the CH-direct read by the
config's project finds nothing and errors the entry with "not in ClickHouse"."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from model_hub.models.ai_model import AIModel
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.run_entry import run_entry
from tracer.tests._ch_seed import seed_ch_span, seed_ch_trace, seed_ch_trace_sessions


@pytest.fixture
def config_project(db, organization, workspace):
    """The sibling project the borrowed configs are authored in — it never
    holds any of the evaluated targets."""
    return Project.objects.create(
        name="Config Author Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _borrowed_config(config_project, eval_template, mapping):
    return CustomEvalConfig.objects.create(
        name="Borrowed Eval",
        project=config_project,
        eval_template=eval_template,
        config={"threshold": 0.8},
        mapping=mapping,
        filters={},
    )


def _task_with(project, config):
    task = EvalTask.objects.create(
        project=project,
        name="Cross-project Eval Task",
        filters={},
        sampling_rate=1.0,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        spans_limit=100,
    )
    task.evals.add(config)
    return task


def _make_entry(**kwargs):
    """Create the entry the way the materializer does — bulk_create bypasses
    full_clean, so a CH-only target id (no PG row) is allowed."""
    entry = EvalLogger(status=EvalEntryStatus.RUNNING, **kwargs)
    EvalLogger.objects.bulk_create([entry])
    return entry


def _ch_only_span(project, trace):
    """A span that lives ONLY in CH, under the task's project."""
    span = ObservationSpan(
        id=f"xproj-{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id="",
        name="ch-span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=2),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "hi"}]},
        output={"choices": [{"message": {"content": "yo"}}]},
        status="OK",
    )
    seed_ch_span(span)
    return span


def _assert_completed(status, entry):
    entry.refresh_from_db()
    assert status == EvalEntryStatus.COMPLETED, (status, entry.error_message)
    assert entry.status == EvalEntryStatus.COMPLETED
    assert not entry.error


@pytest.mark.integration
@pytest.mark.django_db
class TestRunEntryCrossProjectScoping:
    def test_span_target_completes_when_config_lives_in_sibling_project(
        self, project, config_project, eval_template, stub_run_eval, stub_cost_log
    ):
        config = _borrowed_config(
            config_project, eval_template, {"input": "input", "output": "output"}
        )
        task = _task_with(project, config)
        trace = Trace.objects.create(project=project, name="t")
        span = _ch_only_span(project, trace)
        entry = _make_entry(
            target_type=EvalTargetType.SPAN,
            observation_span_id=span.id,
            trace=trace,
            custom_eval_config=config,
            eval_task_id=str(task.id),
        )
        _assert_completed(run_entry(entry), entry)

    def test_trace_target_completes_when_config_lives_in_sibling_project(
        self, project, config_project, eval_template, stub_run_eval, stub_cost_log
    ):
        config = _borrowed_config(
            config_project, eval_template, {"input": "input", "output": "output"}
        )
        task = _task_with(project, config)
        trace = Trace(
            id=uuid.uuid4(),
            project=project,
            name="t",
            input={"q": "hello"},
            output={"a": "world"},
        )
        seed_ch_trace(trace)
        root = _ch_only_span(project, trace)
        entry = _make_entry(
            target_type=EvalTargetType.TRACE,
            observation_span_id=root.id,
            trace_id=str(trace.id),
            custom_eval_config=config,
            eval_task_id=str(task.id),
        )
        _assert_completed(run_entry(entry), entry)

    def test_session_target_completes_when_config_lives_in_sibling_project(
        self,
        observe_project,
        config_project,
        eval_template,
        stub_run_eval,
        stub_cost_log,
    ):
        # "name" is a session field; "input" is not.
        config = _borrowed_config(config_project, eval_template, {"input": "name"})
        task = _task_with(observe_project, config)
        session = TraceSession.objects.create(project=observe_project, name="sess")
        seed_ch_trace_sessions([session])
        entry = _make_entry(
            target_type=EvalTargetType.SESSION,
            trace_session=session,
            custom_eval_config=config,
            eval_task_id=str(task.id),
        )
        _assert_completed(run_entry(entry), entry)
