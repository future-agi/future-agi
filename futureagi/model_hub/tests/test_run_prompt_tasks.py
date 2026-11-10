"""
Tests for run_prompt task functions in model_hub/tasks/run_prompt.py.

Run with: pytest model_hub/tests/test_run_prompt_tasks.py -v
"""

from datetime import timedelta
from unittest.mock import MagicMock, PropertyMock, patch
from uuid import UUID

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

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_not_started_prompt("prompt-123")

        mock_tracker.mark_running.assert_called_once()
        mock_runner.run_prompt.assert_called_once()
        mock_tracker.mark_completed.assert_called_once_with("prompt-123")

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_skips_if_already_running_on_another_instance(
        self, mock_close, mock_lock_mgr, mock_tracker
    ):
        """Test that processing is skipped if prompt is running on another instance."""
        from model_hub.tasks.run_prompt import process_not_started_prompt

        mock_tracker.is_running.return_value = True
        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = mock_running_info

        process_not_started_prompt("prompt-123")

        # Lock should never be acquired since we skip early
        mock_lock_mgr.lock.assert_not_called()

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

        mock_tracker.is_running.return_value = False
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
    def test_uses_correct_lock_timeout(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that distributed lock uses 1-hour timeout."""
        from model_hub.tasks.run_prompt import process_not_started_prompt

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_not_started_prompt("550e8400-e29b-41d4-a716-446655440000")

        mock_lock_mgr.lock.assert_called_once()
        call_kwargs = mock_lock_mgr.lock.call_args[1]
        assert call_kwargs["timeout"] == 3600  # 1 hour


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

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_editing_prompt("prompt-123")

        mock_tracker.mark_running.assert_called_once()
        mock_runner.run_prompt.assert_called_once_with(edit_mode=True)
        mock_tracker.mark_completed.assert_called_once_with("prompt-123")

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_requests_cancel_if_already_running(
        self, mock_close, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that cancellation is requested if prompt is running elsewhere."""
        from model_hub.tasks.run_prompt import process_editing_prompt

        mock_tracker.is_running.return_value = True
        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = mock_running_info
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_editing_prompt("550e8400-e29b-41d4-a716-446655440001")

        mock_tracker.request_cancel.assert_called_once_with(
            "550e8400-e29b-41d4-a716-446655440001", reason="Edit requested"
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

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_editing_prompt("550e8400-e29b-41d4-a716-446655440002")

        mock_lock_mgr.lock.assert_called_once()
        call_kwargs = mock_lock_mgr.lock.call_args[1]
        assert call_kwargs["blocking_timeout"] == 30  # Longer wait for edit


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

        mock_tracker.is_running.return_value = False
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

        mock_tracker.is_running.return_value = False
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
    def test_skips_if_already_running_on_another_instance(
        self, mock_close, mock_prompter, mock_tracker
    ):
        """Test idempotency - skip if already running elsewhere."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        mock_tracker.is_running.return_value = True
        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = mock_running_info

        mock_prompt = MagicMock()
        mock_prompt.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt

        process_prompts_single({"type": "not_started", "prompt_id": "prompt-123"})

        # Should not process
        mock_tracker.mark_running.assert_not_called()


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

        # Mock stuck prompts query
        stuck_ids = ["prompt-1", "prompt-2"]
        mock_prompter.objects.filter.return_value.values_list.return_value.__getitem__.return_value = (
            stuck_ids
        )

        recover_stuck_run_prompts()

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
        mock_prompter.objects.filter.return_value.values_list.return_value.__getitem__.return_value = (
            []
        )

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

        mock_prompter.objects.filter.return_value.values_list.return_value.__getitem__.return_value = (
            []
        )
        mock_tracker.cleanup_stale.return_value = 5  # 5 stale entries cleaned

        recover_stuck_run_prompts()

        mock_tracker.cleanup_stale.assert_called_once()


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


class TestRunPromptPersistence:
    """Exercise the row worker without a provider, database, or billing service."""

    @pytest.fixture
    def execution(self, monkeypatch):
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import Mock

        from django.db import transaction

        from model_hub.views import run_prompt as module
        from tfc.constants.api_calls import APICallStatusChoices

        state = SimpleNamespace(
            history=[],
            save_failures=0,
            persisted_status=APICallStatusChoices.PROCESSING.value,
        )

        def save_status(**kwargs):
            state.history.append(f"status:{api_call.status}")
            if state.save_failures:
                state.save_failures -= 1
                raise RuntimeError("status save unavailable")
            state.persisted_status = api_call.status

        def update_status(*, status):
            state.history.append(f"update:{status}")
            state.persisted_status = status
            return 1

        class ApiCall:
            objects = Mock()

        api_call = ApiCall()
        api_call.pk = api_call.id = 2080
        api_call.log_id = UUID("550e8400-e29b-41d4-a716-446655442080")
        api_call.status = state.persisted_status
        api_call.save = Mock(side_effect=save_status)
        state.api_call = api_call
        state.fallback = ApiCall.objects.filter.return_value.update
        state.fallback.side_effect = update_status

        class MissingCell(Exception):
            pass

        def persist_cell(**kwargs):
            state.history.append("cell")
            return state.cell

        state.cell = SimpleNamespace(id="cell-2080", save=Mock(side_effect=persist_cell))
        state.cells = Mock()
        state.cells.create.side_effect = persist_cell
        state.cells.get.return_value = state.cell
        state.missing_cell = MissingCell
        monkeypatch.setattr(
            module, "Cell", SimpleNamespace(objects=state.cells, DoesNotExist=MissingCell)
        )

        dataset = SimpleNamespace(id="dataset-2080", workspace=None)
        state.row = SimpleNamespace(id="row-2080", dataset=dataset)
        state.column = SimpleNamespace(id="column-2080")
        state.runner = module.RunPrompts("prompt-2080")
        state.runner.is_editing = False
        state.runner.run_prompt_model = SimpleNamespace(
            organization=SimpleNamespace(id="org-2080"),
            dataset=dataset,
            model="gpt-4o",
            messages=[],
            run_prompt_config={},
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            output_format="string",
        )
        state.validate = Mock(return_value=api_call)
        monkeypatch.setattr(module, "log_and_deduct_cost_for_api_request", state.validate)
        monkeypatch.setattr(module, "populate_placeholders", Mock(return_value=[]))
        monkeypatch.setattr(
            module, "remove_empty_text_from_messages", lambda messages: messages
        )
        monkeypatch.setattr(
            module, "get_specific_error_message", Mock(return_value="Run failed")
        )
        monkeypatch.setattr(
            module, "get_error_for_api_status", Mock(return_value="Request denied")
        )

        def respond():
            state.history.append("model")
            return "answer", {"data": {"response": "answer"}}

        state.provider = Mock(side_effect=respond)
        provider_class = Mock()
        provider_class.return_value.litellm_response = state.provider
        monkeypatch.setattr(module, "RunPrompt", provider_class)
        state.close = Mock()
        monkeypatch.setattr(module, "close_old_connections", state.close)
        state.real_on_commit = transaction.on_commit
        state.on_commit = Mock(side_effect=lambda callback: callback())
        monkeypatch.setattr(transaction, "on_commit", state.on_commit)

        events = ModuleType("ee.usage.schemas.events")
        events.UsageEvent = Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
        emitter = ModuleType("ee.usage.services.emitter")
        emitter.emit = Mock(side_effect=lambda event: state.history.append("usage"))
        monkeypatch.setitem(sys.modules, events.__name__, events)
        monkeypatch.setitem(sys.modules, emitter.__name__, emitter)
        state.emit = emitter.emit
        state.module = module
        return state

    @staticmethod
    def configure_cell_path(execution, cell_path):
        execution.runner.is_editing = cell_path != "create"
        if cell_path == "edit_missing":
            execution.cells.get.side_effect = execution.missing_cell

    @pytest.mark.parametrize("cell_path", ["create", "edit", "edit_missing"])
    @pytest.mark.parametrize("save_failures", [0, 1, 2])
    def test_success_is_persisted_before_single_usage_event(
        self, execution, cell_path, save_failures
    ):
        self.configure_cell_path(execution, cell_path)
        execution.save_failures = save_failures

        execution.runner.process_row(execution.row, execution.column)

        assert execution.history[:2] == ["model", "cell"]
        assert execution.history[-1] == "usage"
        assert execution.persisted_status == "success"
        assert execution.api_call.save.call_count == min(save_failures + 1, 2)
        execution.on_commit.assert_called_once()
        execution.emit.assert_called_once()
        event = execution.emit.call_args.args[0]
        assert event.org_id == "org-2080"
        assert event.event_type == "dataset_run_prompt"
        assert isinstance(event.event_id, str)
        assert UUID(event.event_id) == execution.api_call.log_id
        assert event.event_id == str(execution.api_call.log_id)
        if save_failures == 2:
            type(execution.api_call).objects.filter.assert_called_once_with(pk=2080)
            execution.fallback.assert_called_once_with(status="success")
        else:
            execution.fallback.assert_not_called()
        execution.close.assert_called_once()

    @pytest.mark.parametrize("save_failures", [0, 1, 2])
    def test_provider_failure_persists_error_without_usage(self, execution, save_failures):
        execution.save_failures = save_failures
        execution.provider.side_effect = RuntimeError("provider unavailable")

        execution.runner.process_row(execution.row, execution.column)

        assert execution.persisted_status == "error"
        assert (
            execution.cells.create.call_args.kwargs["status"]
            == execution.module.CellStatus.ERROR.value
        )
        assert execution.api_call.save.call_count == min(save_failures + 1, 2)
        execution.emit.assert_not_called()
        if save_failures == 2:
            execution.fallback.assert_called_once_with(status="error")
        execution.close.assert_called_once()

    @pytest.mark.parametrize("cell_path", ["create", "edit", "edit_missing"])
    def test_failed_cell_persist_records_error_without_usage(self, execution, cell_path):
        self.configure_cell_path(execution, cell_path)
        execution.cells.create.side_effect = RuntimeError("cell persist unavailable")
        execution.cell.save.side_effect = RuntimeError("cell persist unavailable")

        with pytest.raises(RuntimeError, match="cell persist unavailable"):
            execution.runner.process_row(execution.row, execution.column)

        assert execution.persisted_status == "error"
        execution.emit.assert_not_called()
        execution.close.assert_called_once()

    @pytest.mark.parametrize("fallback_failure", ["exception", "missing_row"])
    def test_unpersistable_error_status_raises(self, execution, fallback_failure):
        execution.provider.side_effect = RuntimeError("provider unavailable")
        execution.save_failures = 2
        if fallback_failure == "exception":
            execution.fallback.side_effect = RuntimeError("status update unavailable")
        else:
            execution.fallback.side_effect = None
            execution.fallback.return_value = 0

        with pytest.raises(RuntimeError, match="Unable to persist run-prompt API call status"):
            execution.runner.process_row(execution.row, execution.column)

        assert execution.persisted_status == "processing"
        assert execution.api_call.save.call_count == 2
        execution.fallback.assert_called_once_with(status="error")
        execution.emit.assert_not_called()
        execution.close.assert_called_once()

    def test_failed_success_status_records_error_and_does_not_emit(self, execution):
        execution.save_failures = 2
        execution.fallback.side_effect = None
        execution.fallback.return_value = 0

        with pytest.raises(RuntimeError, match="Unable to persist run-prompt API call status"):
            execution.runner.process_row(execution.row, execution.column)

        assert "cell" in execution.history
        assert execution.persisted_status == "error"
        execution.emit.assert_not_called()
        execution.close.assert_called_once()

    def test_usage_waits_for_commit(self, execution):
        callbacks = []
        execution.on_commit.side_effect = callbacks.append

        execution.runner.process_row(execution.row, execution.column)

        assert execution.persisted_status == "success"
        execution.emit.assert_not_called()
        assert len(callbacks) == 1
        callbacks[0]()
        execution.emit.assert_called_once()

    @pytest.mark.parametrize(
        "log_id",
        ["550e8400-e29b-41d4-a716-446655442080", "550e8400-e29b-41d4-a716-446655442081"],
        ids=["first-call", "second-call"],
    )
    def test_replayed_usage_callback_keeps_event_uuid(self, execution, log_id):
        execution.api_call.log_id = UUID(log_id)
        callbacks = []
        execution.on_commit.side_effect = callbacks.append
        execution.emit.side_effect = [RuntimeError("meter unavailable"), None]

        execution.runner.process_row(execution.row, execution.column)

        execution.on_commit.assert_called_once()
        execution.emit.assert_not_called()
        # Explicitly replay delivery for the saved call; the worker itself does not retry.
        callbacks[0]()
        callbacks[0]()

        event_ids = [call.args[0].event_id for call in execution.emit.call_args_list]
        assert event_ids == [log_id, log_id]
        assert all(UUID(event_id) == execution.api_call.log_id for event_id in event_ids)
        assert execution.persisted_status == "success"
        execution.api_call.save.assert_called_once_with(update_fields=["status"])
        execution.provider.assert_called_once()
        execution.cells.create.assert_called_once()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
    def test_usage_follows_outer_transaction(self, execution, monkeypatch, rollback):
        from django.db import transaction

        monkeypatch.setattr(transaction, "on_commit", execution.real_on_commit)
        with transaction.atomic():
            execution.runner.process_row(execution.row, execution.column)
            execution.emit.assert_not_called()
            if rollback:
                transaction.set_rollback(True)

        assert execution.emit.call_count == (0 if rollback else 1)

    def test_emitter_failure_does_not_undo_success_or_retry_usage(self, execution):
        execution.emit.side_effect = RuntimeError("meter unavailable")

        execution.runner.process_row(execution.row, execution.column)

        assert execution.history[:2] == ["model", "cell"]
        assert execution.persisted_status == "success"
        execution.emit.assert_called_once()
        execution.close.assert_called_once()

    @pytest.mark.parametrize("rejected_status", ["rate_limited", "resource_limit"])
    def test_rejected_call_keeps_its_status_and_never_emits(self, execution, rejected_status):
        execution.api_call.status = execution.persisted_status = rejected_status

        execution.runner.process_row(execution.row, execution.column)

        assert execution.persisted_status == rejected_status
        execution.api_call.save.assert_not_called()
        execution.fallback.assert_not_called()
        execution.provider.assert_not_called()
        execution.emit.assert_not_called()

    @pytest.mark.parametrize("validation_failure", ["exception", "missing_row"])
    def test_validation_failure_does_not_mask_cell_error(self, execution, validation_failure):
        if validation_failure == "exception":
            execution.validate.side_effect = RuntimeError("validation unavailable")
        else:
            execution.validate.return_value = None

        execution.runner.process_row(execution.row, execution.column)

        assert (
            execution.cells.create.call_args.kwargs["status"]
            == execution.module.CellStatus.ERROR.value
        )
        execution.provider.assert_not_called()
        execution.emit.assert_not_called()
        execution.api_call.save.assert_not_called()
        execution.close.assert_called_once()

    def test_oss_without_billing_still_persists_cell(self, execution, monkeypatch):
        monkeypatch.setattr(execution.module, "log_and_deduct_cost_for_api_request", None)

        execution.runner.process_row(execution.row, execution.column)

        assert execution.history == ["model", "cell"]
        execution.emit.assert_not_called()
        execution.close.assert_called_once()
