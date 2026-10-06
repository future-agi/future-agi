"""Pure orchestration contracts for AI eval-task cutover paths."""

from types import SimpleNamespace

import pytest


class _Atomic:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.mark.unit
def test_ai_create_commits_complete_task_before_ensure_active(monkeypatch):
    from ai_tools.base import ToolContext
    from ai_tools.tools.tracing.create_eval_task import (
        CreateEvalTaskInput,
        CreateEvalTaskTool,
    )
    from tracer.models.eval_task import EvalTaskStatus

    project = SimpleNamespace(id="project-id", name="Project")
    eval_id = "00000000-0000-0000-0000-000000000002"
    eval_config = SimpleNamespace(id=eval_id, name="Eval")
    task = SimpleNamespace(
        id="task-id",
        name="Task",
        project=project,
        status=EvalTaskStatus.PENDING,
        created_at=None,
        evals=SimpleNamespace(set=lambda configs: None),
    )
    starts = []
    callbacks = []

    monkeypatch.setattr(
        "tracer.models.project.Project.objects.get", lambda **_kwargs: project
    )
    monkeypatch.setattr(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects.filter",
        lambda **_kwargs: [eval_config],
    )
    monkeypatch.setattr(
        "tracer.models.eval_task.EvalTask.objects.create", lambda **_kwargs: task
    )
    monkeypatch.setattr(
        "tracer.models.eval_task.EvalTaskLogger.objects.create",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr("django.db.transaction.atomic", lambda: _Atomic())
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda callback: callbacks.append(callback)
    )
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda value, **kwargs: starts.append((value, kwargs)),
    )

    result = CreateEvalTaskTool().execute(
        CreateEvalTaskInput(
            project_id="00000000-0000-0000-0000-000000000001",
            name="Task",
            eval_config_ids=[eval_id],
        ),
        ToolContext(user=object(), organization=object(), workspace=object()),
    )

    assert not result.is_error
    assert starts == []
    assert len(callbacks) == 1
    callbacks[0]()
    assert starts == [(task, {})]


@pytest.mark.unit
def test_ai_unpause_preserves_filters_and_replaces_closing_workflow(monkeypatch):
    from ai_tools.base import ToolContext
    from ai_tools.tools.tracing.unpause_eval_task import (
        UnpauseEvalTaskInput,
        UnpauseEvalTaskTool,
    )
    from tracer.models.eval_task import EvalTaskStatus

    original_filters = {"created_at": "original", "project_id": "project-id"}
    task = SimpleNamespace(
        id="task-id",
        name="Task",
        status=EvalTaskStatus.PAUSED,
        filters=original_filters.copy(),
        save=lambda **_kwargs: None,
    )
    logger = SimpleNamespace(offset=17, save=lambda: None)
    callbacks = []
    starts = []
    queryset = SimpleNamespace(get=lambda **_kwargs: task)

    monkeypatch.setattr(
        "tracer.selectors.eval_tasks.scope.eval_tasks_in_scope",
        lambda _queryset, **_scope: SimpleNamespace(
            select_for_update=lambda **_lock: queryset
        ),
    )
    monkeypatch.setattr(
        "tracer.models.eval_task.EvalTaskLogger.objects.get", lambda **_kwargs: logger
    )
    monkeypatch.setattr("django.db.transaction.atomic", lambda: _Atomic())
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda callback: callbacks.append(callback)
    )
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda value, **kwargs: starts.append((value, kwargs)),
    )

    result = UnpauseEvalTaskTool().execute(
        UnpauseEvalTaskInput(eval_task_id="00000000-0000-0000-0000-000000000001"),
        ToolContext(user=object(), organization=object(), workspace=object()),
    )

    assert not result.is_error
    assert task.status == EvalTaskStatus.PENDING
    assert task.filters == original_filters
    assert logger.offset == 0
    assert starts == []
    assert len(callbacks) == 1
    callbacks[0]()
    assert starts == [(task, {"replace_existing": True})]


def _unpause_through_the_tool(monkeypatch, status):
    """Run the AI tool's Resume on a task in ``status`` with the ORM, the
    transaction and the workflow start doubled; commit callbacks run at once."""
    from ai_tools.base import ToolContext
    from ai_tools.tools.tracing.unpause_eval_task import (
        UnpauseEvalTaskInput,
        UnpauseEvalTaskTool,
    )

    task = SimpleNamespace(
        id="task-id", name="Task", status=status, save=lambda **_kwargs: None
    )
    starts = []
    monkeypatch.setattr(
        "tracer.selectors.eval_tasks.scope.eval_tasks_in_scope",
        lambda _queryset, **_scope: SimpleNamespace(
            select_for_update=lambda **_lock: SimpleNamespace(
                get=lambda **_kwargs: task
            )
        ),
    )
    monkeypatch.setattr(
        "tracer.models.eval_task.EvalTaskLogger.objects.get",
        lambda **_kwargs: SimpleNamespace(offset=0, save=lambda: None),
    )
    monkeypatch.setattr("django.db.transaction.atomic", lambda: _Atomic())
    monkeypatch.setattr("django.db.transaction.on_commit", lambda callback: callback())
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda value, **kwargs: starts.append((value, kwargs)),
    )

    result = UnpauseEvalTaskTool().execute(
        UnpauseEvalTaskInput(eval_task_id="00000000-0000-0000-0000-000000000001"),
        ToolContext(user=object(), organization=object(), workspace=object()),
    )
    return result, task, starts


@pytest.mark.unit
def test_ai_unpause_recovers_a_failed_task(monkeypatch):
    """The unpause endpoint and all three task UIs resume a failed task; the AI
    tool refused it, so the same Resume answered differently by surface."""
    from tracer.models.eval_task import EvalTaskStatus

    result, task, starts = _unpause_through_the_tool(monkeypatch, EvalTaskStatus.FAILED)

    assert not result.is_error, result.content
    assert task.status == EvalTaskStatus.PENDING
    assert starts == [(task, {"replace_existing": True})]
    assert "Previous Status:** FAILED" in result.content


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", ["pending", "running", "completed", "failed", "paused", "deleted"]
)
def test_ai_unpause_accepts_exactly_what_the_endpoint_resumes(monkeypatch, status):
    """One status set, shared with ``unpause_eval_task`` in the eval-task view:
    a task the endpoint would resume the tool resumes, and anything else it
    refuses without writing or starting anything."""
    from tracer.models.eval_task import RESUMABLE_TASK_STATUSES, EvalTaskStatus

    assert set(EvalTaskStatus.values) == {
        "pending",
        "running",
        "completed",
        "failed",
        "paused",
        "deleted",
    }
    assert RESUMABLE_TASK_STATUSES == {EvalTaskStatus.PAUSED, EvalTaskStatus.FAILED}

    result, task, starts = _unpause_through_the_tool(monkeypatch, status)

    if status in RESUMABLE_TASK_STATUSES:
        assert not result.is_error, result.content
        assert task.status == EvalTaskStatus.PENDING
        assert starts == [(task, {"replace_existing": True})]
    else:
        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"
        assert "Only paused or failed tasks can be resumed." in result.content
        assert task.status == status
        assert starts == []
