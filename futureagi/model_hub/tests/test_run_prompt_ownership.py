"""Regression coverage for prompt handoffs, fenced writes and recovery races."""

import threading
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.utils import timezone
from redis.exceptions import RedisError

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import RunPrompter
from model_hub.services.run_prompt_ownership import (
    OwnershipLostError,
    PromptSupersededError,
    guard_prompt_write,
    lock_prompt,
    queue_prompt_rows,
)
from model_hub.tasks import run_prompt as tasks
from model_hub.views.run_prompt import RunPrompts, run_all_prompts_task
from tfc.utils.distributed_state import DistributedEvaluationTracker

pytestmark = pytest.mark.django_db


@pytest.fixture
def prompt_data(organization, workspace):
    dataset = Dataset.objects.create(
        name="Ownership",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    prompt = RunPrompter.objects.create(
        name="Output",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.RUNNING.value,
        model="gpt-4",
        messages=[{"role": "user", "content": "hi"}],
    )
    column = Column.objects.create(
        name="Output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=str(prompt.id),
    )
    rows = [Row.objects.create(dataset=dataset, order=i) for i in range(3)]
    return SimpleNamespace(prompt=prompt, dataset=dataset, column=column, rows=rows)


@pytest.fixture
def tracker(monkeypatch):
    tracker = DistributedEvaluationTracker(default_ttl=300)
    tracker.key_prefix = f"test_prompt_ownership:{uuid4()}:"
    assert tracker.is_reachable(), (
        "These regression tests require the isolated test Redis"
    )
    monkeypatch.setattr(tasks, "run_prompt_tracker", tracker)
    monkeypatch.setattr(tasks, "close_old_connections", lambda: None)
    with patch("model_hub.views.run_prompt.close_old_connections"):
        yield tracker
    keys = list(tracker._redis_client.scan_iter(f"{tracker.key_prefix}*"))
    if keys:
        tracker._redis_client.delete(*keys)


def runner_for(data, token):
    runner = RunPrompts(
        str(data.prompt.id),
        run_token=token,
        ownership_guard=lambda revision: guard_prompt_write(
            str(data.prompt.id),
            token,
            tasks._get_fresh_lease,
            updated_at=revision,
        ),
    )
    runner.run_prompt_model = data.prompt
    return runner


def save_cell(data, row, status=CellStatus.RUNNING.value):
    return Cell.objects.create(
        dataset=data.dataset,
        column=data.column,
        row=row,
        status=status,
        value="original",
    )


def backdate(data):
    old = timezone.now() - timedelta(hours=2)
    RunPrompter.objects.filter(id=data.prompt.id).update(updated_at=old)
    Cell.objects.filter(column=data.column).update(updated_at=old)
    data.prompt.refresh_from_db()


def test_stale_worker_cannot_overwrite_successor_without_local_fence(
    prompt_data, tracker
):
    data = prompt_data
    token = tasks._claim_prompt(str(data.prompt.id), {})
    runner = runner_for(data, token)
    tracker.mark_completed(data.prompt.id, run_token=token)
    successor = tasks._claim_prompt(str(data.prompt.id), {})
    cell = save_cell(data, data.rows[0], CellStatus.PASS.value)
    with pytest.raises(OwnershipLostError):
        runner._save_result(
            data.rows[0], data.column, "stale", {}, CellStatus.PASS.value
        )
    cell.refresh_from_db()
    assert cell.value == "original"
    assert tracker.get_running_info(data.prompt.id).metadata["run_token"] == successor


def test_retry_reuses_cell_and_skips_completed_current_revision(prompt_data, tracker):
    data = prompt_data
    token = tasks._claim_prompt(str(data.prompt.id), {})
    runner = runner_for(data, token)
    runner._save_result(data.rows[0], data.column, "answer", {}, CellStatus.PASS.value)
    runner._save_result(data.rows[0], data.column, "answer", {}, CellStatus.PASS.value)
    assert Cell.objects.filter(row=data.rows[0], column=data.column).count() == 1
    assert runner._cell_was_processed(data.rows[0], data.column)
    with (
        patch.object(runner, "_save_result") as write,
        patch("model_hub.views.run_prompt.RunPrompt") as llm,
    ):
        runner.process_row(data.rows[0], data.column)
    llm.assert_not_called()
    write.assert_not_called()
    data.prompt.save()
    assert not runner._cell_was_processed(data.rows[0], data.column)


def test_stale_edit_does_not_cancel_new_owner(prompt_data, tracker):
    data = prompt_data
    old_revision = data.prompt.updated_at.isoformat()
    data.prompt.save()
    token = tasks._claim_prompt(str(data.prompt.id), {})
    tasks.process_editing_prompt(str(data.prompt.id), revision=old_revision)
    assert tracker.get_cancel_request(data.prompt.id, token) is None
    assert tracker.get_running_info(data.prompt.id).metadata["run_token"] == token


def test_new_revision_rejects_old_write_even_before_replacement_claims(
    prompt_data, tracker
):
    data = prompt_data
    token = tasks._claim_prompt(str(data.prompt.id), {})
    runner = runner_for(data, token)
    queue_prompt_rows([str(data.prompt.id)], [str(data.rows[0].id)])
    with pytest.raises(PromptSupersededError):
        runner._save_result(data.rows[0], data.column, "old", {}, CellStatus.PASS.value)
    assert not Cell.objects.filter(column=data.column).exists()


def test_selected_rerun_of_completed_prompt_does_not_expand_to_new_rows(prompt_data):
    data = prompt_data
    data.prompt.status = StatusType.COMPLETED.value
    data.prompt.save()
    save_cell(data, data.rows[0], CellStatus.PASS.value)
    queue_prompt_rows([str(data.prompt.id)], [str(data.rows[0].id)])
    data.prompt.refresh_from_db()
    assert data.prompt.queued_row_ids == [str(data.rows[0].id)]


def test_subset_handoff_preserves_unfinished_full_run_without_repeating_finished_rows(
    prompt_data,
):
    data = prompt_data
    save_cell(data, data.rows[0], CellStatus.PASS.value)
    save_cell(data, data.rows[1])
    queue_prompt_rows([str(data.prompt.id)], [str(data.rows[1].id)])
    data.prompt.refresh_from_db()
    assert set(data.prompt.queued_row_ids) == {str(row.id) for row in data.rows[1:]}


def test_two_queued_selections_merge_only_unfinished_work(prompt_data):
    data = prompt_data
    data.prompt.status = StatusType.COMPLETED.value
    data.prompt.save()
    queue_prompt_rows([str(data.prompt.id)], [str(data.rows[0].id)])
    queue_prompt_rows([str(data.prompt.id)], [str(data.rows[1].id)])
    data.prompt.refresh_from_db()
    assert set(data.prompt.queued_row_ids) == {str(row.id) for row in data.rows[:2]}
    save_cell(data, data.rows[0], CellStatus.PASS.value)
    queue_prompt_rows([str(data.prompt.id)], [str(data.rows[2].id)])
    data.prompt.refresh_from_db()
    assert set(data.prompt.queued_row_ids) == {str(row.id) for row in data.rows[1:]}


def test_selected_retry_does_not_reset_completed_cells(prompt_data):
    data = prompt_data
    ids, rows = [str(data.prompt.id)], [str(data.rows[0].id)]
    revisions = queue_prompt_rows(ids, rows)
    cell = save_cell(data, data.rows[0], CellStatus.PASS.value)
    with patch.object(tasks, "process_editing_prompt") as process:
        run_all_prompts_task._original_func(ids, rows, revisions=revisions)
        run_all_prompts_task._original_func(ids, rows, revisions=revisions)
    assert process.call_count == 2
    cell.refresh_from_db()
    assert cell.status == CellStatus.PASS.value
    assert cell.value == "original"


@pytest.mark.parametrize("redis_failure", [True, False])
def test_recovery_cannot_fail_an_unreadable_or_locked_owner(
    prompt_data, tracker, redis_failure
):
    data = prompt_data
    cell = save_cell(data, data.rows[0])
    backdate(data)
    if redis_failure:
        # PING works, but the actual lease read fails.
        with patch.object(
            tracker, "get_running_info", side_effect=RedisError("GET failed")
        ):
            tasks.recover_stuck_run_prompts._original_func()
    else:
        with tasks.distributed_lock_manager.lock(
            f"run_prompt:{data.prompt.id}", timeout=240, thread_local=False
        ):
            tasks.recover_stuck_run_prompts._original_func()
    data.prompt.refresh_from_db()
    cell.refresh_from_db()
    assert data.prompt.status == StatusType.RUNNING.value
    assert cell.status == CellStatus.RUNNING.value


def test_recovery_rechecks_revision_after_candidate_scan(prompt_data, tracker):
    data = prompt_data
    cell = save_cell(data, data.rows[0])
    backdate(data)
    original_lock = tasks.distributed_lock_manager.lock

    @contextmanager
    def queue_before_lock(*args, **kwargs):
        queue_prompt_rows([str(data.prompt.id)], [str(data.rows[0].id)])
        with original_lock(*args, **kwargs) as lock:
            yield lock

    with patch.object(
        tasks.distributed_lock_manager, "lock", side_effect=queue_before_lock
    ):
        tasks.recover_stuck_run_prompts._original_func()
    data.prompt.refresh_from_db()
    cell.refresh_from_db()
    assert data.prompt.status == StatusType.RUNNING.value
    assert cell.status == CellStatus.RUNNING.value


def test_owned_failure_repairs_pending_cells_before_releasing_lease(
    prompt_data, tracker
):
    data = prompt_data
    cell = save_cell(data, data.rows[0])
    with patch.object(tasks, "RunPrompts") as runner:
        runner.return_value.run_prompt.side_effect = ValueError("broken configuration")
        with pytest.raises(ValueError, match="broken configuration"):
            tasks.process_not_started_prompt(
                str(data.prompt.id), revision=data.prompt.updated_at.isoformat()
            )
    data.prompt.refresh_from_db()
    cell.refresh_from_db()
    assert data.prompt.status == StatusType.FAILED.value
    assert cell.status == CellStatus.ERROR.value
    assert tracker.get_running_info(data.prompt.id) is None


def test_same_process_old_token_cannot_renew_successor(tracker):
    assert tracker.mark_running("prompt", runner_info={"run_token": "new"})
    assert not tracker.refresh_running("prompt", run_token="old")
    assert tracker.get_running_info("prompt").metadata == {"run_token": "new"}


def test_cancel_never_rewrites_lease_or_extends_its_ttl(tracker):
    tracker.mark_running("prompt", runner_info={"run_token": "new"}, ttl=30)
    key = tracker._get_key("prompt")
    before = tracker._redis_client.get(key)
    assert tracker.request_cancel("prompt", target="old", replacement=True)
    assert tracker._redis_client.get(key) == before
    assert tracker._redis_client.ttl(key) <= 30
    assert not tracker.should_cancel("prompt", "new")
    assert tracker.get_cancel_request("prompt", "old")["replacement"] is True


def test_replacement_cancellation_stops_renewing_a_hung_worker(tracker):
    tracker.mark_running("prompt", runner_info={"run_token": "old"})
    tracker.request_cancel("prompt", target="old", replacement=True)
    lock = MagicMock()
    lease = tasks.OwnershipLease("prompt", lock=lock, run_token="old")
    lease.renew_once()
    assert lease.lost.is_set()
    lock.extend.assert_not_called()
    assert "renewed_at" not in tracker.get_running_info("prompt").metadata


def test_strict_tracker_read_distinguishes_missing_from_unavailable(tracker):
    assert tracker.get_running_info("missing", strict=True) is None
    with patch.object(
        tracker._redis_client, "get", side_effect=RedisError("unavailable")
    ):
        with pytest.raises(RedisError):
            tracker.get_running_info("missing", strict=True)
        assert tracker.get_running_info("missing") is None
    tracker._redis_available = False
    with pytest.raises(RedisError):
        tracker.get_running_info("missing", strict=True)
    tracker._redis_available = True


@pytest.mark.django_db(transaction=True)
def test_successor_claim_waits_until_result_transaction_finishes(prompt_data, tracker):
    from django.db import connections

    data = prompt_data
    old = tasks._claim_prompt(str(data.prompt.id), {})
    entered, finished = threading.Event(), threading.Event()
    errors = []

    def claim_successor():
        try:
            entered.set()
            with lock_prompt(str(data.prompt.id)):
                tracker.mark_completed(data.prompt.id, run_token=old)
                tasks._claim_prompt(str(data.prompt.id), {})
            finished.set()
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    with guard_prompt_write(str(data.prompt.id), old, tasks._get_fresh_lease):
        thread = threading.Thread(target=claim_successor)
        thread.start()
        assert entered.wait(2)
        assert not finished.wait(0.1)
        save_cell(data, data.rows[0], CellStatus.PASS.value)
    thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert finished.is_set()
    with pytest.raises(OwnershipLostError):
        runner_for(data, old)._save_result(
            data.rows[0], data.column, "stale", {}, CellStatus.PASS.value
        )


def test_empty_pass_placeholder_is_unfinished_work(prompt_data, tracker):
    data = prompt_data
    Cell.objects.create(dataset=data.dataset, column=data.column, row=data.rows[0])
    token = tasks._claim_prompt(str(data.prompt.id), {})
    assert not runner_for(data, token)._cell_was_processed(data.rows[0], data.column)
    queue_prompt_rows([str(data.prompt.id)], [str(data.rows[1].id)])
    data.prompt.refresh_from_db()
    assert str(data.rows[0].id) in data.prompt.queued_row_ids


def test_legacy_retry_adopts_once_and_never_resets_a_newer_request(prompt_data):
    data = prompt_data
    ids, rows = [str(data.prompt.id)], [str(data.rows[0].id)]
    scheduled = timezone.now()
    first = queue_prompt_rows(
        ids, rows, request_id="legacy-activity", scheduled_at=scheduled
    )
    cell = save_cell(data, data.rows[0], CellStatus.PASS.value)
    retried = queue_prompt_rows(
        ids, rows, request_id="legacy-activity", scheduled_at=scheduled
    )
    assert retried == first
    cell.refresh_from_db()
    assert cell.value == "original"
    assert cell.status == CellStatus.PASS.value
    newer = queue_prompt_rows(ids, [str(data.rows[1].id)])
    assert newer != first
    assert (
        queue_prompt_rows(
            ids, rows, request_id="legacy-activity", scheduled_at=scheduled
        )
        == {}
    )


def test_transient_redis_error_does_not_mark_runner_failed(prompt_data, tracker):
    data = prompt_data
    save_cell(data, data.rows[0])
    token = tasks._claim_prompt(str(data.prompt.id), {})
    runner = runner_for(data, token)
    # Fail only the first guarded read; failure finalization could read again.
    with patch.object(
        tasks,
        "_get_fresh_lease",
        side_effect=[RedisError("temporary"), tracker.get_running_info(data.prompt.id)],
    ):
        with pytest.raises(RedisError):
            runner.run_prompt()
    data.prompt.refresh_from_db()
    assert data.prompt.status == StatusType.RUNNING.value
    assert (
        Cell.objects.get(row=data.rows[0], column=data.column).status
        == CellStatus.RUNNING.value
    )


def test_selected_runner_uses_persisted_merged_scope(prompt_data, tracker):
    data = prompt_data
    save_cell(data, data.rows[0], CellStatus.PASS.value)
    requested = [str(data.rows[1].id)]
    queue_prompt_rows([str(data.prompt.id)], requested)
    data.prompt.refresh_from_db()
    token = tasks._claim_prompt(str(data.prompt.id), {})
    runner = runner_for(data, token)
    with patch.object(runner, "process_row") as process:
        runner.run_prompt(edit_mode=True, row_ids=requested)
    assert {str(call.args[0].id) for call in process.call_args_list} == {
        str(data.rows[1].id),
        str(data.rows[2].id),
    }


def test_loading_new_config_after_claim_does_not_adopt_its_revision(
    prompt_data, tracker
):
    data = prompt_data
    token = tasks._claim_prompt(str(data.prompt.id), {})
    data.prompt.save()
    runner = runner_for(data, token)
    with pytest.raises(PromptSupersededError):
        runner._save_result(
            data.rows[0], data.column, "stale", {}, CellStatus.PASS.value
        )
    assert not Cell.objects.filter(column=data.column).exists()


@pytest.mark.django_db(transaction=True)
def test_recovery_skips_a_paused_database_writer(prompt_data, tracker):
    from django.db import connections

    data = prompt_data
    save_cell(data, data.rows[0])
    backdate(data)
    locked, release = threading.Event(), threading.Event()
    errors = []

    def hold_row():
        try:
            with lock_prompt(str(data.prompt.id)):
                locked.set()
                release.wait(5)
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    thread = threading.Thread(target=hold_row)
    thread.start()
    try:
        assert locked.wait(2)
        tasks.recover_stuck_run_prompts._original_func()
        data.prompt.refresh_from_db()
        assert data.prompt.status == StatusType.RUNNING.value
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
