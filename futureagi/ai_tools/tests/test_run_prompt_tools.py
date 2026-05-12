import uuid
from unittest.mock import MagicMock, patch

import pytest

from ai_tools.registry import registry
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_dataset, make_dataset_with_rows

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def writable_dataset(tool_context, mock_resource_limit):
    """Dataset with 'OTHERS' source columns (writable)."""
    return make_dataset(
        tool_context,
        name="Prompt Dataset",
        columns=[("input", "text"), ("output", "text")],
    )


@pytest.fixture
def populated_dataset(tool_context, mock_resource_limit):
    """Dataset with populated rows."""
    ds, cols, rows = make_dataset_with_rows(
        tool_context,
        name="Populated Prompt Dataset",
        row_data=[
            {"input": "hello", "output": "world"},
            {"input": "foo", "output": "bar"},
            {"input": "baz", "output": "qux"},
        ],
    )
    return ds, cols, rows


@pytest.fixture
def other_org_context(tool_context):
    """Create a second org + tool context for cross-org isolation tests."""
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from ai_tools.base import ToolContext
    from tfc.middleware.workspace_context import set_workspace_context

    other_org = Organization.objects.create(name="Other Org")
    other_user = User.objects.create(
        email="other@example.com",
        organization=other_org,
    )
    ctx = ToolContext(user=other_user, organization=other_org, workspace=None)
    return ctx


@pytest.fixture
def mock_celery():
    """Patch Celery task to prevent real task execution."""
    with patch(
        "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
    ) as mock_task:
        yield mock_task


@pytest.fixture
def mock_run_all_prompts():
    """Patch run_all_prompts_task."""
    with patch(
        "model_hub.views.run_prompt.run_all_prompts_task.apply_async"
    ) as mock_task:
        yield mock_task


# ===================================================================
# ADD_RUN_PROMPT_COLUMN TOOL
# ===================================================================


class TestAddRunPromptColumnTool:
    """Tests for the add_run_prompt_column MCP tool."""

    def test_basic_creation(self, tool_context, writable_dataset, mock_celery):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Summary",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Summarize: {{input}}"}],
                "run": False,
            },
            tool_context,
        )
        assert not result.is_error
        assert "Run Prompt Column Added" in result.content
        assert result.data["name"] == "Summary"

    def test_org_isolation(self, other_org_context, writable_dataset, mock_celery):
        """Verify dataset from org A is not accessible by org B."""
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Hacked",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hack"}],
            },
            other_org_context,
        )
        assert result.is_error
        assert "not found" in result.content.lower()

    def test_empty_name_rejected(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
            },
            tool_context,
        )
        assert result.is_error

    def test_missing_column_reference(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Test",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Process: {{nonexistent}}"}],
                "run": False,
            },
            tool_context,
        )
        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_messages"] is True
        assert "nonexistent" in result.data["missing_columns"]

    def test_duplicate_column_name(self, tool_context, writable_dataset, mock_celery):
        # Create first column
        run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "input",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "run": False,
            },
            tool_context,
        )
        # Try duplicate
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "input",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "run": False,
            },
            tool_context,
        )
        assert result.is_error
        assert "already exists" in result.content

    def test_first_message_not_assistant(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Test",
                "model": "gpt-4o",
                "messages": [{"role": "assistant", "content": "I'm an assistant"}],
            },
            tool_context,
        )
        assert result.is_error

    def test_user_message_empty_content(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Test",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "   "}],
            },
            tool_context,
        )
        assert result.is_error

    def test_invalid_role(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Test",
                "model": "gpt-4o",
                "messages": [{"role": "admin", "content": "test"}],
            },
            tool_context,
        )
        assert result.is_error

    def test_concurrency_max_10(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Test",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "concurrency": 11,
            },
            tool_context,
        )
        assert result.is_error

    def test_temperature_boundary(self, tool_context, writable_dataset, mock_celery):
        # Valid at boundary
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "TempTest",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "temperature": 2.0,
                "run": False,
            },
            tool_context,
        )
        assert not result.is_error

        # Invalid above boundary
        result2 = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "TempTest2",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "temperature": 2.1,
            },
            tool_context,
        )
        assert result2.is_error

    def test_invalid_output_format(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Test",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "output_format": "xml",
            },
            tool_context,
        )
        assert result.is_error

    def test_tts_requires_voice(self, tool_context, writable_dataset):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "TTS Column",
                "model": "tts-1",
                "messages": [{"role": "user", "content": "Say hello"}],
                "model_type": "tts",
            },
            tool_context,
        )
        assert result.is_error

    def test_tts_with_voice_succeeds(self, tool_context, writable_dataset, mock_celery):
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "TTS Column",
                "model": "tts-1",
                "messages": [{"role": "user", "content": "Say hello"}],
                "model_type": "tts",
                "voice": "alloy",
                "run": False,
            },
            tool_context,
        )
        assert not result.is_error

    def test_json_path_variable_reference(
        self, tool_context, writable_dataset, mock_celery
    ):
        """Test that {{column.field}} syntax is properly handled."""
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "JSON Path Test",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Get {{input.name}} value"}],
                "run": False,
            },
            tool_context,
        )
        # Should succeed - 'input' column exists, '.name' is a path within it
        assert not result.is_error


# ===================================================================
# RUN_PROMPT_FOR_ROWS TOOL
# ===================================================================


class TestRunPromptForRowsTool:
    """Tests for the run_prompt_for_rows MCP tool."""

    def _make_run_prompter(self, tool_context, dataset, mock_celery):
        """Helper: create a RunPrompter via the add tool."""
        result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(dataset.id),
                "name": "Test Prompt",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Process: {{input}}"}],
                "run": False,
            },
            tool_context,
        )
        assert not result.is_error
        return result.data["run_prompter_id"]

    def test_run_selected_rows(
        self, tool_context, populated_dataset, mock_celery, mock_run_all_prompts
    ):
        ds, cols, rows = populated_dataset
        rp_id = self._make_run_prompter(tool_context, ds, mock_celery)

        result = run_tool(
            "run_prompt_for_rows",
            {
                "run_prompt_ids": [rp_id],
                "row_ids": [str(rows[0].id), str(rows[1].id)],
            },
            tool_context,
        )
        assert not result.is_error
        assert result.data["rows_queued"] == 2
        mock_run_all_prompts.assert_called_once()

    def test_run_selected_rows_skips_malformed_row_ids(
        self, tool_context, populated_dataset, mock_celery, mock_run_all_prompts
    ):
        ds, cols, rows = populated_dataset
        rp_id = self._make_run_prompter(tool_context, ds, mock_celery)

        result = run_tool(
            "run_prompt_for_rows",
            {
                "run_prompt_ids": [rp_id],
                "row_ids": [str(rows[0].id), "c8f13e2d-d056c3c7fc16"],
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["rows_queued"] == 1
        assert result.data["skipped_invalid_row_ids"] == ["c8f13e2d-d056c3c7fc16"]
        mock_run_all_prompts.assert_called_once()

    def test_malformed_run_prompt_id_returns_candidates(
        self, tool_context, populated_dataset
    ):
        result = run_tool(
            "run_prompt_for_rows",
            {
                "run_prompt_ids": ["not-a-run-prompt-id"],
                "row_ids": [str(populated_dataset[2][0].id)],
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_run_prompt_ids"] is True

    def test_run_all_rows(
        self, tool_context, populated_dataset, mock_celery, mock_run_all_prompts
    ):
        ds, cols, rows = populated_dataset
        rp_id = self._make_run_prompter(tool_context, ds, mock_celery)

        result = run_tool(
            "run_prompt_for_rows",
            {
                "run_prompt_ids": [rp_id],
                "selected_all_rows": True,
            },
            tool_context,
        )
        assert not result.is_error
        assert result.data["rows_queued"] == 3

    def test_missing_row_ids_and_not_select_all(self, tool_context):
        result = run_tool(
            "run_prompt_for_rows",
            {
                "run_prompt_ids": [str(uuid.uuid4())],
            },
            tool_context,
        )
        assert result.is_error

    def test_empty_run_prompt_ids(self, tool_context):
        result = run_tool(
            "run_prompt_for_rows",
            {
                "run_prompt_ids": [],
                "row_ids": [str(uuid.uuid4())],
            },
            tool_context,
        )
        assert result.is_error

    def test_org_isolation(
        self, tool_context, other_org_context, populated_dataset, mock_celery
    ):
        ds, cols, rows = populated_dataset
        rp_id = self._make_run_prompter(tool_context, ds, mock_celery)

        result = run_tool(
            "run_prompt_for_rows",
            {
                "run_prompt_ids": [rp_id],
                "row_ids": [str(rows[0].id)],
            },
            other_org_context,
        )
        assert result.is_error


# ===================================================================
# GET_RUN_PROMPT_COLUMN_CONFIG TOOL
# ===================================================================


class TestGetRunPromptColumnConfigTool:
    """Tests for the get_run_prompt_column_config MCP tool."""

    def test_get_config(self, tool_context, writable_dataset, mock_celery):
        # Create a run prompt column first
        create_result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Config Test",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Test {{input}}"}],
                "temperature": 0.5,
                "concurrency": 3,
                "run": False,
            },
            tool_context,
        )
        assert not create_result.is_error
        column_id = create_result.data["column_id"]

        result = run_tool(
            "get_run_prompt_column_config",
            {"column_id": column_id},
            tool_context,
        )
        assert not result.is_error
        assert result.data["name"] == "Config Test"
        assert result.data["model"] == "gpt-4o"
        assert result.data["temperature"] == 0.5
        assert result.data["concurrency"] == 3

    def test_not_found(self, tool_context):
        result = run_tool(
            "get_run_prompt_column_config",
            {"column_id": str(uuid.uuid4())},
            tool_context,
        )
        assert result.is_error

    def test_org_isolation(
        self, tool_context, other_org_context, writable_dataset, mock_celery
    ):
        create_result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Secret Config",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "run": False,
            },
            tool_context,
        )
        column_id = create_result.data["column_id"]

        result = run_tool(
            "get_run_prompt_column_config",
            {"column_id": column_id},
            other_org_context,
        )
        assert result.is_error


# ===================================================================
# PREVIEW_RUN_PROMPT_COLUMN TOOL
# ===================================================================


class TestPreviewRunPromptColumnTool:
    def test_missing_column_reference_returns_available_columns(
        self, tool_context, writable_dataset
    ):
        result = run_tool(
            "preview_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "messages": [
                    {
                        "role": "user",
                        "content": "Input: {{missing_input}}\nOutput: {{missing_output}}",
                    }
                ],
                "first_n_rows": 1,
            },
            tool_context,
        )

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_messages"] is True
        assert result.data["missing_columns"] == ["missing_input", "missing_output"]
        assert {column["name"] for column in result.data["available_columns"]} == {
            "input",
            "output",
        }


# ===================================================================
# EDIT_RUN_PROMPT_COLUMN TOOL
# ===================================================================


class TestEditRunPromptColumnTool:
    """Tests for the edit_run_prompt_column MCP tool."""

    def test_edit_name_and_model(self, tool_context, writable_dataset, mock_celery):
        create_result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Original",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "run": False,
            },
            tool_context,
        )
        column_id = create_result.data["column_id"]

        result = run_tool(
            "edit_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "column_id": column_id,
                "name": "Updated",
                "model": "gpt-4o-mini",
                "run": False,
            },
            tool_context,
        )
        assert not result.is_error
        assert result.data["name"] == "Updated"
        assert result.data["model"] == "gpt-4o-mini"

    def test_edit_nonexistent_column(self, tool_context, writable_dataset):
        result = run_tool(
            "edit_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "column_id": str(uuid.uuid4()),
                "name": "Test",
            },
            tool_context,
        )
        assert result.is_error

    def test_edit_non_run_prompt_column(self, tool_context, writable_dataset):
        """Editing a regular column (not run-prompt source) should fail."""
        from model_hub.models.develop_dataset import Column

        col = Column.objects.filter(dataset=writable_dataset, deleted=False).first()

        result = run_tool(
            "edit_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "column_id": str(col.id),
                "name": "Test",
            },
            tool_context,
        )
        assert result.is_error
        assert "not a run-prompt column" in result.content

    def test_org_isolation(
        self, tool_context, other_org_context, writable_dataset, mock_celery
    ):
        create_result = run_tool(
            "add_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "name": "Secret",
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "run": False,
            },
            tool_context,
        )

        result = run_tool(
            "edit_run_prompt_column",
            {
                "dataset_id": str(writable_dataset.id),
                "column_id": create_result.data["column_id"],
                "name": "Hacked",
            },
            other_org_context,
        )
        assert result.is_error
