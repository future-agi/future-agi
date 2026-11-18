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
        lambda _task, _queue, **_kwargs: (workflow_class, workflow_input),
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
        lambda _task, _queue, **_kwargs: (SimpleNamespace(run=object()), object()),
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
        lambda _task, _queue, **_kwargs: (workflow_class, workflow_input),
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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("described", "expected"),
    [
        ("absent", True),
        ("closed", True),
        ("progressing", False),
    ],
)
def test_the_start_carries_what_the_describe_said_into_the_reap(
    monkeypatch, described, expected
):
    """The first thing a fresh execution does is reap, and the reap applies a
    ninety-minute floor unless something can show it is not racing a live
    dispatcher. Nothing inside the workflow can show that — a describe taken
    from within finds the execution asking — so the starter takes it, in the
    moment between the old execution ending and the new one beginning, and
    carries the answer in the workflow input."""
    from tfc.temporal.eval_tasks import client
    from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
    from tracer.models.eval_task import RunType

    monkeypatch.setattr(
        client, "describe_eval_task_workflow_sync", lambda _task_id: described
    )
    captured = {}

    def _start(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_sync", _start)

    client.start_eval_task_workflow_sync(
        SimpleNamespace(id="task-id", run_type=RunType.HISTORICAL),
        replace_existing=True,
    )

    workflow_input = captured["workflow_input"]
    assert isinstance(workflow_input, EvalTaskWorkflowInput)
    assert workflow_input.workflow_confirmed_stopped is expected


@pytest.mark.unit
def test_a_describe_that_cannot_answer_leaves_the_floor_in_place(monkeypatch):
    """No answer is not a negative answer. An unreachable Temporal must not
    read as "nothing is draining" — that is the one reading that lets a reap
    retire a claim a live execution still owns."""
    from tfc.temporal.eval_tasks import client
    from tracer.models.eval_task import RunType

    def _unreachable(_task_id):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", _unreachable)
    captured = {}

    def _start(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_sync", _start)

    client.start_eval_task_workflow_sync(
        SimpleNamespace(id="task-id", run_type=RunType.HISTORICAL)
    )

    assert captured["workflow_input"].workflow_confirmed_stopped is False


@pytest.mark.unit
def test_a_describe_fallback_is_logged_with_its_cause(monkeypatch):
    """The fallback is conservative but not silent: it changes what the run
    reclaims, and the start after it can still succeed, so the event has to
    carry the failure itself — its type and traceback."""
    import structlog

    from tfc.temporal.eval_tasks import client

    failure = RuntimeError("temporal unreachable")

    def _unreachable(_task_id):
        raise failure

    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", _unreachable)

    with structlog.testing.capture_logs() as records:
        assert client._describe_says_nothing_is_draining("task-id") is False

    [line] = [r for r in records if r["event"] == "eval_task_start_describe_failed"]
    assert line["log_level"] == "warning"
    assert line["task_id"] == "task-id"
    assert line["error_type"] == "RuntimeError"
    assert line["exc_info"] is failure
    assert failure.__traceback__ is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_async_describe_fallback_is_logged_with_its_cause(monkeypatch):
    import structlog

    from tfc.temporal.eval_tasks import client

    failure = RuntimeError("temporal unreachable")

    async def _unreachable(_task_id):
        raise failure

    monkeypatch.setattr(client, "describe_eval_task_workflow_async", _unreachable)

    with structlog.testing.capture_logs() as records:
        assert await client._describe_says_nothing_is_draining_async("t") is False

    [line] = [r for r in records if r["event"] == "eval_task_start_describe_failed"]
    assert line["exc_info"] is failure
    assert line["error_type"] == "RuntimeError"


@pytest.mark.unit
def test_a_start_handed_the_describes_answer_does_not_describe_again(monkeypatch):
    """The sweep's restart is gated on a describe it has just taken. Asking
    again costs a second RPC, and a second one that failed would put the run
    on the blind floor after the sweep had admitted it on 600 s. Recorded
    rather than raised: the helper absorbs any exception from the describe."""
    from tfc.temporal.eval_tasks import client
    from tracer.models.eval_task import RunType

    described = []
    monkeypatch.setattr(
        client,
        "describe_eval_task_workflow_sync",
        lambda task_id: described.append(task_id) or client.WF_PROGRESSING,
    )
    captured = {}

    def _start(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_sync", _start)

    client.start_eval_task_workflow_sync(
        SimpleNamespace(id="task-id", run_type=RunType.HISTORICAL),
        workflow_confirmed_stopped=True,
    )

    assert described == []
    assert captured["workflow_input"].workflow_confirmed_stopped is True
    assert captured["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING


@pytest.mark.unit
def test_a_replacing_start_cannot_be_handed_an_answer(monkeypatch):
    """A replacing start terminates whatever owns the id, so whether that was
    a live drain is what its own describe, taken just before, has to say. An
    answer taken earlier can predate a start that the terminate then kills."""
    from tfc.temporal.eval_tasks import client

    described = []
    started = []
    monkeypatch.setattr(
        client,
        "describe_eval_task_workflow_sync",
        lambda task_id: described.append(task_id) or client.WF_CLOSED,
    )
    monkeypatch.setattr(client, "start_workflow_sync", started.append)

    with pytest.raises(ValueError, match="coalescing starts only"):
        client.start_eval_task_workflow_sync(
            SimpleNamespace(id="task-id"),
            replace_existing=True,
            workflow_confirmed_stopped=True,
        )

    assert described == []
    assert started == []


@pytest.mark.unit
@pytest.mark.parametrize("existing_state", ["absent", "running", "closed"])
def test_initial_start_retry_never_creates_a_second_execution(
    monkeypatch, existing_state
):
    """Model the server's policy arbitration after a stale NOT_FOUND describe.

    This is a transport double, not a real Temporal concurrency test.
    """
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from tfc.temporal.eval_tasks import client

    monkeypatch.setattr(
        client, "describe_eval_task_workflow_sync", lambda _task_id: client.WF_ABSENT
    )
    executions = [] if existing_state == "absent" else [existing_state]

    def start(**kwargs):
        assert kwargs["workflow_id"] == "eval-task-task-id"
        assert kwargs["workflow_input"].start_once is True
        assert kwargs["cancel_existing"] is False
        assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
        assert kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
        if executions == ["closed"]:
            raise WorkflowAlreadyStartedError(
                kwargs["workflow_id"], "HistoricalEvalTaskWorkflow"
            )
        if not executions:
            executions.append("running")
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(client, "start_workflow_sync", start)
    task = SimpleNamespace(id="task-id", run_type="historical")
    if existing_state == "closed":
        with pytest.raises(WorkflowAlreadyStartedError):
            client.start_eval_task_workflow_sync(task, start_once=True)
        assert executions == ["closed"]
    else:
        for _ in range(2):
            started = client.start_eval_task_workflow_sync(task, start_once=True)
            assert started == "eval-task-task-id"
        assert executions == ["running"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_start_probe_distinguishes_success_from_other_closed_runs(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from temporalio.client import WorkflowExecutionStatus

    from tfc.temporal.eval_tasks import client

    server = Mock()
    describe = AsyncMock()
    server.get_workflow_handle.return_value.describe = describe
    monkeypatch.setattr(
        "tfc.temporal.common.client.get_client", AsyncMock(return_value=server)
    )
    for status, expected in (
        (WorkflowExecutionStatus.COMPLETED, client.WF_COMPLETED),
        (WorkflowExecutionStatus.FAILED, client.WF_CLOSED),
    ):
        describe.return_value = SimpleNamespace(status=status)
        probe = client.describe_eval_task_workflow_async
        assert await probe("task") == client.WF_CLOSED
        assert await probe("task", distinguish_completion=True) == expected


@pytest.mark.unit
def test_initial_start_retry_cannot_replace_an_execution(monkeypatch):
    from unittest.mock import Mock

    from tfc.temporal.eval_tasks import client

    start = Mock()
    describe = Mock()
    monkeypatch.setattr(client, "start_workflow_sync", start)
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)

    with pytest.raises(ValueError, match="cannot replace"):
        client.start_eval_task_workflow_sync(
            SimpleNamespace(id="task-id"), start_once=True, replace_existing=True
        )

    start.assert_not_called()
    describe.assert_not_called()
