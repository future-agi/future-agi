"""Falcon's Resume reaches exactly the tasks the unpause endpoint does.

Originally written against the hand-written ``unpause_eval_task`` tool, which
filtered by organization alone and so resumed — and paid for the evaluations of
— a task in another workspace of the same organization, or in a deleted
project, both of which the endpoint refuses. That tool is gone: Resume is now
the generated ``resume_eval_task``, which POSTs to
``/tracer/eval-task/unpause_eval_task/`` and therefore inherits the endpoint's
scope instead of re-deriving it. These cases are kept because the scope is what
matters, not which layer enforces it, and they now pin it end to end.

Two differences from the retired tool, both inherited from the endpoint and
both pinned below:

* Out-of-scope is refused as ``VALIDATION_ERROR``, not ``NOT_FOUND`` — the
  endpoint answers an unreachable id with 400, which the catalog maps per
  ``ai_tools.generated._STATUS_TO_ERROR_CODE``. The old tool raised its own 404.
* A context with no workspace resolves to the caller's *default* workspace
  rather than the whole organization, so it is narrower than the old tool.

These run on the real database.
"""

import pytest

from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_project
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType


@pytest.fixture
def starts(monkeypatch):
    """Record workflow starts instead of reaching Temporal.

    Patched on the view module, not on ``tfc.temporal.eval_tasks.client``: the
    view binds the name at import time, so patching the client package would
    leave the endpoint calling the real thing.
    """
    recorded = []
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
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
    return run_tool("resume_eval_task", {"eval_task_id": str(task.id)}, context)


def _assert_refused(result, task, starts):
    assert result.is_error
    assert result.error_code == "VALIDATION_ERROR"
    task.refresh_from_db()
    assert task.status == EvalTaskStatus.PAUSED
    assert starts == []


class TestResumeEvalTaskScope:
    def test_a_task_in_the_callers_workspace_is_resumed(self, tool_context, starts):
        """The control: the caller's workspace is the default one."""
        task = _paused_task(make_project(tool_context))

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
        """FAILED is resumable, which is what made the organization-only
        lookup worth closing: being resumable must not widen the scope."""
        task = _paused_task(make_project(tool_context, workspace=other_workspace))
        EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.FAILED)

        result = _resume(task, tool_context)

        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.FAILED
        assert starts == []

    def test_a_running_task_is_refused_as_unresumable(self, tool_context, starts):
        """In scope but not in ``RESUMABLE_TASK_STATUSES`` — refused without
        writing or starting anything, so scope and status stay separable."""
        task = _paused_task(make_project(tool_context))
        EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.RUNNING)

        result = _resume(task, tool_context)

        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.RUNNING
        assert starts == []

    def test_no_workspace_falls_back_to_the_default_workspace_scope(
        self, tool_context, other_workspace, starts
    ):
        """A context without a workspace is not org-wide. The executor sends an
        empty ``X-WORKSPACE-ID`` and the endpoint resolves the caller's default
        workspace, so a no-workspace context reaches exactly that workspace.

        This is narrower than the retired ``unpause_eval_task`` tool, which fell
        back to the organization and so could resume a task in any workspace —
        the widening this scope work set out to close. Asserted here so the
        narrowing is a pinned contract rather than an accident of the cutover.
        """
        here = _paused_task(make_project(tool_context))
        elsewhere = _paused_task(
            make_project(tool_context, workspace=other_workspace)
        )
        gone = _paused_task(make_project(tool_context, deleted=True))
        unscoped = ToolContext(
            user=tool_context.user,
            organization=tool_context.organization,
            workspace=None,
        )

        _assert_refused(_resume(gone, unscoped), gone, starts)
        _assert_refused(_resume(elsewhere, unscoped), elsewhere, starts)

        result = _resume(here, unscoped)

        assert not result.is_error, result.content
        here.refresh_from_db()
        assert here.status == EvalTaskStatus.PENDING
        assert starts == [(str(here.id), {"replace_existing": True})]
