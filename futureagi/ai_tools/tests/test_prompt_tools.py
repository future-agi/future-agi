import uuid

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_prompt_template, make_prompt_version

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

BASIC_PROMPT_CONFIG = [
    {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello {{name}}"},
        ],
        "model": "gpt-4o",
        "configuration": {"temperature": 0.7, "max_tokens": 1000},
    }
]


@pytest.fixture
def prompt_template(tool_context):
    return make_prompt_template(tool_context)


@pytest.fixture
def prompt_version(tool_context, prompt_template):
    return make_prompt_version(tool_context, template=prompt_template)


# ===================================================================
# READ TOOLS
# ===================================================================


class TestListPromptTemplatesTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_prompt_templates", {}, tool_context)

        assert not result.is_error
        assert "Prompt Templates (0)" in result.content
        assert result.data["total"] == 0

    def test_list_with_template(self, tool_context, prompt_template):
        result = run_tool("list_prompt_templates", {}, tool_context)

        assert not result.is_error
        assert result.data["total"] == 1
        assert "Test Prompt" in result.content

    def test_list_search(self, tool_context, prompt_template):
        result = run_tool(
            "list_prompt_templates",
            {"search": "Test"},
            tool_context,
        )
        assert result.data["total"] == 1

        result = run_tool(
            "list_prompt_templates",
            {"search": "nonexistent"},
            tool_context,
        )
        assert result.data["total"] == 0


class TestCreatePromptSimulationTool:
    def test_missing_name_returns_needs_input(self, tool_context):
        result = run_tool("create_prompt_simulation", {}, tool_context)

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_name"] is True


class TestGetPromptTemplateTool:
    def test_get_existing(self, tool_context, prompt_template):
        result = run_tool(
            "get_prompt_template",
            {"template_id": str(prompt_template.id)},
            tool_context,
        )

        assert not result.is_error
        assert "Test Prompt" in result.content

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_prompt_template",
            {"template_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error


class TestListPromptVersionsTool:
    def test_list_versions(self, tool_context, prompt_template, prompt_version):
        result = run_tool(
            "list_prompt_versions",
            {"template_id": str(prompt_template.id)},
            tool_context,
        )

        assert not result.is_error
        assert "v1" in result.content

    def test_list_versions_nonexistent_template(self, tool_context):
        result = run_tool(
            "list_prompt_versions",
            {"template_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error


class TestGetPromptVersionTool:
    def test_get_existing(self, tool_context, prompt_template, prompt_version):
        result = run_tool(
            "get_prompt_version",
            {
                "template_id": str(prompt_template.id),
                "version_id": str(prompt_version.id),
            },
            tool_context,
        )

        assert not result.is_error
        assert "v1" in result.content


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreatePromptTemplateTool:
    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_prompt_template",
            {"name": "New Prompt"},
            tool_context,
        )

        assert not result.is_error
        assert "Prompt Template Created" in result.content
        assert result.data["name"] == "New Prompt"
        assert result.data["version_id"]  # v1 created automatically
        assert result.data["version"] == "v1"

    def test_create_with_config(self, tool_context):
        result = run_tool(
            "create_prompt_template",
            {
                "name": "Configured Prompt",
                "prompt_config": BASIC_PROMPT_CONFIG,
            },
            tool_context,
        )

        assert not result.is_error
        assert "Configured Prompt" in result.content
        # Variables should be extracted from {{name}}
        assert "name" in result.data.get("variable_names", [])

    def test_create_duplicate_name(self, tool_context):
        run_tool("create_prompt_template", {"name": "Dup Prompt"}, tool_context)
        result = run_tool(
            "create_prompt_template", {"name": "Dup Prompt"}, tool_context
        )

        assert result.is_error
        assert "already exists" in result.content

    def test_create_with_description(self, tool_context):
        result = run_tool(
            "create_prompt_template",
            {"name": "Described Prompt", "description": "A test prompt template"},
            tool_context,
        )

        assert not result.is_error

    def test_create_with_model(self, tool_context):
        result = run_tool(
            "create_prompt_template",
            {"name": "Model Prompt", "model": "claude-3-5-sonnet"},
            tool_context,
        )

        assert not result.is_error

    def test_create_normalizes_null_variable_values(self, tool_context):
        result = run_tool(
            "create_prompt_template",
            {
                "name": "Null Variable Values Prompt",
                "variable_values": {"customer_message": None, "account_tier": None},
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "Null Variable Values Prompt"


class TestCreatePromptVersionTool:
    def test_create_new_version(self, tool_context, prompt_template):
        result = run_tool(
            "create_prompt_version",
            {
                "template_id": str(prompt_template.id),
                "prompt_config": BASIC_PROMPT_CONFIG,
            },
            tool_context,
        )

        assert not result.is_error
        assert "Prompt Version Created" in result.content
        assert result.data["version_id"]

    def test_create_version_with_commit_message(self, tool_context, prompt_template):
        result = run_tool(
            "create_prompt_version",
            {
                "template_id": str(prompt_template.id),
                "prompt_config": BASIC_PROMPT_CONFIG,
                "commit_message": "Updated system prompt",
            },
            tool_context,
        )

        assert not result.is_error

    def test_create_version_set_default(self, tool_context, prompt_template):
        result = run_tool(
            "create_prompt_version",
            {
                "template_id": str(prompt_template.id),
                "prompt_config": BASIC_PROMPT_CONFIG,
                "set_default": True,
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["is_default"] is True

    def test_create_version_accepts_dict_config_and_scalar_variable_values(
        self, tool_context, prompt_template
    ):
        result = run_tool(
            "create_prompt_version",
            {
                "template_id": str(prompt_template.id),
                "prompt_config": {
                    "messages": [{"role": "user", "content": "Hello {{name}}"}],
                    "configuration": {"temperature": 0.2},
                },
                "model": ["claude-sonnet-4-6"],
                "variable_values": {"name": "Alice"},
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["version_id"]

    def test_create_version_nonexistent_template(self, tool_context):
        result = run_tool(
            "create_prompt_version",
            {
                "template_id": str(uuid.uuid4()),
                "prompt_config": BASIC_PROMPT_CONFIG,
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_template_id"] is True

    def test_version_numbers_increment(self, tool_context, prompt_template):
        """Successive versions should get incrementing version numbers."""
        r1 = run_tool(
            "create_prompt_version",
            {
                "template_id": str(prompt_template.id),
                "prompt_config": BASIC_PROMPT_CONFIG,
            },
            tool_context,
        )

        r2 = run_tool(
            "create_prompt_version",
            {
                "template_id": str(prompt_template.id),
                "prompt_config": BASIC_PROMPT_CONFIG,
            },
            tool_context,
        )

        assert not r1.is_error
        assert not r2.is_error
        # Versions should be different
        assert r1.data["version_id"] != r2.data["version_id"]


class TestDeletePromptTemplateTool:
    def test_delete_existing(self, tool_context, prompt_template, prompt_version):
        result = run_tool(
            "delete_prompt_template",
            {"template_id": str(prompt_template.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "Test Prompt"
        assert result.data["versions_deleted"] >= 1

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_prompt_template",
            {"template_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error

    def test_delete_already_deleted(
        self, tool_context, prompt_template, prompt_version
    ):
        run_tool(
            "delete_prompt_template",
            {"template_id": str(prompt_template.id)},
            tool_context,
        )
        result = run_tool(
            "delete_prompt_template",
            {"template_id": str(prompt_template.id)},
            tool_context,
        )

        assert result.is_error

    def test_deleted_template_not_in_list(
        self, tool_context, prompt_template, prompt_version
    ):
        run_tool(
            "delete_prompt_template",
            {"template_id": str(prompt_template.id)},
            tool_context,
        )

        result = run_tool("list_prompt_templates", {}, tool_context)
        assert result.data["total"] == 0
