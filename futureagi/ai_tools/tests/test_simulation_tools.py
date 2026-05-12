import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_agent_definition, make_scenario

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_definition(tool_context):
    return make_agent_definition(tool_context)


@pytest.fixture
def mock_temporal_scenario():
    """Mock Temporal for scenario creation (dataset scenarios start workflows)."""
    with patch(
        "tfc.temporal.scenarios.start_scenario_workflow",
        return_value="mock-scenario-workflow",
    ):
        yield


# ===================================================================
# READ TOOLS
# ===================================================================


class TestListPersonasTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_personas", {}, tool_context)
        assert not result.is_error

    def test_list_with_persona(self, tool_context):
        from simulate.models.persona import Persona

        Persona.objects.create(
            name="Test Persona",
            persona_type="workspace",
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )

        result = run_tool("list_personas", {}, tool_context)
        assert not result.is_error
        assert "Test Persona" in result.content


class TestListScenariosTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_scenarios", {}, tool_context)
        assert not result.is_error


class TestListAgentsTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_agents", {}, tool_context)
        assert not result.is_error

    def test_list_with_agent(self, tool_context):
        from simulate.models.agent_definition import AgentDefinition

        AgentDefinition.objects.create(
            agent_name="Listed Agent",
            agent_type="voice",
            languages=["en"],
            inbound=True,
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )
        result = run_tool("list_agents", {}, tool_context)
        assert not result.is_error
        assert "Listed Agent" in result.content


class TestUpdateScenarioTool:
    def test_missing_scenario_returns_candidates(self, tool_context):
        result = run_tool(
            "update_scenario",
            {"scenario_id": str(uuid.uuid4()), "name": "Renamed scenario"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_scenario_id"] is True


class TestAddScenarioRowsTool:
    def test_missing_scenario_returns_candidates(self, tool_context):
        result = run_tool(
            "add_scenario_rows",
            {
                "scenario_id": str(uuid.uuid4()),
                "num_rows": 10,
                "description": "Generate support escalation rows.",
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_scenario_id"] is True
        assert "Scenario Not Found" in result.content

    def test_malformed_scenario_id_returns_candidates(self, tool_context):
        result = run_tool(
            "add_scenario_rows",
            {
                "scenario_id": "5aa13d82-0081-46a1-9ead-c9f9dd2",
                "num_rows": 25,
                "description": "Generate support escalation rows.",
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_scenario_id"] is True
        assert "Scenario Not Found" in result.content


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreatePersonaTool:
    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_persona",
            {"name": "New Persona", "description": "A test persona"},
            tool_context,
        )

        assert not result.is_error
        assert "Persona Created" in result.content
        assert result.data["name"] == "New Persona"

    def test_create_with_traits(self, tool_context):
        result = run_tool(
            "create_persona",
            {
                "name": "Detailed Persona",
                "description": "A detailed persona for testing",
                "gender": ["male"],
                "age_group": ["25-32"],
                "personality": ["Friendly and cooperative"],
                "tone": "casual",
                "verbosity": "balanced",
            },
            tool_context,
        )

        assert not result.is_error

    def test_create_with_json_demographics_and_personality(self, tool_context):
        result = run_tool(
            "create_persona",
            {
                "name": "JSON Persona",
                "description": "Persona generated from free-form analysis",
                "demographics": '{"age_range": "28-45", "occupation": "Professional / Decision-Maker", "tech_savviness": "high"}',
                "personality": '{"traits": ["decisive", "goal-oriented"], "motivation": "Complete a task quickly", "patience_level": "low"}',
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "JSON Persona"

    def test_create_with_trace_derived_freeform_fields(self, tool_context):
        result = run_tool(
            "create_persona",
            {
                "name": "Trace Derived Persona",
                "description": "Persona inferred from traces",
                "age": "45",
                "gender": "Female",
                "location": "Chicago, IL",
                "occupation": "Small Business Owner",
                "traits": '["frustrated", "confrontational", "low patience"]',
                "text_style": "assertive",
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "Trace Derived Persona"

    def test_create_duplicate_name(self, tool_context):
        run_tool(
            "create_persona",
            {"name": "Dup Persona", "description": "Test"},
            tool_context,
        )
        result = run_tool(
            "create_persona",
            {"name": "Dup Persona", "description": "Test"},
            tool_context,
        )

        assert not result.is_error
        assert "Already Exists" in result.content
        assert result.data["already_exists"] is True

    def test_create_duplicate_case_insensitive(self, tool_context):
        run_tool(
            "create_persona", {"name": "Case Test", "description": "Test"}, tool_context
        )
        result = run_tool(
            "create_persona", {"name": "case test", "description": "Test"}, tool_context
        )

        assert not result.is_error
        assert result.data["already_exists"] is True


class TestCreateAgentDefinitionTool:
    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_agent_definition",
            {"agent_name": "New Agent", "language": "en"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "New Agent"
        assert result.data["type"] == "text"

    def test_create_with_description(self, tool_context):
        result = run_tool(
            "create_agent_definition",
            {"agent_name": "Agent Two", "description": "Test agent", "language": "en"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "Agent Two"


class TestCreateScenarioTool:
    def test_persona_descriptions_are_folded_into_generation_instruction(
        self, tool_context, agent_definition
    ):
        scenario_id = uuid.uuid4()
        created_scenario = SimpleNamespace(
            id=scenario_id,
            name="Regression Scenario",
            scenario_type="graph",
            source_type="agent_definition",
            status="draft",
            created_at=timezone.now(),
        )

        with patch("simulate.services.scenario_service.create_scenario") as create:
            create.return_value = {
                "scenario": created_scenario,
                "workflow_started": False,
                "id": str(scenario_id),
                "name": created_scenario.name,
                "type": created_scenario.scenario_type,
                "agent_id": agent_definition.id,
                "status": created_scenario.status,
            }

            result = run_tool(
                "create_scenario",
                {
                    "name": "Regression Scenario",
                    "agent_id": str(agent_definition.id),
                    "personas": [
                        "Frustrated returning customer",
                        "Power user who catches factual inaccuracies",
                    ],
                },
                tool_context,
            )

        assert not result.is_error
        kwargs = create.call_args.kwargs
        assert kwargs["personas"] is None
        assert kwargs["add_persona_automatically"] is True
        assert "Frustrated returning customer" in kwargs["custom_instruction"]


class TestUpdateAgentDefinitionTool:
    def test_no_update_fields_returns_needs_input(
        self, tool_context, agent_definition
    ):
        result = run_tool(
            "update_agent_definition",
            {"agent_id": str(agent_definition.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_update_fields"] is True


class TestDeletePersonaTool:
    def test_delete_existing(self, tool_context):
        create_result = run_tool(
            "create_persona",
            {"name": "To Delete Persona", "description": "A persona to delete"},
            tool_context,
        )
        persona_id = create_result.data["id"]

        result = run_tool(
            "delete_persona",
            {"persona_id": persona_id},
            tool_context,
        )

        assert not result.is_error

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_persona",
            {"persona_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error


class TestDeleteAgentDefinitionTool:
    def test_delete_existing(self, tool_context):
        from simulate.models.agent_definition import AgentDefinition

        agent = AgentDefinition.objects.create(
            agent_name="To Delete Agent",
            agent_type="voice",
            languages=["en"],
            inbound=True,
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )
        result = run_tool(
            "delete_agent_definition",
            {"agent_id": str(agent.id)},
            tool_context,
        )

        assert not result.is_error

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_agent_definition",
            {"agent_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error
