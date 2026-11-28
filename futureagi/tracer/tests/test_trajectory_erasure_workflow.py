"""TrajectoryErasureWorkflow on the Temporal time-skipping test server.

The activities are stand-ins registered under the real activity names; they
record the order and the server time at which each step ran.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal import trajectory_erasure as erasure
from tfc.temporal.trajectory_erasure import (
    TrajectoryErasureInput,
    TrajectoryErasureWorkflow,
)

STEPS = [
    erasure.ACTIVITY_GATE,
    erasure.ACTIVITY_INVALIDATE,
    erasure.ACTIVITY_CLEAR_SEEN,
    erasure.ACTIVITY_DELETE_ITEMS,
    erasure.ACTIVITY_VERIFY,
    erasure.ACTIVITY_STALLED,
]


def _fake_activities(log, *, gate_failures=0, gate_always_fails=False):
    state = {"gate_calls": 0}

    def make(name):
        @activity.defn(name=name)
        async def fake(payload: dict) -> dict:
            info = activity.info()
            log.append((name, info.current_attempt_scheduled_time, payload["args"]))
            if name == erasure.ACTIVITY_GATE:
                state["gate_calls"] += 1
                if gate_always_fails or state["gate_calls"] <= gate_failures:
                    raise RuntimeError("spans_present")
            if name == erasure.ACTIVITY_DELETE_ITEMS:
                return {"result": {"rows": 3}, "status": "completed"}
            return {"result": {"ok": True}, "status": "completed"}

        return fake

    return [make(name) for name in STEPS], state


async def _run(input, activities, *, expect_failure=False):
    options = {}
    if path := os.environ.get("TEMPORAL_TEST_SERVER"):
        options["test_server_existing_path"] = path
    async with await WorkflowEnvironment.start_time_skipping(**options) as env:
        async with Worker(
            env.client,
            task_queue=erasure.ERASURE_QUEUE,
            workflows=[TrajectoryErasureWorkflow],
            activities=activities,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                TrajectoryErasureWorkflow.run,
                input,
                id=erasure.erasure_workflow_id(input.project_id),
                task_queue=erasure.ERASURE_QUEUE,
            )
            if expect_failure:
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
                return None
            return await handle.result()


async def test_erasure_workflow_step_order():
    log = []
    activities, _ = _fake_activities(log)
    pid = str(uuid.uuid4())
    result = await _run(
        TrajectoryErasureInput(project_id=pid, ingest_wait_seconds=3960), activities
    )
    names = [name for name, _, _ in log]
    assert names == [
        erasure.ACTIVITY_GATE,
        erasure.ACTIVITY_INVALIDATE,
        erasure.ACTIVITY_CLEAR_SEEN,
        erasure.ACTIVITY_DELETE_ITEMS,
        erasure.ACTIVITY_GATE,
        erasure.ACTIVITY_CLEAR_SEEN,
        erasure.ACTIVITY_DELETE_ITEMS,
        erasure.ACTIVITY_VERIFY,
    ]
    assert all(args == [pid] for _, _, args in log)
    at = [when for _, when, _ in log]
    # Seen sets are cleared only after the collectors stop accepting the project.
    assert at[2] - at[1] >= timedelta(seconds=3960)
    # The recheck runs 72 h after the first delete.
    assert at[4] - at[3] >= timedelta(hours=72)
    assert result["first_pass_rows"] == 3 and result["second_pass_rows"] == 3
    assert result["project_id"] == pid


async def test_erasure_workflow_retries_gate_until_spans_are_gone():
    log = []
    activities, state = _fake_activities(log, gate_failures=3)
    await _run(
        TrajectoryErasureInput(project_id=str(uuid.uuid4()), ingest_wait_seconds=60),
        activities,
    )
    # Three failed attempts, one pass, then the recheck gate.
    assert state["gate_calls"] == 5
    assert [n for n, _, _ in log].count(erasure.ACTIVITY_DELETE_ITEMS) == 2
    first_delete = next(
        i for i, (n, _, _) in enumerate(log) if n == erasure.ACTIVITY_DELETE_ITEMS
    )
    assert all(n != erasure.ACTIVITY_DELETE_ITEMS for n, _, _ in log[:4])
    assert first_delete > 4


async def test_erasure_workflow_alerts_when_a_step_never_passes():
    log = []
    activities, _ = _fake_activities(log, gate_always_fails=True)
    pid = str(uuid.uuid4())
    await _run(
        TrajectoryErasureInput(
            project_id=pid, ingest_wait_seconds=60, step_deadline_seconds=4 * 3600
        ),
        activities,
        expect_failure=True,
    )
    names = [n for n, _, _ in log]
    assert erasure.ACTIVITY_DELETE_ITEMS not in names
    assert erasure.ACTIVITY_CLEAR_SEEN not in names
    stalled = [args for n, _, args in log if n == erasure.ACTIVITY_STALLED]
    assert stalled == [[pid, erasure.ACTIVITY_GATE]]


def test_erasure_workflow_and_activities_are_registered_on_tasks_l():
    from temporalio import activity as temporal_activity

    from tfc.temporal.common.registry import (
        get_activities_for_queue,
        get_workflows_for_queue,
    )

    assert TrajectoryErasureWorkflow in get_workflows_for_queue("tasks_l")
    names = {
        temporal_activity._Definition.must_from_callable(fn).name
        for fn in get_activities_for_queue("tasks_l")
    }
    assert set(STEPS) | {"tracer.trajectory_items_gc"} <= names


@pytest.mark.django_db
def test_soft_delete_projects_makes_no_clickhouse_or_trajectory_call(
    organization, workspace, monkeypatch, django_capture_on_commit_callbacks
):
    """Soft delete stays Postgres-only: no synchronous erasure hook returns."""
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project
    from tracer.services import project_deletion
    from tracer.services.clickhouse.v2 import trajectory_lifecycle as lifecycle

    def forbidden(*args, **kwargs):
        raise AssertionError("soft delete reached the trajectory store")

    for name in (
        "open_executor",
        "request_trajectory_erasure",
        "erase_project_items",
        "trajectory_items_gc",
    ):
        monkeypatch.setattr(lifecycle, name, forbidden)
    monkeypatch.setattr("tfc.temporal.common.client.start_workflow_sync", forbidden)
    monkeypatch.setattr(
        project_deletion, "publish_project_invalidation", lambda ids: None
    )

    project = Project.objects.create(
        organization=organization,
        workspace=workspace,
        name=f"soft-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    with django_capture_on_commit_callbacks(execute=True):
        ids = project_deletion.soft_delete_projects(
            Project.objects.filter(pk=project.pk), "observe"
        )
    assert ids == [str(project.pk)]
    assert Project._base_manager.get(pk=project.pk).deleted is True
