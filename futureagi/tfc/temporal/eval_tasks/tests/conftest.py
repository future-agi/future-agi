"""Fixtures for eval-task Temporal tests.

The root ``conftest.py`` supplies ``organization`` / ``user`` / ``workspace``;
this adds the tracer-side objects (project, eval config, task, entries) plus the
in-memory Temporal server used by the workflow e2e tests.
"""

import uuid

import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment

from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.models.trace import Trace


@pytest.fixture
def project(db, organization, workspace):
    return Project.objects.create(
        name="WF Test Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment",
        config=[
            {"id": "input", "name": "Input", "is_visible": True},
            {"id": "output", "name": "Output", "is_visible": True},
        ],
    )


@pytest.fixture
def eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="WF Eval Template",
        description="t",
        organization=organization,
        workspace=workspace,
        config={"type": "pass_fail", "criteria": "c"},
    )


@pytest.fixture
def custom_eval_config(db, project, eval_template):
    return CustomEvalConfig.objects.create(
        name="WF Eval",
        project=project,
        eval_template=eval_template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
    )


@pytest.fixture
def eval_task(db, project, custom_eval_config):
    task = EvalTask.objects.create(
        project=project,
        name="WF Task",
        filters={},
        sampling_rate=100.0,
        spans_limit=1_000_000,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
    )
    task.evals.add(custom_eval_config)
    return task


@pytest.fixture
def make_pending_entries(db, custom_eval_config):
    """Create N span entries for a task directly in PG (bypassing CH/materialize)."""

    def _make(task, n, *, status=EvalEntryStatus.PENDING):
        out = []
        for i in range(n):
            trace = Trace.objects.create(project=task.project, name=f"wf-t-{i}")
            span = ObservationSpan.objects.create(
                id=f"wf-s-{i}-{uuid.uuid4().hex[:8]}",
                project=task.project,
                trace=trace,
                name="s",
                observation_type="llm",
            )
            out.append(
                EvalLogger.objects.create(
                    target_type=EvalTargetType.SPAN,
                    observation_span=span,
                    trace=trace,
                    custom_eval_config=custom_eval_config,
                    eval_task_id=str(task.id),
                    status=status,
                )
            )
        return out

    return _make


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def workflow_environment():
    """In-memory Temporal server for the whole test session."""
    env = await WorkflowEnvironment.start_local()
    yield env
    await env.shutdown()
