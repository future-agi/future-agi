"""run-prompt terminal status persistence and usage-event gating (issue #2080).

Two defects on the run-prompt execution path in
``futureagi/model_hub/views/run_prompt.py``:

1. Terminal status can be lost — if the save that records a cell's terminal
   status fails, the row is left non-terminal with nothing retrying it.
2. Usage events can be emitted for unpersisted results — the usage/billing
   event used to be emitted before the cell result was persisted.

These tests pin the fixed contract:

- terminal status is always persisted (retry + queryset-level fallback,
  failing loudly if even the fallback cannot record it);
- the usage event is emitted only after a successful persist of a success
  (PASS) cell — never for failed calls, and never for work whose result was
  not saved.
"""

from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.views.run_prompt import RunPrompts, _persist_cell_result


def _make_task(edit_mode=False):
    """Build a RunPrompts instance with mocked model attributes, no DB."""
    task = RunPrompts.__new__(RunPrompts)
    task.run_prompt_id = "prompt-123"
    task.tools_config = []
    task.run_prompt_model = MagicMock()
    task.run_prompt_model.organization.id = "org-1"
    task.run_prompt_model.dataset = MagicMock()
    task.run_prompt_model.dataset.id = "ds-1"
    task.run_prompt_model.model = "gpt-4o"
    task.run_prompt_model.messages = [{"role": "user", "content": "hi"}]
    task.run_prompt_model.temperature = 0.7
    task.run_prompt_model.frequency_penalty = 0.0
    task.run_prompt_model.presence_penalty = 0.0
    task.run_prompt_model.top_p = 1.0
    task.run_prompt_model.response_format = None
    task.run_prompt_model.tool_choice = None
    task.run_prompt_model.output_format = "string"
    task.run_prompt_model.run_prompt_config = {}
    task.run_prompt_model.concurrency = 4
    task.is_editing = edit_mode
    return task


def _make_row_column():
    row = MagicMock()
    row.id = "row-1"
    column = MagicMock()
    column.id = "col-1"
    return row, column


@pytest.fixture
def cell_objects():
    """Mock Cell.objects for the duration of a test."""
    with patch("model_hub.views.run_prompt.Cell.objects") as mock_objects:
        yield mock_objects


@pytest.fixture
def process_row_env(cell_objects):
    """Patch every external call site used by RunPrompts.process_row."""
    with (
        patch(
            "model_hub.views.run_prompt.log_and_deduct_cost_for_api_request",
            new=None,
        ),
        patch("model_hub.views.run_prompt.populate_placeholders") as mock_populate,
        patch(
            "model_hub.views.run_prompt.remove_empty_text_from_messages"
        ) as mock_remove_empty,
        patch("model_hub.views.run_prompt.RunPrompt") as mock_run_prompt,
        patch(
            "model_hub.views.run_prompt.close_old_connections"
        ) as mock_close,
        patch("ee.usage.services.emitter.emit") as mock_emit,
    ):
        yield {
            "cell_objects": cell_objects,
            "populate_placeholders": mock_populate,
            "remove_empty_text_from_messages": mock_remove_empty,
            "run_prompt": mock_run_prompt,
            "close_old_connections": mock_close,
            "emit": mock_emit,
        }


class TestUsageEventGating:
    """The usage/billing event fires only after a persisted success cell."""

    def test_usage_event_emitted_after_cell_persist_on_success(
        self, process_row_env
    ):
        env = process_row_env
        env["run_prompt"].return_value.litellm_response.return_value = (
            "generated",
            {
                "metadata": {
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7},
                    "response_time": 0.5,
                }
            },
        )
        task = _make_task()
        row, column = _make_row_column()

        events = []
        env["cell_objects"].create.side_effect = (
            lambda **kwargs: events.append("create") or MagicMock()
        )
        env["emit"].side_effect = lambda *a, **kw: events.append("emit")

        task.process_row(row, column)

        # Emitted exactly once, and strictly AFTER the cell was persisted.
        env["emit"].assert_called_once()
        assert events == ["create", "emit"]
        create_kwargs = env["cell_objects"].create.call_args[1]
        assert create_kwargs["status"] == CellStatus.PASS.value
        assert create_kwargs["prompt_tokens"] == 5
        assert create_kwargs["completion_tokens"] == 7

    def test_no_usage_event_on_failed_llm_call(self, process_row_env):
        env = process_row_env
        env["run_prompt"].return_value.litellm_response.side_effect = Exception(
            "provider error"
        )
        task = _make_task()
        row, column = _make_row_column()

        task.process_row(row, column)

        # The failed call still persists a terminal ERROR cell, but is never
        # billed (issue #2080: no usage event for failed work).
        env["emit"].assert_not_called()
        create_kwargs = env["cell_objects"].create.call_args[1]
        assert create_kwargs["status"] == CellStatus.ERROR.value

    def test_no_usage_event_when_persist_fails_and_status_fallback_applied(
        self, process_row_env
    ):
        env = process_row_env
        env["run_prompt"].return_value.litellm_response.return_value = (
            "generated",
            {"metadata": {"usage": {}, "response_time": 0.5}},
        )
        # The full persist fails on both attempts; only the queryset-level
        # status fallback succeeds.
        env["cell_objects"].create.side_effect = [
            Exception("db down"),
            Exception("db down"),
        ]
        env["cell_objects"].filter.return_value.update.return_value = 1
        task = _make_task()
        row, column = _make_row_column()

        task.process_row(row, column)

        # The terminal status was recorded via the fallback, but the result
        # was never saved — so no usage event (issue #2080).
        env["emit"].assert_not_called()
        update_kwargs = env["cell_objects"].filter.return_value.update.call_args[1]
        assert update_kwargs["status"] == CellStatus.PASS.value

    def test_raises_loudly_when_terminal_status_cannot_be_recorded(
        self, process_row_env
    ):
        env = process_row_env
        env["run_prompt"].return_value.litellm_response.return_value = (
            "generated",
            {"metadata": {"usage": {}, "response_time": 0.5}},
        )
        # Full persist fails twice AND the queryset fallback fails: the row
        # must not be left silently non-terminal (issue #2080).
        env["cell_objects"].create.side_effect = Exception("db down")
        env["cell_objects"].filter.return_value.update.side_effect = Exception(
            "fallback failed"
        )
        task = _make_task()
        row, column = _make_row_column()

        with pytest.raises(Exception, match="fallback failed"):
            task.process_row(row, column)
        env["emit"].assert_not_called()


class TestCellPersistTerminalStatus:
    """The cell persist helper always reaches a terminal status."""

    def test_persists_on_first_attempt(self, cell_objects):
        persisted = _persist_cell_result(
            dataset=MagicMock(),
            column=MagicMock(),
            row=MagicMock(),
            value="ok",
            value_info=None,
            status=CellStatus.PASS.value,
            edit_mode=False,
        )
        assert persisted is True
        cell_objects.create.assert_called_once()
        cell_objects.filter.return_value.update.assert_not_called()

    def test_retries_then_succeeds(self, cell_objects):
        cell_objects.create.side_effect = [
            Exception("transient"),
            MagicMock(),
        ]
        persisted = _persist_cell_result(
            dataset=MagicMock(),
            column=MagicMock(),
            row=MagicMock(),
            value="ok",
            value_info=None,
            status=CellStatus.PASS.value,
            edit_mode=False,
        )
        assert persisted is True
        assert cell_objects.create.call_count == 2
        cell_objects.filter.return_value.update.assert_not_called()

    def test_falls_back_to_status_only_update(self, cell_objects):
        cell_objects.create.side_effect = [
            Exception("db down"),
            Exception("db down"),
        ]
        cell_objects.filter.return_value.update.return_value = 1
        persisted = _persist_cell_result(
            dataset=MagicMock(),
            column=MagicMock(),
            row=MagicMock(),
            value="ok",
            value_info=None,
            status=CellStatus.ERROR.value,
            edit_mode=False,
        )
        assert persisted is False
        update_kwargs = cell_objects.filter.return_value.update.call_args[1]
        assert update_kwargs["status"] == CellStatus.ERROR.value

    def test_raises_when_even_fallback_fails(self, cell_objects):
        cell_objects.create.side_effect = Exception("db down")
        cell_objects.filter.return_value.update.side_effect = Exception(
            "fallback failed"
        )
        with pytest.raises(Exception, match="fallback failed"):
            _persist_cell_result(
                dataset=MagicMock(),
                column=MagicMock(),
                row=MagicMock(),
                value="ok",
                value_info=None,
                status=CellStatus.ERROR.value,
                edit_mode=False,
            )

    def test_edit_mode_updates_existing_cell(self, cell_objects):
        existing = MagicMock()
        cell_objects.get.return_value = existing
        persisted = _persist_cell_result(
            dataset=MagicMock(),
            column=MagicMock(),
            row=MagicMock(),
            value="updated",
            value_info={"reason": "x"},
            status=CellStatus.PASS.value,
            edit_mode=True,
        )
        assert persisted is True
        existing.save.assert_called_once()
        assert existing.status == CellStatus.PASS.value
        assert existing.value == "updated"
        cell_objects.create.assert_not_called()

    def test_edit_mode_creates_when_cell_missing(self, cell_objects):
        cell_objects.get.side_effect = Cell.DoesNotExist
        persisted = _persist_cell_result(
            dataset=MagicMock(),
            column=MagicMock(),
            row=MagicMock(),
            value="new",
            value_info=None,
            status=CellStatus.PASS.value,
            edit_mode=True,
        )
        assert persisted is True
        cell_objects.create.assert_called_once()


class TestRunPromptTerminalFailure:
    """The run_prompter FAILED status is persisted or the failure is loud."""

    @patch.object(RunPrompts, "load_run_prompt_id")
    @patch("model_hub.views.run_prompt.create_run_prompt_column")
    @patch("model_hub.views.run_prompt.Dataset.objects")
    @patch("model_hub.views.run_prompt.Row.objects")
    @patch("model_hub.views.run_prompt.RunPrompter.objects")
    def test_failed_status_persist_failure_raises_loudly(
        self,
        mock_prompter_objects,
        mock_row_objects,
        mock_dataset_objects,
        mock_create_column,
        mock_load,
    ):
        """A FAILED persist that cannot be recorded must not be swallowed."""
        from django.utils import timezone

        start_updated_at = timezone.now()
        task = RunPrompts.__new__(RunPrompts)
        task.run_prompt_id = "prompt-123"
        task.tools_config = []
        task.run_prompt_model = MagicMock()
        task.run_prompt_model.updated_at = start_updated_at
        task.run_prompt_model.dataset.id = "ds-1"
        task.run_prompt_model.name = "test prompt"
        task.run_prompt_model.output_format = "string"
        task.run_prompt_model.response_format = None
        task.run_prompt_model.concurrency = 1

        dataset_mock = MagicMock()
        dataset_mock.column_order = []
        mock_dataset_objects.filter.return_value.get.return_value = dataset_mock
        mock_create_column.return_value = (MagicMock(), False)

        row = MagicMock()
        row.id = "row-1"
        mock_row_objects.filter.return_value.order_by.return_value = [row]

        # The queued work itself fails...
        with patch.object(
            RunPrompts, "process_row", side_effect=Exception("row boom")
        ):
            # ...and the FAILED persist also fails.
            qs = MagicMock()
            qs.values.return_value.first.return_value = {
                "status": StatusType.RUNNING.value,
                "updated_at": start_updated_at,
            }
            qs.update.side_effect = Exception("terminal update failed")
            mock_prompter_objects.filter.return_value = qs

            with pytest.raises(Exception, match="terminal update failed") as exc_info:
                task.run_prompt(edit_mode=False)

        # The original row failure is preserved as the cause of the loud raise.
        assert str(exc_info.value.__cause__) == "row boom"
