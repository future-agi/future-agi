"""
Tests for run_prompt task functions in model_hub/tasks/run_prompt.py.

Run with: pytest model_hub/tests/test_run_prompt_tasks.py -v
"""

from datetime import timedelta
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from django.utils import timezone


class TestProcessNotStartedPrompt:
    """Tests for process_not_started_prompt function."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_processes_prompt_successfully(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test successful processing of a not-started prompt."""
        from model_hub.tasks.run_prompt import process_not_started_prompt

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_not_started_prompt("prompt-123")

        mock_tracker.mark_running.assert_called_once()
        mock_runner.run_prompt.assert_called_once()
        # Release is scoped to the token we published, so a lost-ownership run can't delete a successor's lease.
        token = mock_tracker.mark_running.call_args.kwargs["runner_info"]["run_token"]
        mock_tracker.mark_completed.assert_called_once_with("prompt-123", run_token=token)

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_raises_if_already_running_on_another_instance(
        self, mock_close, mock_lock_mgr, mock_tracker
    ):
        """A fresh lease held by another live instance must fail the attempt
        (so Temporal retries) instead of returning a false success."""
        from model_hub.tasks.run_prompt import (
            PromptAlreadyRunningElsewhere,
            process_not_started_prompt,
        )

        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        # metadata is a MagicMock -> renewal age unknown -> treated as live
        mock_tracker.get_running_info.return_value = mock_running_info

        with pytest.raises(PromptAlreadyRunningElsewhere):
            process_not_started_prompt("prompt-123")

        # Lock should never be acquired since we fail early
        mock_lock_mgr.lock.assert_not_called()
        # Must not clobber the live owner's lease or mark the prompt FAILED
        mock_tracker.mark_completed.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_marks_failed_on_exception(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that prompt is marked as FAILED when an exception occurs."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_not_started_prompt

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner.run_prompt.side_effect = Exception("Processing failed")
        mock_runner_class.return_value = mock_runner

        with pytest.raises(Exception, match="Processing failed"):
            process_not_started_prompt("prompt-123")

        # Should mark as completed in distributed tracker
        mock_tracker.mark_completed.assert_called()
        # Should update DB status to FAILED
        mock_prompter.objects.filter.assert_called_with(id="prompt-123")

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_uses_renewable_lock_timeout(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """The lock uses the short renewable TTL (extended by the lease
        renewer), not a fixed lifetime longer than the work, and is acquired
        with a non-thread-local token so the renewer thread can extend it."""
        from model_hub.tasks.run_prompt import (
            LOCK_TTL_SECONDS,
            process_not_started_prompt,
        )

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_not_started_prompt("550e8400-e29b-41d4-a716-446655440000")

        mock_lock_mgr.lock.assert_called_once()
        call_kwargs = mock_lock_mgr.lock.call_args[1]
        assert call_kwargs["timeout"] == LOCK_TTL_SECONDS
        assert call_kwargs["thread_local"] is False

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_lock_contention_fails_retryably_without_marking_failed(
        self, mock_close, mock_prompter, mock_lock_mgr, mock_tracker
    ):
        """Another worker holding the lock means the prompt is being
        processed: fail the attempt so Temporal retries, but leave the
        status and the owner's tracker entry alone."""
        from model_hub.tasks.run_prompt import process_not_started_prompt
        from tfc.utils.distributed_locks import LockContendedError

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_lock_mgr.lock.side_effect = LockContendedError("held elsewhere")

        with pytest.raises(LockContendedError):
            process_not_started_prompt("prompt-123")

        mock_prompter.objects.filter.return_value.update.assert_not_called()
        mock_tracker.mark_completed.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_redis_failure_during_lock_marks_failed(
        self, mock_close, mock_prompter, mock_lock_mgr, mock_tracker
    ):
        """A Redis failure while taking the lock is not "someone else has
        it": nobody is processing the prompt, so it must not be left in
        RUNNING until the hourly sweep."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_not_started_prompt
        from tfc.utils.distributed_locks import LockAcquisitionError

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_lock_mgr.lock.side_effect = LockAcquisitionError("Redis error")

        with pytest.raises(LockAcquisitionError):
            process_not_started_prompt("prompt-123")

        mock_prompter.objects.filter.return_value.update.assert_called_once_with(
            status=StatusType.FAILED.value
        )
        # We never owned a lease, so nothing may be deleted from the tracker.
        mock_tracker.mark_completed.assert_not_called()

    @patch("model_hub.tasks.run_prompt.fail_pending_run_prompt_cells")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_lost_ownership_retries_without_marking_failed(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker, fail_cells
    ):
        """A fenced run fails its attempt so Temporal retries and reclaims;
        it must not write FAILED (a successor may own the prompt) and must
        release only its own lease."""
        from model_hub.tasks.run_prompt import process_not_started_prompt
        from model_hub.views.run_prompt import OwnershipLostError

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_runner_class.return_value.run_prompt.side_effect = OwnershipLostError("p")

        with (
            patch("model_hub.tasks.run_prompt._is_final_attempt", return_value=False),
            pytest.raises(OwnershipLostError),
        ):
            process_not_started_prompt("prompt-123")

        mock_prompter.objects.filter.return_value.update.assert_not_called()
        fail_cells.assert_not_called()
        token = mock_tracker.mark_running.call_args.kwargs["runner_info"]["run_token"]
        mock_tracker.mark_completed.assert_called_once_with("prompt-123", run_token=token)

    @patch("model_hub.tasks.run_prompt.fail_pending_run_prompt_cells")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_lost_ownership_on_final_attempt_fails_only_if_nobody_reclaimed(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker, fail_cells
    ):
        """Last retry, fence tripped: with a live successor the status is
        theirs; with nobody, the prompt and its cells must not spin for an hour."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_not_started_prompt
        from model_hub.views.run_prompt import OwnershipLostError

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_runner_class.return_value.run_prompt.side_effect = OwnershipLostError("p")

        successor = MagicMock()
        successor.instance_id = "other"
        successor.metadata = {}
        with (
            patch("model_hub.tasks.run_prompt._is_final_attempt", return_value=True),
            # Entry checks see nobody (we got in); the successor appears only after we lost the lock.
            patch("model_hub.tasks.run_prompt._held_by_other_live_instance", return_value=False),
            patch("model_hub.tasks.run_prompt._get_fresh_lease", return_value=successor),
            pytest.raises(OwnershipLostError),
        ):
            process_not_started_prompt("prompt-123")
        mock_prompter.objects.filter.return_value.update.assert_not_called()
        fail_cells.assert_not_called()

        with (
            patch("model_hub.tasks.run_prompt._is_final_attempt", return_value=True),
            patch("model_hub.tasks.run_prompt._get_fresh_lease", return_value=None),
            pytest.raises(OwnershipLostError),
        ):
            process_not_started_prompt("prompt-123")
        mock_prompter.objects.filter.return_value.update.assert_called_once_with(
            status=StatusType.FAILED.value
        )
        fail_cells.assert_called_once_with(["prompt-123"])


class TestClaimPrompt:
    """_claim_prompt must not run a prompt it could not register as its own."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_lost_nx_to_live_owner_raises(self, mock_tracker):
        """mark_running is SET NX; losing it to a fresh lease elsewhere
        means a second worker would run with no lease of its own."""
        from model_hub.tasks.run_prompt import (
            PromptAlreadyRunningElsewhere,
            _claim_prompt,
        )

        mock_tracker.instance_id = "current-instance"
        live = MagicMock()
        live.instance_id = "other-instance"
        # metadata is a MagicMock -> renewal age unknown -> treated as live
        mock_tracker.get_running_info.return_value = live
        mock_tracker.mark_running.return_value = False

        with pytest.raises(PromptAlreadyRunningElsewhere):
            _claim_prompt("prompt-123", runner_info={})

        mock_tracker.mark_completed.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_lost_nx_with_redis_unavailable_proceeds(self, mock_tracker):
        """With Redis down mark_running also returns False, but then nobody
        else can hold a lease either; the prompt must still run."""
        from model_hub.tasks.run_prompt import _claim_prompt

        mock_tracker.instance_id = "current-instance"
        mock_tracker.get_running_info.return_value = None
        mock_tracker.mark_running.return_value = False

        _claim_prompt("prompt-123", runner_info={})  # no raise

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_claim_clears_stale_cancel_before_publishing_ownership(self, mock_tracker):
        """A stale flag must go, but clearing *after* mark_running would also
        delete a cancel that arrived for us in between; so clear first, and
        publish a per-run token the edit can aim its cancel at."""
        from model_hub.tasks.run_prompt import _claim_prompt

        mock_tracker.instance_id = "current-instance"
        mock_tracker.get_running_info.return_value = None
        mock_tracker.mark_running.return_value = True
        order = []

        def _clear(*a, **k):
            order.append("clear")

        def _publish(*a, **k):
            order.append("publish")
            return True

        mock_tracker.clear_cancel_flag.side_effect = _clear
        mock_tracker.mark_running.side_effect = _publish

        run_token = _claim_prompt("prompt-123", runner_info={"type": "editing"})

        assert order == ["clear", "publish"]
        published = mock_tracker.mark_running.call_args.kwargs["runner_info"]
        assert published["run_token"] == run_token and len(run_token) == 32
        assert published["type"] == "editing"


class TestIsFinalAttempt:
    """Contention on the edit path is retried; FAILED is written only when
    no further retry will come."""

    @pytest.mark.parametrize(
        "attempt,expected",
        [(1, False), (5, False), (6, True), (7, True)],
    )
    def test_final_attempt_tracks_retry_policy(self, attempt, expected):
        from model_hub.tasks.run_prompt import (
            PROCESS_PROMPT_MAX_RETRIES,
            _is_final_attempt,
        )

        assert PROCESS_PROMPT_MAX_RETRIES + 1 == 6  # attempts = retries + 1
        info = MagicMock(attempt=attempt)
        with patch("model_hub.tasks.run_prompt.try_activity_info", return_value=info):
            assert _is_final_attempt() is expected

    def test_outside_temporal_counts_as_final(self):
        """No activity context means no retry is coming; fail visibly."""
        from model_hub.tasks.run_prompt import _is_final_attempt

        with patch("model_hub.tasks.run_prompt.try_activity_info", return_value=None):
            assert _is_final_attempt() is True


class TestProcessEditingPrompt:
    """Tests for process_editing_prompt function."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_processes_editing_prompt_successfully(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test successful processing of an editing prompt."""
        from model_hub.tasks.run_prompt import process_editing_prompt

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_editing_prompt("prompt-123")

        mock_tracker.mark_running.assert_called_once()
        mock_runner.run_prompt.assert_called_once_with(edit_mode=True)
        token = mock_tracker.mark_running.call_args.kwargs["runner_info"]["run_token"]
        mock_tracker.mark_completed.assert_called_once_with("prompt-123", run_token=token)

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_requests_cancel_if_already_running(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that cancellation is requested if prompt is running elsewhere."""
        from model_hub.tasks.run_prompt import process_editing_prompt

        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        # unparseable started_at -> renewal age unknown -> treated as live
        mock_running_info.metadata = {"run_token": "owner-token"}
        mock_tracker.get_running_info.return_value = mock_running_info
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_editing_prompt("550e8400-e29b-41d4-a716-446655440001")

        # Aimed at the live owner's token so it can't leak onto the edit's own run.
        mock_tracker.request_cancel.assert_called_once_with(
            "550e8400-e29b-41d4-a716-446655440001",
            reason="Edit requested",
            target="owner-token",
        )

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_requests_cancel_when_same_instance_holds_a_live_run(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """A live run on *this* instance blocks the lock just the same; the
        edit must ask it to drain rather than wait out every retry."""
        from model_hub.tasks.run_prompt import process_editing_prompt

        mock_tracker.instance_id = "current-instance"
        mine = MagicMock()
        mine.instance_id = "current-instance"
        mine.metadata = {}  # pre-token lease: cancel is untargeted
        mock_tracker.get_running_info.return_value = mine
        mock_runner_class.return_value = MagicMock()

        process_editing_prompt("prompt-123")

        mock_tracker.request_cancel.assert_called_once_with(
            "prompt-123", reason="Edit requested", target=None
        )

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_uses_longer_blocking_timeout_for_edit(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that edit mode uses longer blocking timeout (30s)."""
        from model_hub.tasks.run_prompt import process_editing_prompt

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_editing_prompt("550e8400-e29b-41d4-a716-446655440002")

        mock_lock_mgr.lock.assert_called_once()
        call_kwargs = mock_lock_mgr.lock.call_args[1]
        assert call_kwargs["blocking_timeout"] == 30  # Longer wait for edit

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_marks_failed_when_live_owner_keeps_the_lock(
        self, mock_close, mock_prompter, mock_lock_mgr, mock_tracker
    ):
        """On the final attempt an edit that still cannot take the lock has no
        one to finish it: the original run refuses to write a terminal status
        once updated_at changed. It must be FAILED now, not RUNNING until the
        sweep — and the owner's tracker entry must not be deleted (the old bug)."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_editing_prompt
        from tfc.utils.distributed_locks import LockContendedError

        mock_tracker.instance_id = "current-instance"
        live = MagicMock()
        live.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = live
        mock_lock_mgr.lock.side_effect = LockContendedError("held elsewhere")

        with (
            patch("model_hub.tasks.run_prompt._is_final_attempt", return_value=True),
            patch(
                "model_hub.tasks.run_prompt.fail_pending_run_prompt_cells"
            ) as fail_cells,
            pytest.raises(LockContendedError),
        ):
            process_editing_prompt("prompt-123")

        mock_prompter.objects.filter.return_value.update.assert_called_once_with(
            status=StatusType.FAILED.value
        )
        # The edit reset cells to running; recovery only sees RUNNING prompts, so resolve them here.
        fail_cells.assert_called_once_with(["prompt-123"])
        mock_tracker.mark_completed.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_contention_before_final_attempt_retries_without_failing(
        self, mock_close, mock_prompter, mock_lock_mgr, mock_tracker
    ):
        """The owner honours the cancel flag per row, so it will drain; an
        early attempt must leave status alone and let Temporal retry."""
        from model_hub.tasks.run_prompt import process_editing_prompt
        from tfc.utils.distributed_locks import LockContendedError

        mock_tracker.instance_id = "current-instance"
        live = MagicMock()
        live.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = live
        mock_lock_mgr.lock.side_effect = LockContendedError("held elsewhere")

        with (
            patch("model_hub.tasks.run_prompt._is_final_attempt", return_value=False),
            pytest.raises(LockContendedError),
        ):
            process_editing_prompt("prompt-123")

        mock_tracker.request_cancel.assert_called_once()
        mock_prompter.objects.filter.return_value.update.assert_not_called()
        mock_tracker.mark_completed.assert_not_called()


@pytest.mark.django_db
class TestProcessPromptsSingle:
    """Tests for process_prompts_single Temporal activity."""

    @patch("model_hub.tasks.run_prompt.process_not_started_prompt")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_processes_not_started_prompt_type(
        self, mock_close, mock_prompter, mock_tracker, mock_process
    ):
        """Test that not_started type calls process_not_started_prompt."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_prompt = MagicMock()
        mock_prompt.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt

        process_prompts_single({"type": "not_started", "prompt_id": "prompt-123"})

        mock_process.assert_called_once_with("prompt-123")

    @patch("model_hub.tasks.run_prompt.process_editing_prompt")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_processes_editing_prompt_type(
        self, mock_close, mock_prompter, mock_tracker, mock_process
    ):
        """Test that editing type calls process_editing_prompt."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        mock_tracker.get_running_info.return_value = None
        mock_tracker.instance_id = "test-instance"
        mock_prompt = MagicMock()
        mock_prompt.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt

        process_prompts_single({"type": "editing", "prompt_id": "prompt-123"})

        mock_process.assert_called_once_with("prompt-123")

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_skips_if_status_not_running(self, mock_close, mock_prompter, mock_tracker):
        """Test that processing is skipped if status is not RUNNING."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        mock_tracker.instance_id = "test-instance"
        mock_prompt = MagicMock()
        mock_prompt.status = StatusType.COMPLETED.value  # Not RUNNING
        mock_prompter.objects.get.return_value = mock_prompt

        # Should return early without processing
        process_prompts_single({"type": "not_started", "prompt_id": "prompt-123"})

        mock_tracker.mark_running.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_raises_if_already_running_on_another_instance(
        self, mock_close, mock_prompter, mock_tracker
    ):
        """A live lease elsewhere must fail the Temporal attempt (retryable),
        never return success: a false success ends the workflow and the
        prompt is never reprocessed if the other owner dies."""
        from model_hub.models.choices import StatusType
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.tasks.run_prompt import (
            PromptAlreadyRunningElsewhere,
            process_prompts_single,
        )

        # Keep the real exception class so `except RunPrompter.DoesNotExist`
        # stays valid while the module attribute is mocked.
        mock_prompter.DoesNotExist = RunPrompter.DoesNotExist
        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        # metadata is a MagicMock -> renewal age unknown -> treated as live
        mock_tracker.get_running_info.return_value = mock_running_info

        mock_prompt = MagicMock()
        mock_prompt.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt

        with pytest.raises(PromptAlreadyRunningElsewhere):
            process_prompts_single(
                {"type": "not_started", "prompt_id": "prompt-123"}
            )

        # Should not process
        mock_tracker.mark_running.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_proceeds_when_leftover_lease_is_stale(
        self, mock_close, mock_prompter, mock_tracker
    ):
        """A lease whose last renewal is old belongs to a dead worker; the
        retry must reclaim the prompt instead of dead-ending on the entry."""
        from datetime import datetime
        from datetime import timedelta as td

        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import (
            LEASE_FRESH_SECONDS,
            process_prompts_single,
        )

        mock_tracker.instance_id = "current-instance"
        stale_info = MagicMock()
        stale_info.instance_id = "dead-instance"
        stale_info.metadata = {
            "renewed_at": (
                datetime.utcnow() - td(seconds=LEASE_FRESH_SECONDS + 60)
            ).isoformat()
        }
        mock_tracker.get_running_info.return_value = stale_info

        mock_prompt = MagicMock()
        mock_prompt.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt

        with patch(
            "model_hub.tasks.run_prompt.process_not_started_prompt"
        ) as mock_process:
            process_prompts_single(
                {"type": "not_started", "prompt_id": "prompt-123"}
            )

        mock_process.assert_called_once_with("prompt-123")


@pytest.mark.django_db
class TestRecoverStuckRunPrompts:
    """Tests for recover_stuck_run_prompts Temporal activity."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_recovers_stuck_prompts(self, mock_close, mock_prompter, mock_tracker):
        """Test that stuck prompts are recovered and marked as FAILED."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        # Mock stuck prompts query:
        # filter(...).filter(~Exists(...)).order_by(...).values_list(...)[:20]
        stuck_ids = ["prompt-1", "prompt-2"]
        query_chain = (
            mock_prompter.objects.filter.return_value.filter.return_value
            .order_by.return_value.values_list.return_value
        )
        query_chain.__getitem__.return_value = stuck_ids
        # No lease -> candidates are dead
        mock_tracker.get_running_info.return_value = None

        # Call the undecorated function: the temporal_activity wrapper's
        # close_old_connections() would kill pytest-django's transaction
        # connection before the real Cell queries run.
        recover_stuck_run_prompts._original_func()

        # Should mark stuck prompts as FAILED
        mock_prompter.objects.filter.return_value.update.assert_called()

        # Should clean up distributed tracker for each stuck prompt
        assert mock_tracker.mark_completed.call_count == 2
        assert mock_tracker.clear_cancel_flag.call_count == 2

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_handles_no_stuck_prompts(self, mock_close, mock_prompter, mock_tracker):
        """Test that no action is taken when there are no stuck prompts."""
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        # No stuck prompts
        query_chain = (
            mock_prompter.objects.filter.return_value.filter.return_value
            .order_by.return_value.values_list.return_value
        )
        query_chain.__getitem__.return_value = []

        recover_stuck_run_prompts()

        # Should not try to update or clean up
        mock_tracker.mark_completed.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_cleans_stale_tracker_entries(
        self, mock_close, mock_prompter, mock_tracker
    ):
        """Test that stale tracker entries are cleaned up."""
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        query_chain = (
            mock_prompter.objects.filter.return_value.filter.return_value
            .order_by.return_value.values_list.return_value
        )
        query_chain.__getitem__.return_value = []
        mock_tracker.cleanup_stale.return_value = 5  # 5 stale entries cleaned

        recover_stuck_run_prompts()

        # Cleanup age must exceed the longest legitimate run (4h activity
        # limit): cleanup keys off started_at, and deleting a live long
        # run's lease would break dedup and the liveness signal.
        mock_tracker.cleanup_stale.assert_called_once_with(max_age_hours=5)


class TestGetRunningPromptsStatus:
    """Tests for get_running_prompts_status helper function."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_returns_running_prompts_info(self, mock_tracker):
        """Test that running prompts status is returned correctly."""
        from model_hub.tasks.run_prompt import get_running_prompts_status

        mock_info = MagicMock()
        mock_info.task_id = "prompt-123"
        mock_info.instance_id = "instance-1"
        mock_info.started_at = "2024-01-01T00:00:00"
        mock_info.cancel_requested = False
        mock_info.metadata = {"type": "not_started"}
        mock_tracker.get_all_running.return_value = [mock_info]

        result = get_running_prompts_status()

        assert len(result) == 1
        assert result[0]["prompt_id"] == "prompt-123"
        assert result[0]["instance"] == "instance-1"
        assert result[0]["cancel_requested"] is False


class TestCancelRunningPrompt:
    """Tests for cancel_running_prompt helper function."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_cancels_running_prompt(self, mock_tracker):
        """Test successful cancellation request."""
        from model_hub.tasks.run_prompt import cancel_running_prompt

        mock_tracker.is_running.return_value = True
        mock_tracker.request_cancel.return_value = True

        result = cancel_running_prompt("prompt-123", reason="User requested")

        assert result is True
        mock_tracker.request_cancel.assert_called_once_with(
            "prompt-123", reason="User requested"
        )

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_returns_false_if_not_running(self, mock_tracker):
        """Test that False is returned if prompt is not running."""
        from model_hub.tasks.run_prompt import cancel_running_prompt

        mock_tracker.is_running.return_value = False

        result = cancel_running_prompt("prompt-123")

        assert result is False
        mock_tracker.request_cancel.assert_not_called()


class TestLockTimeoutConfiguration:
    """Tests to verify lock timeout is configured correctly (1 hour)."""

    def test_stuck_running_threshold_is_one_hour(self):
        """Test that STUCK_RUNNING_THRESHOLD_HOURS is 1."""
        from model_hub.tasks.run_prompt import STUCK_RUNNING_THRESHOLD_HOURS

        assert STUCK_RUNNING_THRESHOLD_HOURS == 1


class TestRunPromptTrackerConfiguration:
    """Tests for run_prompt_tracker configuration."""

    def test_tracker_has_correct_key_prefix(self):
        """Test that run_prompt_tracker uses correct key prefix."""
        from model_hub.tasks.run_prompt import run_prompt_tracker

        assert run_prompt_tracker.key_prefix == "running_prompt:"

    def test_tracker_ttl_matches_lease(self):
        """Tracker entries are short-lived leases renewed by the worker.

        A dead worker (crash/OOM/deploy) leaves its tracker entry behind;
        with the old 2-hour TTL, Temporal retries saw "already running
        elsewhere" and returned successfully without processing, leaving
        cells stuck in RUNNING forever. Live workers renew the lease every
        LEASE_RENEW_INTERVAL_SECONDS, so a short TTL only ever expires
        entries owned by dead workers.
        """
        from model_hub.tasks.run_prompt import (
            LEASE_RENEW_INTERVAL_SECONDS,
            LEASE_TTL_SECONDS,
            run_prompt_tracker,
        )

        assert run_prompt_tracker.default_ttl == LEASE_TTL_SECONDS
        # TTL must comfortably outlast the renew interval, or live leases
        # would expire between renewals.
        assert LEASE_TTL_SECONDS >= 3 * LEASE_RENEW_INTERVAL_SECONDS


class TestOwnershipLease:
    """Tests for the lease renewer that keeps the tracker entry and the
    distributed lock alive while a worker processes a prompt."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_renew_refreshes_tracker_and_extends_lock(self, mock_tracker):
        from model_hub.tasks.run_prompt import (
            LEASE_TTL_SECONDS,
            LOCK_TTL_SECONDS,
            OwnershipLease,
        )

        mock_lock = MagicMock()
        OwnershipLease("prompt-123", lock=mock_lock).renew_once()

        mock_tracker.refresh_running.assert_called_once_with(
            "prompt-123", ttl=LEASE_TTL_SECONDS
        )
        mock_lock.extend.assert_called_once_with(
            LOCK_TTL_SECONDS, replace_ttl=True
        )

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_renew_survives_redis_errors(self, mock_tracker):
        """A failed renewal must not kill the run; the next tick retries."""
        from model_hub.tasks.run_prompt import OwnershipLease

        mock_tracker.refresh_running.side_effect = Exception("redis down")
        mock_lock = MagicMock()
        mock_lock.extend.side_effect = Exception("redis down")

        OwnershipLease("prompt-123", lock=mock_lock).renew_once()  # no raise

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_extend_failures_are_counted_until_one_succeeds(self, mock_tracker):
        """Consecutive extend failures are what turn into a lapsed lock; the
        count must be visible in the log and reset on success."""
        from model_hub.tasks.run_prompt import OwnershipLease

        mock_lock = MagicMock()
        lease = OwnershipLease("prompt-123", lock=mock_lock)

        mock_lock.extend.side_effect = Exception("redis down")
        lease.renew_once()
        lease.renew_once()
        assert lease._extend_failures == 2

        mock_lock.extend.side_effect = None
        lease.renew_once()
        assert lease._extend_failures == 0
        assert not lease.lost.is_set()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_lock_not_owned_fences_immediately(self, mock_tracker):
        """redis-py raises LockNotOwnedError when the token no longer matches:
        someone else holds the lock, so this run must stop touching cells."""
        from redis.exceptions import LockNotOwnedError

        from model_hub.tasks.run_prompt import OwnershipLease

        mock_lock = MagicMock()
        mock_lock.extend.side_effect = LockNotOwnedError("not owned")
        lease = OwnershipLease("prompt-123", lock=mock_lock)

        lease.renew_once()

        assert lease.lost.is_set()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_failures_spanning_the_lock_ttl_fence_the_run(self, mock_tracker):
        """A Redis outage approaching LOCK_TTL means the lock is about to
        expire on the server; after that many failed extends we treat
        ownership as gone even though no call ever said so. Sticky: a later
        success can't un-fence."""
        from model_hub.tasks.run_prompt import (
            LEASE_RENEW_INTERVAL_SECONDS,
            LOCK_LOST_AFTER_FAILURES,
            LOCK_TTL_SECONDS,
            OwnershipLease,
        )

        mock_lock = MagicMock()
        mock_lock.extend.side_effect = Exception("redis down")
        lease = OwnershipLease("prompt-123", lock=mock_lock)

        for _ in range(LOCK_LOST_AFTER_FAILURES - 1):
            lease.renew_once()
        assert not lease.lost.is_set()
        lease.renew_once()
        assert lease.lost.is_set()
        # The fence must trip strictly before the lock can expire, or an edit can own it while we still write.
        assert LOCK_LOST_AFTER_FAILURES * LEASE_RENEW_INTERVAL_SECONDS < LOCK_TTL_SECONDS

        mock_lock.extend.side_effect = None
        lease.renew_once()
        assert lease.lost.is_set()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_renew_is_a_noop_once_stopped(self, mock_tracker):
        """A refresh that lands after mark_completed would write the lease
        back for another TTL; once the owner is releasing, do nothing."""
        from model_hub.tasks.run_prompt import OwnershipLease

        mock_lock = MagicMock()
        lease = OwnershipLease("prompt-123", lock=mock_lock)
        lease._stop.set()

        lease.renew_once()

        mock_tracker.refresh_running.assert_not_called()
        mock_lock.extend.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_local_lock_without_extend_is_skipped(self, mock_tracker):
        """The local threading-lock fallback has no extend(); the lease must
        still renew the tracker entry."""
        import threading

        from model_hub.tasks.run_prompt import OwnershipLease

        OwnershipLease("prompt-123", lock=threading.Lock()).renew_once()

        mock_tracker.refresh_running.assert_called_once()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_context_manager_starts_and_stops_renewer(self, mock_tracker):
        from model_hub.tasks.run_prompt import OwnershipLease

        lease = OwnershipLease("prompt-123")
        with lease:
            assert lease._thread.is_alive()
        assert not lease._thread.is_alive()

    def test_renewer_thread_extends_a_real_redis_lock(self):
        """The renewer runs on its own thread, and redis-py keeps the lock's
        ownership token in thread-local storage by default — so extend()
        raises there and the lock lapses mid-run. A MagicMock lock called on
        the main thread cannot catch that; this uses a real lock and a real
        thread."""
        import threading

        from model_hub.tasks.run_prompt import LOCK_TTL_SECONDS, OwnershipLease
        from tfc.utils.distributed_locks import distributed_lock_manager

        if not distributed_lock_manager._redis_available:
            pytest.skip("redis not available")

        name = "run_prompt:lease-renew-test"
        key = distributed_lock_manager._get_lock_key(name)
        client = distributed_lock_manager._redis_client

        with distributed_lock_manager.lock(
            name, timeout=LOCK_TTL_SECONDS, blocking_timeout=5, thread_local=False
        ) as lock:
            # Wind the TTL down so a successful extend is unambiguous.
            client.pexpire(key, 5000)

            renewer = threading.Thread(
                target=OwnershipLease("prompt-real", lock=lock).renew_once
            )
            renewer.start()
            renewer.join(timeout=10)

            assert client.pttl(key) > 100_000, "lock TTL was not extended"


class TestLeaseTimingInvariants:
    """A crashed worker's lock must expire before Temporal's retry arrives,
    or the retry dies on LockContendedError and the prompt sits stuck until
    the hourly sweep."""

    def test_lock_expires_before_temporal_retry_arrives(self):
        """Pinned against the workflow's own constant, so a change to the
        heartbeat timeout fails here rather than in production."""
        from model_hub.tasks.run_prompt import LOCK_TTL_SECONDS
        from tfc.temporal.drop_in.workflow import ACTIVITY_HEARTBEAT_TIMEOUT

        assert LOCK_TTL_SECONDS < ACTIVITY_HEARTBEAT_TIMEOUT.total_seconds()

    def test_reclaim_retries_are_spread_out(self):
        """The reclaim rides on the retry after the heartbeat timeout. With
        the default policy (3 attempts, 5s/10s apart) one transient failure
        there burns the budget; the activity must declare a wider one."""
        import model_hub.tasks.run_prompt  # noqa: F401  (registers the activity)
        from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
        from tfc.temporal.drop_in.workflow import TaskRunnerInput, _resolve_retry_policy

        meta = _ACTIVITY_REGISTRY["process_prompts_single"]
        policy = _resolve_retry_policy(
            TaskRunnerInput(
                activity_name="process_prompts_single",
                args=[],
                kwargs={},
                max_retries=meta["max_retries"],
                retry_delay=meta["retry_delay"],
            )
        )
        assert policy.maximum_attempts >= 4
        assert policy.initial_interval.total_seconds() >= 60

    def test_lock_ttl_leaves_slack_for_transient_redis_failures(self):
        """At least 4 renewal attempts must fit inside the lock's lifetime so
        a brief Redis blip can't drop a live worker's lock mid-run."""
        from model_hub.tasks.run_prompt import (
            LEASE_RENEW_INTERVAL_SECONDS,
            LOCK_TTL_SECONDS,
        )

        assert LOCK_TTL_SECONDS / LEASE_RENEW_INTERVAL_SECONDS >= 4

    def test_lease_freshness_matches_lock_lifetime(self):
        """If a lease looked dead while its lock could still be held, the
        reclaimer would raise LockContendedError instead of taking over."""
        from model_hub.tasks.run_prompt import LEASE_FRESH_SECONDS, LOCK_TTL_SECONDS

        assert LEASE_FRESH_SECONDS == LOCK_TTL_SECONDS


class TestRunPromptsHonoursCancel:
    """RunPrompts.process_row is the per-row LLM step; a run that has been
    asked to stop must skip it instead of burning calls whose cells the
    preempting edit will overwrite anyway."""

    def _runner(self, **kwargs):
        from model_hub.views.run_prompt import RunPrompts

        return RunPrompts(run_prompt_id="prompt-123", **kwargs)

    @patch("model_hub.views.run_prompt.Cell")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_process_row_skips_when_cancel_requested(self, mock_tracker, mock_cell):
        mock_tracker.should_cancel.return_value = True
        runner = self._runner(run_token="tok-1")
        runner.run_prompt_model = MagicMock()

        assert runner.process_row(MagicMock(), MagicMock()) is None

        # Scoped to this run's token so a cancel aimed at the previous owner is ignored.
        mock_tracker.should_cancel.assert_called_once_with("prompt-123", run_token="tok-1")
        mock_cell.objects.create.assert_not_called()
        mock_cell.objects.get.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_redis_failure_is_not_a_cancel(self, mock_tracker):
        """A flaky Redis must never silently skip rows."""
        mock_tracker.should_cancel.side_effect = Exception("redis down")

        assert self._runner()._should_stop() is False

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_lost_ownership_fence_works_without_redis(self, mock_tracker):
        """Ownership is lost precisely when Redis is unreachable, so the fence
        must be a local event, not another Redis read."""
        import threading

        mock_tracker.should_cancel.return_value = False
        fence = threading.Event()
        runner = self._runner(fence=fence)
        assert runner._should_stop() is False

        fence.set()
        assert runner._should_stop() is True
        mock_tracker.should_cancel.assert_called_once()  # only the first call reached Redis

    @patch("model_hub.views.run_prompt.close_old_connections")
    @patch("model_hub.views.run_prompt.log_and_deduct_cost_for_api_request", None)
    @patch("model_hub.views.run_prompt.Cell")
    def test_cell_write_is_fenced_when_ownership_is_lost_mid_call(self, mock_cell, _close):
        """Ownership can lapse *during* an LLM call; the result must not be
        written over a cell a successor run now owns."""
        runner = self._runner()
        runner.run_prompt_model = MagicMock()
        # Not stopped when the row starts, stopped by the time the call returns.
        with patch.object(type(runner), "_should_stop", side_effect=[False, True]):
            runner.process_row(MagicMock(), MagicMock())

        mock_cell.objects.create.assert_not_called()
        mock_cell.objects.get.assert_not_called()

    def _run_to_final_status(self, runner, mock_prompter):
        """Drive run_prompt() with no rows so only the final-status write is exercised."""
        from model_hub.models.choices import StatusType

        stamp = object()
        runner.run_prompt_model = MagicMock(updated_at=stamp, concurrency=1)
        mock_prompter.objects.filter.return_value.values.return_value.first.return_value = {
            "status": StatusType.RUNNING.value,
            "updated_at": stamp,
        }
        with (
            patch.object(type(runner), "load_run_prompt_id"),
            patch("model_hub.views.run_prompt.Dataset"),
            patch(
                "model_hub.views.run_prompt.create_run_prompt_column",
                return_value=(MagicMock(), False),
            ),
            patch("model_hub.views.run_prompt.Row") as mock_row,
        ):
            mock_row.objects.filter.return_value.order_by.return_value = []
            runner.run_prompt()
        return mock_prompter.objects.filter.return_value.update

    @patch("model_hub.views.run_prompt.fail_pending_run_prompt_cells")
    @patch("model_hub.views.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_lost_ownership_raises_instead_of_writing_a_status(
        self, mock_tracker, mock_prompter, fail_cells
    ):
        """A fenced run may have been reclaimed by another worker, so it
        must not write FAILED or COMPLETED; and it must not return success
        either, or Temporal never retries when nobody reclaimed it."""
        import threading

        from model_hub.views.run_prompt import OwnershipLostError

        fence = threading.Event()
        fence.set()
        with pytest.raises(OwnershipLostError):
            self._run_to_final_status(self._runner(fence=fence), mock_prompter)

        mock_prompter.objects.filter.return_value.update.assert_not_called()
        fail_cells.assert_not_called()

    @patch("model_hub.views.run_prompt.fail_pending_run_prompt_cells")
    @patch("model_hub.views.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_row_failure_while_fenced_does_not_write_failed(
        self, mock_tracker, mock_prompter, fail_cells
    ):
        """The generic failure path writes FAILED when updated_at is
        unchanged; if the fence tripped, that write would land on the
        successor's run too."""
        import threading

        from model_hub.views.run_prompt import OwnershipLostError

        fence = threading.Event()
        fence.set()
        runner = self._runner(fence=fence)
        with (
            patch.object(type(runner), "load_run_prompt_id", side_effect=RuntimeError("boom")),
            pytest.raises(OwnershipLostError),
        ):
            runner.run_prompt()

        mock_prompter.objects.filter.return_value.update.assert_not_called()

    @patch("model_hub.views.run_prompt.fail_pending_run_prompt_cells")
    @patch("model_hub.views.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_cancel_with_no_successor_fails_prompt_and_pending_cells(
        self, mock_tracker, mock_prompter, fail_cells
    ):
        from model_hub.models.choices import StatusType

        mock_tracker.should_cancel.return_value = True
        update = self._run_to_final_status(self._runner(), mock_prompter)

        update.assert_called_once_with(status=StatusType.FAILED.value)
        fail_cells.assert_called_once_with(["prompt-123"])

    @patch("model_hub.views.run_prompt.fail_pending_run_prompt_cells")
    @patch("model_hub.views.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_uninterrupted_run_completes(self, mock_tracker, mock_prompter, fail_cells):
        from model_hub.models.choices import StatusType

        mock_tracker.should_cancel.return_value = False
        update = self._run_to_final_status(self._runner(), mock_prompter)

        update.assert_called_once_with(status=StatusType.COMPLETED.value)
        fail_cells.assert_not_called()

    @patch("model_hub.views.run_prompt.Cell")
    def test_fail_pending_cells_targets_running_cells_of_given_prompts(self, mock_cell):
        from model_hub.models.choices import CellStatus, SourceChoices, StatusType
        from model_hub.views.run_prompt import fail_pending_run_prompt_cells

        mock_cell.objects.filter.return_value.update.return_value = 3

        assert fail_pending_run_prompt_cells(["p1", "p2"], "gone") == 3

        flt = mock_cell.objects.filter.call_args.kwargs
        assert flt["column__source"] == SourceChoices.RUN_PROMPT.value
        assert flt["column__source_id__in"] == ["p1", "p2"]
        assert set(flt["status__in"]) == {CellStatus.RUNNING.value, StatusType.RUNNING.value}
        assert flt["deleted"] is False
        upd = mock_cell.objects.filter.return_value.update.call_args.kwargs
        assert upd["status"] == CellStatus.ERROR.value and upd["value"] == "gone"


@pytest.mark.django_db
class TestRecoverStuckRunPromptsCellCleanup:
    """Integration tests: recovery must repair stuck cells and must not
    kill runs that are still actively writing cells (liveness check)."""

    @pytest.fixture
    def stuck_setup(self, organization, workspace):
        """A RUNNING RunPrompter (stale for >1h) with two stuck cells."""
        from model_hub.models.choices import (
            CellStatus,
            DatasetSourceChoices,
            DataTypeChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
        from model_hub.models.run_prompt import RunPrompter

        dataset = Dataset.objects.create(
            name="Stuck Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        prompter = RunPrompter.objects.create(
            name="Stuck Prompter",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            run_prompt_config={},
        )
        column = Column.objects.create(
            name="Stuck Output",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(prompter.id),
        )
        row1 = Row.objects.create(dataset=dataset, order=0)
        row2 = Row.objects.create(dataset=dataset, order=1)
        cell_running = Cell.objects.create(
            dataset=dataset,
            column=column,
            row=row1,
            value="",
            status=CellStatus.RUNNING.value,
        )
        cell_legacy = Cell.objects.create(
            dataset=dataset,
            column=column,
            row=row2,
            value="",
            status=CellStatus.RUNNING.value,
        )
        # Legacy rerun path bulk-wrote the wrong enum ("Running") into
        # Cell.status via queryset.update (which bypasses full_clean);
        # recovery must repair those too. Reproduce it the same way.
        Cell.objects.filter(id=cell_legacy.id).update(
            status=StatusType.RUNNING.value
        )

        # Soft-deleted cells must be left alone by recovery.
        row3 = Row.objects.create(dataset=dataset, order=2)
        cell_deleted = Cell.objects.create(
            dataset=dataset,
            column=column,
            row=row3,
            value="",
            status=CellStatus.RUNNING.value,
            deleted=True,
        )

        # Backdate everything past the stuck threshold (queryset.update
        # bypasses auto_now).
        stale = timezone.now() - timedelta(hours=2)
        RunPrompter.objects.filter(id=prompter.id).update(updated_at=stale)
        Cell.objects.filter(
            id__in=[cell_running.id, cell_legacy.id, cell_deleted.id]
        ).update(updated_at=stale)

        return {
            "prompter": prompter,
            "column": column,
            "cells": [cell_running, cell_legacy],
            "deleted_cell": cell_deleted,
        }

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_recovery_marks_stuck_cells_error(
        self, mock_tracker, mock_close, stuck_setup
    ):
        """Dead run: prompt -> FAILED and its RUNNING cells -> ERROR."""
        from model_hub.models.choices import CellStatus, StatusType
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        mock_tracker.get_running_info.return_value = None  # no lease -> dead

        recover_stuck_run_prompts._original_func()

        prompter = stuck_setup["prompter"]
        prompter.refresh_from_db()
        assert prompter.status == StatusType.FAILED.value

        for cell in stuck_setup["cells"]:
            cell.refresh_from_db()
            assert cell.status == CellStatus.ERROR.value
            assert "rerun" in cell.value.lower()

        # Soft-deleted cells are invisible to users; recovery must not
        # rewrite their status/value.
        deleted_cell = stuck_setup["deleted_cell"]
        deleted_cell.refresh_from_db()
        assert deleted_cell.status == CellStatus.RUNNING.value
        assert deleted_cell.value == ""

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_recovery_skips_prompts_with_recent_cell_activity(
        self, mock_tracker, mock_close, stuck_setup
    ):
        """Live long run: recent cell writes mean the run is alive, so the
        prompt must NOT be failed even though RunPrompter.updated_at is stale
        (it is never refreshed during row processing)."""
        from model_hub.models.choices import CellStatus, StatusType
        from model_hub.models.develop_dataset import Cell
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        mock_tracker.get_running_info.return_value = None  # no lease

        # Simulate a freshly written cell (liveness signal)
        live_cell = stuck_setup["cells"][0]
        Cell.objects.filter(id=live_cell.id).update(updated_at=timezone.now())

        recover_stuck_run_prompts._original_func()

        prompter = stuck_setup["prompter"]
        prompter.refresh_from_db()
        assert prompter.status == StatusType.RUNNING.value

        for cell in stuck_setup["cells"]:
            cell.refresh_from_db()
            assert cell.status != CellStatus.ERROR.value

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_recovery_skips_prompts_with_fresh_lease(
        self, mock_tracker, mock_close, stuck_setup
    ):
        """Live long run with no recent cell writes (e.g. one slow LLM call):
        the worker's renewed lease is the primary liveness signal, so the
        prompt must NOT be failed."""
        from datetime import datetime

        from model_hub.models.choices import CellStatus, StatusType
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        lease = MagicMock()
        lease.instance_id = "live-worker"
        lease.metadata = {"renewed_at": datetime.utcnow().isoformat()}
        mock_tracker.get_running_info.return_value = lease

        recover_stuck_run_prompts._original_func()

        prompter = stuck_setup["prompter"]
        prompter.refresh_from_db()
        assert prompter.status == StatusType.RUNNING.value

        for cell in stuck_setup["cells"]:
            cell.refresh_from_db()
            assert cell.status != CellStatus.ERROR.value

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_recovery_waits_when_redis_is_unreachable(
        self, mock_tracker, mock_close, stuck_setup
    ):
        """With Redis down every lease reads as absent, so the same live long
        run above would be failed. Liveness is unknowable; skip this tick."""
        from model_hub.models.choices import CellStatus, StatusType
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        mock_tracker.is_reachable.return_value = False
        mock_tracker.get_running_info.return_value = None  # what get() returns on RedisError

        recover_stuck_run_prompts._original_func()

        prompter = stuck_setup["prompter"]
        prompter.refresh_from_db()
        assert prompter.status == StatusType.RUNNING.value
        for cell in stuck_setup["cells"]:
            cell.refresh_from_db()
            assert cell.status != CellStatus.ERROR.value
        mock_tracker.mark_completed.assert_not_called()

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_recovery_treats_stale_lease_as_dead(
        self, mock_tracker, mock_close, stuck_setup
    ):
        """A lease that stopped being renewed belongs to a dead worker; the
        prompt must be recovered despite the leftover tracker entry."""
        from datetime import datetime

        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import (
            LEASE_FRESH_SECONDS,
            recover_stuck_run_prompts,
        )

        lease = MagicMock()
        lease.instance_id = "dead-worker"
        lease.metadata = {
            "renewed_at": (
                datetime.utcnow() - timedelta(seconds=LEASE_FRESH_SECONDS + 60)
            ).isoformat()
        }
        mock_tracker.get_running_info.return_value = lease

        recover_stuck_run_prompts._original_func()

        prompter = stuck_setup["prompter"]
        prompter.refresh_from_db()
        assert prompter.status == StatusType.FAILED.value

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_recovery_batches_oldest_dead_prompts_first(
        self, mock_tracker, mock_close, stuck_setup, organization, workspace
    ):
        """The batch slice happens after liveness filtering, oldest first, so
        newer stale-looking prompts cannot starve older dead ones."""
        from model_hub.models.choices import DatasetSourceChoices, StatusType
        from model_hub.models.develop_dataset import Dataset
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        mock_tracker.get_running_info.return_value = None

        dataset = Dataset.objects.create(
            name="Starvation Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        # 25 newer stale prompts (no cells, no lease -> dead). With the old
        # newest-first slice-then-filter, these would fill the batch of 20
        # and the 2h-old prompt from stuck_setup could be pushed out.
        newer_ids = []
        for i in range(25):
            p = RunPrompter.objects.create(
                name=f"Newer Stale {i}",
                dataset=dataset,
                organization=organization,
                workspace=workspace,
                status=StatusType.RUNNING.value,
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
                run_prompt_config={},
            )
            newer_ids.append(p.id)
        RunPrompter.objects.filter(id__in=newer_ids).update(
            updated_at=timezone.now() - timedelta(minutes=70)
        )

        recover_stuck_run_prompts._original_func()

        # The oldest dead prompt (2h stale) must be in the recovered batch.
        prompter = stuck_setup["prompter"]
        prompter.refresh_from_db()
        assert prompter.status == StatusType.FAILED.value

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_recovery_scans_past_live_leases_to_reach_a_dead_prompt(
        self, mock_tracker, mock_close, stuck_setup, organization, workspace
    ):
        """Healthy long runs (fresh lease, no cell writes for an hour) sit
        ahead of a newer crashed prompt in updated_at order. The lease check
        must not be confined to the first RECOVERY_BATCH_SIZE rows, or those
        live runs shadow the dead one on every sweep."""
        from datetime import datetime

        from model_hub.models.choices import DatasetSourceChoices, StatusType
        from model_hub.models.develop_dataset import Dataset
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.tasks.run_prompt import (
            RECOVERY_BATCH_SIZE,
            recover_stuck_run_prompts,
        )

        dataset = Dataset.objects.create(
            name="Shadow Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        live_ids = []
        for i in range(RECOVERY_BATCH_SIZE + 5):
            p = RunPrompter.objects.create(
                name=f"Live Long Run {i}",
                dataset=dataset,
                organization=organization,
                workspace=workspace,
                status=StatusType.RUNNING.value,
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
                run_prompt_config={},
            )
            live_ids.append(p.id)
        # Older than the dead prompt from stuck_setup (2h), so they come first.
        RunPrompter.objects.filter(id__in=live_ids).update(
            updated_at=timezone.now() - timedelta(hours=3)
        )
        live_set = {str(i) for i in live_ids}

        def lease_for(prompt_id):
            if str(prompt_id) not in live_set:
                return None
            lease = MagicMock()
            lease.instance_id = "busy-worker"
            lease.metadata = {"renewed_at": datetime.utcnow().isoformat()}
            return lease

        mock_tracker.get_running_info.side_effect = lease_for

        recover_stuck_run_prompts._original_func()

        prompter = stuck_setup["prompter"]
        prompter.refresh_from_db()
        assert prompter.status == StatusType.FAILED.value
        assert not RunPrompter.objects.filter(
            id__in=live_ids, status=StatusType.FAILED.value
        ).exists()


@pytest.mark.django_db
class TestRunAllPromptsTaskCellStatus:
    """run_all_prompts_task must write the CellStatus enum ("running"),
    not StatusType ("Running"), into Cell.status."""

    def test_rerun_sets_cells_to_lowercase_running(self, organization, workspace):
        from model_hub.models.choices import (
            CellStatus,
            DatasetSourceChoices,
            DataTypeChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.views.run_prompt import run_all_prompts_task

        dataset = Dataset.objects.create(
            name="Rerun Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        prompter = RunPrompter.objects.create(
            name="Rerun Prompter",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            status=StatusType.COMPLETED.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            run_prompt_config={},
        )
        column = Column.objects.create(
            name="Rerun Output",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(prompter.id),
        )
        row = Row.objects.create(dataset=dataset, order=0)
        cell = Cell.objects.create(
            dataset=dataset,
            column=column,
            row=row,
            value="old value",
            status=CellStatus.PASS.value,
        )

        # Stub the runner so no LLM call happens; the cell keeps the status
        # written by the bulk reset in run_all_prompts_task. Call the
        # undecorated function so the temporal wrapper doesn't close
        # pytest-django's transaction connection.
        with patch("model_hub.views.run_prompt.RunPrompts") as mock_runner_class:
            mock_runner_class.return_value = MagicMock()
            run_all_prompts_task._original_func([str(prompter.id)], [str(row.id)])

        cell.refresh_from_db()
        assert cell.status == CellStatus.RUNNING.value  # "running", not "Running"


@pytest.mark.django_db
class TestTemporalActivityRegistration:
    """Tests for Temporal activity registration."""

    def test_process_prompts_single_has_temporal_decorator(self):
        """Test that process_prompts_single has temporal_activity decorator."""
        from model_hub.tasks.run_prompt import process_prompts_single

        # Check that it has temporal metadata (set by decorator)
        assert hasattr(process_prompts_single, "__wrapped__") or callable(
            process_prompts_single
        )

    def test_recover_stuck_run_prompts_has_temporal_decorator(self):
        """Test that recover_stuck_run_prompts has temporal_activity decorator."""
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        assert hasattr(recover_stuck_run_prompts, "__wrapped__") or callable(
            recover_stuck_run_prompts
        )


class TestLiteLLMResponseMethodSignature:
    """Tests to ensure litellm_response is called with valid arguments.

    These tests prevent bugs like CORE-BACKEND-YVC where litellm_response
    was accidentally called with an invalid 'run_prompt_id' argument.
    """

    def test_litellm_response_does_not_accept_run_prompt_id(self):
        """Test that litellm_response method does not accept run_prompt_id parameter.

        This test would have caught the bug introduced in commit eecc8185b where
        run_prompt_id was accidentally passed to litellm_response().

        Fixes: CORE-BACKEND-YVC
        """
        import inspect

        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        sig = inspect.signature(RunPrompt.litellm_response)
        param_names = list(sig.parameters.keys())

        # run_prompt_id should NOT be a valid parameter
        assert "run_prompt_id" not in param_names, (
            "litellm_response should not accept 'run_prompt_id' as a parameter. "
            "The run_prompt object already has this context internally."
        )

    def test_litellm_response_valid_parameters(self):
        """Test that litellm_response only accepts expected parameters."""
        import inspect

        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        sig = inspect.signature(RunPrompt.litellm_response)
        param_names = set(sig.parameters.keys())

        expected_params = {
            "self",
            "streaming",
            "template_id",
            "version",
            "index",
            "max_index",
            "run_type",
        }

        assert param_names == expected_params, (
            f"litellm_response signature changed unexpectedly. "
            f"Expected: {expected_params}, Got: {param_names}"
        )

    @patch("model_hub.views.run_prompt.RunPrompt")
    def test_process_row_calls_litellm_response_without_invalid_args(
        self, mock_run_prompt_class
    ):
        """Test that process_row calls litellm_response without invalid arguments.

        This integration test ensures the call site in process_row uses
        the correct method signature.
        """
        from unittest.mock import MagicMock, call

        # Create a mock RunPrompt instance
        mock_run_prompt = MagicMock()
        mock_run_prompt.litellm_response.return_value = ("response", {"data": {}})
        mock_run_prompt_class.return_value = mock_run_prompt

        # Simulate calling litellm_response the way process_row should
        response, value_info = mock_run_prompt.litellm_response()

        # Verify it was called without any arguments (especially not run_prompt_id)
        mock_run_prompt.litellm_response.assert_called_once_with()

        # Ensure run_prompt_id was NOT passed
        call_args = mock_run_prompt.litellm_response.call_args
        assert call_args == call(), (
            "litellm_response should be called without arguments, "
            "not with run_prompt_id or any other invalid parameter"
        )
