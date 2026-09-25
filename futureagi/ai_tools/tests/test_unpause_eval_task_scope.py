"""The AI tool's Resume reaches exactly the tasks the unpause endpoint does.

Both resolve the task id through ``tracer.selectors.eval_tasks.scope``. The
tool used to filter by organization alone, so it resumed — and paid for the
evaluations of — a task in another workspace of the same organization, or in a
deleted project, both of which the endpoint refuses. These run on the real
database: the default workspace's scope reaches the project's workspace through
an outer join, which PostgreSQL will not lock without ``of=("self",)``.
"""

import pytest

from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_project
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType


@pytest.fixture
def starts(monkeypatch):
    """Record workflow starts instead of reaching Temporal."""
    recorded = []
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda task, **kwargs: recorded.append((str(task.id), kwargs)),
    )
    return recorded


@pytest.fixture
def other_workspace(tool_context):
    return Workspace.objects.create(
        name="Other Workspace",
        organization=tool_context.organization,
        is_default=False,
        is_active=True,
        created_by=tool_context.user,
    )


def _paused_task(project):
    return EvalTask.objects.create(
        project=project,
        name="Paused Task",
        filters={},
        sampling_rate=1.0,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PAUSED,
        spans_limit=100,
    )


def _resume(task, context):
    return run_tool("unpause_eval_task", {"eval_task_id": str(task.id)}, context)


def _assert_refused(result, task, starts):
    assert result.is_error
    assert result.error_code == "NOT_FOUND"
    task.refresh_from_db()
    assert task.status == EvalTaskStatus.PAUSED
    assert starts == []


class TestUnpauseEvalTaskScope:
    def test_a_task_in_the_callers_workspace_is_resumed(
        self, tool_context, starts, django_capture_on_commit_callbacks
    ):
        """The control, and the lock: the caller's workspace is the default
        one, so this is the outer-join scope under ``select_for_update``."""
        task = _paused_task(make_project(tool_context))

        with django_capture_on_commit_callbacks(execute=True):
            result = _resume(task, tool_context)

        assert not result.is_error, result.content
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.PENDING
        assert starts == [(str(task.id), {"replace_existing": True})]

    def test_a_task_in_another_workspace_is_refused(
        self, tool_context, other_workspace, starts
    ):
        task = _paused_task(make_project(tool_context, workspace=other_workspace))

        _assert_refused(_resume(task, tool_context), task, starts)

    def test_a_caller_in_another_workspace_cannot_reach_the_default_one(
        self, tool_context, other_workspace, starts
    ):
        task = _paused_task(make_project(tool_context))
        elsewhere = ToolContext(
            user=tool_context.user,
            organization=tool_context.organization,
            workspace=other_workspace,
        )

        _assert_refused(_resume(task, elsewhere), task, starts)

    def test_a_task_whose_project_is_deleted_is_refused(self, tool_context, starts):
        task = _paused_task(make_project(tool_context, deleted=True))

        _assert_refused(_resume(task, tool_context), task, starts)

    def test_a_failed_task_is_scoped_like_a_paused_one(
        self, tool_context, other_workspace, starts
    ):
        """FAILED became resumable through the tool in round 7, which is what
        made the organization-only lookup worth closing."""
        task = _paused_task(make_project(tool_context, workspace=other_workspace))
        EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.FAILED)

        result = _resume(task, tool_context)

        assert result.error_code == "NOT_FOUND"
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.FAILED
        assert starts == []

    def test_no_workspace_narrows_as_the_endpoint_does_without_one(
        self, tool_context, other_workspace, starts, django_capture_on_commit_callbacks
    ):
        """A context without a workspace gets the endpoint's own no-workspace
        scope — organization and live project — not something wider."""
        task = _paused_task(make_project(tool_context, workspace=other_workspace))
        gone = _paused_task(make_project(tool_context, deleted=True))
        unscoped = ToolContext(
            user=tool_context.user,
            organization=tool_context.organization,
            workspace=None,
        )

        _assert_refused(_resume(gone, unscoped), gone, starts)
        with django_capture_on_commit_callbacks(execute=True):
            result = _resume(task, unscoped)

        assert not result.is_error, result.content
        assert starts == [(str(task.id), {"replace_existing": True})]
