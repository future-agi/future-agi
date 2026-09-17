from types import SimpleNamespace

import pytest
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy


@pytest.mark.unit
def test_ensure_active_start_coalesces_with_an_active_workflow(monkeypatch):
    from tfc.temporal.eval_tasks import client

    workflow_class = SimpleNamespace(run=object())
    workflow_input = object()
    captured = {}

    monkeypatch.setattr(
        client,
        "_select",
        lambda _task, _queue: (workflow_class, workflow_input),
    )

    def _start(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_sync", _start)

    task = SimpleNamespace(id="task-id")
    workflow_id = client.start_eval_task_workflow_sync(task)

    assert workflow_id == "eval-task-task-id"
    assert captured["cancel_existing"] is False
    assert captured["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    assert captured["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING


@pytest.mark.unit
def test_committed_rerun_replaces_an_old_or_closing_workflow(monkeypatch):
    from tfc.temporal.eval_tasks import client

    captured = {}
    monkeypatch.setattr(
        client,
        "_select",
        lambda _task, _queue: (SimpleNamespace(run=object()), object()),
    )

    def _start(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_sync", _start)

    client.start_eval_task_workflow_sync(
        SimpleNamespace(id="task-id"), replace_existing=True
    )

    assert captured["cancel_existing"] is False
    assert captured["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    assert captured["id_conflict_policy"] == WorkflowIDConflictPolicy.TERMINATE_EXISTING


@pytest.mark.unit
@pytest.mark.asyncio
async def test_common_start_forwards_workflow_id_conflict_policy(monkeypatch):
    from tfc.temporal.common import client

    captured = {}

    class _TemporalClient:
        async def start_workflow(self, *_args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id=kwargs["id"])

    async def _get_client():
        return _TemporalClient()

    monkeypatch.setattr(client, "get_client", _get_client)

    await client.start_workflow_async(
        workflow_class=SimpleNamespace(run=object()),
        workflow_input=object(),
        workflow_id="eval-task-task-id",
        task_queue="tasks_s",
        cancel_existing=False,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )

    assert captured["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    assert captured["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_committed_rerun_replaces_an_active_workflow(monkeypatch):
    from tfc.temporal.eval_tasks import client

    workflow_class = SimpleNamespace(run=object())
    workflow_input = object()
    captured = {}

    monkeypatch.setattr(
        client,
        "_select",
        lambda _task, _queue: (workflow_class, workflow_input),
    )

    async def _start(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_async", _start)

    workflow_id = await client.start_eval_task_workflow_async(
        SimpleNamespace(id="task-id"), replace_existing=True
    )

    assert workflow_id == "eval-task-task-id"
    assert captured["cancel_existing"] is False
    assert captured["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    assert captured["id_conflict_policy"] == WorkflowIDConflictPolicy.TERMINATE_EXISTING
