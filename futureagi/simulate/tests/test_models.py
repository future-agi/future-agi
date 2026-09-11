"""
Unit tests for Scenarios models in the simulate app.

Tests cover:
- Scenarios model: Creation, validation, soft delete
- SimulatorAgent model: Creation, validators
- ScenarioGraph model: Creation, properties
- NodeType class: Type validation utilities
"""

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator

from model_hub.models.choices import StatusType
from simulate.models import Scenarios
from simulate.models.scenario_graph import NodeType, ScenarioGraph
from simulate.models.simulator_agent import SimulatorAgent

# ============================================================================
# Scenarios Model Tests
# ============================================================================


@pytest.mark.unit
class TestScenariosModel:
    """Tests for Scenarios model."""

    def test_scenario_creation(self, db, organization, workspace):
        """Basic scenario model creation should work."""
        scenario = Scenarios.objects.create(
            name="Test Scenario",
            description="A test description",
            source="Test source content",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
        )

        assert scenario.id is not None
        assert scenario.name == "Test Scenario"
        assert scenario.description == "A test description"
        assert scenario.source == "Test source content"
        assert scenario.scenario_type == Scenarios.ScenarioTypes.DATASET
        assert scenario.organization == organization
        assert scenario.workspace == workspace
        assert scenario.deleted is False
        assert scenario.status == StatusType.RUNNING.value

    def test_scenario_creation_with_defaults(self, db, organization, workspace):
        """Scenario creation with default values."""
        scenario = Scenarios.objects.create(
            name="Minimal Scenario",
            source="Minimal source",
            organization=organization,
            workspace=workspace,
        )

        # Default scenario type should be DATASET
        assert scenario.scenario_type == Scenarios.ScenarioTypes.DATASET
        # Default status should be RUNNING
        assert scenario.status == StatusType.RUNNING.value
        # Default metadata should be empty dict
        assert scenario.metadata == {}
        # Description can be null/blank
        assert scenario.description in [None, ""]

    def test_scenario_types_choices(self):
        """Verify ScenarioTypes enum values."""
        assert Scenarios.ScenarioTypes.GRAPH == "graph"
        assert Scenarios.ScenarioTypes.SCRIPT == "script"
        assert Scenarios.ScenarioTypes.DATASET == "dataset"

        # Verify choices are properly defined
        choices = Scenarios.ScenarioTypes.choices
        assert ("graph", "Graph") in choices
        assert ("script", "Script") in choices
        assert ("dataset", "Dataset") in choices
        assert len(choices) == 3

    def test_scenario_clean_empty_name(self, db, organization, workspace):
        """Model validation should reject empty or whitespace-only name."""
        scenario = Scenarios(
            name="   ",  # Whitespace only
            source="Valid source",
            organization=organization,
            workspace=workspace,
        )

        with pytest.raises(ValidationError) as exc_info:
            scenario.clean()

        assert "name" in exc_info.value.message_dict
        assert "empty" in str(exc_info.value.message_dict["name"][0]).lower()

    def test_scenario_clean_empty_source(self, db, organization, workspace):
        """Model validation should reject empty or whitespace-only source."""
        scenario = Scenarios(
            name="Valid Name",
            source="   ",  # Whitespace only
            organization=organization,
            workspace=workspace,
        )

        with pytest.raises(ValidationError) as exc_info:
            scenario.clean()

        assert "source" in exc_info.value.message_dict
        assert "empty" in str(exc_info.value.message_dict["source"][0]).lower()

    def test_scenario_soft_delete(self, db, organization, workspace):
        """Soft delete should mark scenario as deleted."""
        scenario = Scenarios.objects.create(
            name="To Be Deleted",
            source="Delete me",
            organization=organization,
            workspace=workspace,
        )

        scenario_id = scenario.id
        assert scenario.deleted is False
        assert scenario.deleted_at is None

        # Perform soft delete
        scenario.delete()

        # Verify soft delete worked
        assert scenario.deleted is True
        assert scenario.deleted_at is not None

        # Object should not appear in default queryset
        assert not Scenarios.objects.filter(id=scenario_id).exists()

        # But should appear in all_objects queryset
        deleted_scenario = Scenarios.all_objects.get(id=scenario_id)
        assert deleted_scenario.deleted is True

    def test_scenario_str_representation(self, db, organization, workspace):
        """Test string representation of scenario."""
        scenario = Scenarios.objects.create(
            name="My Test Scenario",
            source="Source content",
            organization=organization,
            workspace=workspace,
        )

        assert str(scenario) == "My Test Scenario"

    def test_scenario_with_different_types(self, db, organization, workspace):
        """Scenarios can be created with different types."""
        graph_scenario = Scenarios.objects.create(
            name="Graph Scenario",
            source="Graph source",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
        )
        assert graph_scenario.scenario_type == Scenarios.ScenarioTypes.GRAPH

        script_scenario = Scenarios.objects.create(
            name="Script Scenario",
            source="Script source",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=organization,
            workspace=workspace,
        )
        assert script_scenario.scenario_type == Scenarios.ScenarioTypes.SCRIPT

        dataset_scenario = Scenarios.objects.create(
            name="Dataset Scenario",
            source="Dataset source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
        )
        assert dataset_scenario.scenario_type == Scenarios.ScenarioTypes.DATASET

    def test_scenario_with_metadata(self, db, organization, workspace):
        """Scenario can store custom metadata."""
        metadata = {
            "version": "1.0",
            "tags": ["test", "simulation"],
            "custom_field": {"nested": "value"},
        }

        scenario = Scenarios.objects.create(
            name="Scenario with Metadata",
            source="Source",
            metadata=metadata,
            organization=organization,
            workspace=workspace,
        )

        assert scenario.metadata == metadata
        assert scenario.metadata["version"] == "1.0"
        assert "test" in scenario.metadata["tags"]

    def test_scenario_status_choices(self, db, organization, workspace):
        """Scenario status can be set to valid StatusType values."""
        scenario = Scenarios.objects.create(
            name="Status Test",
            source="Source",
            status=StatusType.COMPLETED.value,
            organization=organization,
            workspace=workspace,
        )
        assert scenario.status == StatusType.COMPLETED.value

        scenario.status = StatusType.FAILED.value
        scenario.save()
        assert scenario.status == StatusType.FAILED.value


# ============================================================================
# SimulatorAgent Model Tests
# ============================================================================


@pytest.mark.unit
class TestSimulatorAgentModel:
    """Tests for SimulatorAgent model."""

    def test_simulator_agent_creation(self, db, organization, workspace):
        """Basic simulator agent creation should work."""
        agent = SimulatorAgent.objects.create(
            name="Test Agent",
            prompt="You are a helpful test assistant.",
            voice_provider="elevenlabs",
            voice_name="adam",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )

        assert agent.id is not None
        assert agent.name == "Test Agent"
        assert agent.prompt == "You are a helpful test assistant."
        assert agent.voice_provider == "elevenlabs"
        assert agent.voice_name == "adam"
        assert agent.model == "gpt-4"
        assert agent.deleted is False

    def test_simulator_agent_defaults(self, db, organization, workspace):
        """Simulator agent should have correct default values."""
        agent = SimulatorAgent.objects.create(
            name="Minimal Agent",
            prompt="Test prompt",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )

        assert agent.interrupt_sensitivity == 0.5
        assert agent.conversation_speed == 1.0
        assert agent.finished_speaking_sensitivity == 0.5
        assert agent.llm_temperature == 0.7
        assert agent.max_call_duration_in_minutes == 30
        assert agent.initial_message_delay == 0
        assert agent.initial_message == ""

    def test_simulator_agent_str_representation(self, db, organization, workspace):
        """Test string representation of simulator agent."""
        agent = SimulatorAgent.objects.create(
            name="Display Name Agent",
            prompt="Test prompt",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )

        assert str(agent) == "Display Name Agent"

    def test_simulator_agent_with_custom_settings(self, db, organization, workspace):
        """Simulator agent with custom voice and conversation settings."""
        agent = SimulatorAgent.objects.create(
            name="Custom Agent",
            prompt="Custom prompt",
            voice_provider="openai",
            voice_name="alloy",
            model="gpt-4-turbo",
            interrupt_sensitivity=0.8,
            conversation_speed=1.5,
            finished_speaking_sensitivity=0.9,
            llm_temperature=0.3,
            max_call_duration_in_minutes=60,
            initial_message_delay=5,
            initial_message="Hello! How can I help you today?",
            organization=organization,
            workspace=workspace,
        )

        assert agent.interrupt_sensitivity == 0.8
        assert agent.conversation_speed == 1.5
        assert agent.finished_speaking_sensitivity == 0.9
        assert agent.llm_temperature == 0.3
        assert agent.max_call_duration_in_minutes == 60
        assert agent.initial_message_delay == 5
        assert agent.initial_message == "Hello! How can I help you today?"

    def test_simulator_agent_soft_delete(self, db, organization, workspace):
        """Soft delete should work for simulator agents."""
        agent = SimulatorAgent.objects.create(
            name="To Be Deleted",
            prompt="Delete me",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )

        agent_id = agent.id
        agent.delete()

        assert not SimulatorAgent.objects.filter(id=agent_id).exists()
        assert SimulatorAgent.all_objects.filter(id=agent_id).exists()


# ============================================================================
# ScenarioGraph Model Tests
# ============================================================================


@pytest.mark.unit
class TestScenarioGraphModel:
    """Tests for ScenarioGraph model."""

    @pytest.fixture
    def scenario(self, db, organization, workspace):
        """Create a scenario for graph tests."""
        return Scenarios.objects.create(
            name="Graph Parent Scenario",
            source="Parent source",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
        )

    def test_scenario_graph_creation(self, db, organization, scenario):
        """Basic scenario graph creation should work."""
        graph = ScenarioGraph.objects.create(
            name="Test Graph",
            description="A test graph",
            scenario=scenario,
            organization=organization,
            graph_config={"nodes": [], "edges": []},
        )

        assert graph.id is not None
        assert graph.name == "Test Graph"
        assert graph.description == "A test graph"
        assert graph.scenario == scenario
        assert graph.organization == organization
        assert graph.version == 1
        assert graph.is_active is True
        assert graph.deleted is False

    def test_scenario_graph_defaults(self, db, organization, scenario):
        """Scenario graph should have correct default values."""
        graph = ScenarioGraph.objects.create(
            name="Minimal Graph",
            scenario=scenario,
            organization=organization,
        )

        assert graph.version == 1
        assert graph.is_active is True
        assert graph.description == ""
        assert graph.graph_config == {}

    def test_scenario_graph_str_representation(self, db, organization, scenario):
        """Test string representation of scenario graph."""
        graph = ScenarioGraph.objects.create(
            name="Display Graph",
            scenario=scenario,
            organization=organization,
            version=3,
        )

        assert str(graph) == "Display Graph (v3)"

    def test_scenario_graph_with_config(self, db, organization, scenario):
        """Scenario graph can store complex graph configuration."""
        config = {
            "nodes": [
                {"id": "start", "type": "start", "label": "Start"},
                {"id": "msg1", "type": "message", "label": "Greeting"},
                {"id": "end", "type": "end", "label": "End"},
            ],
            "edges": [
                {"source": "start", "target": "msg1"},
                {"source": "msg1", "target": "end"},
            ],
            "graph_data": {"style": "default"},
        }

        graph = ScenarioGraph.objects.create(
            name="Configured Graph",
            scenario=scenario,
            organization=organization,
            graph_config=config,
        )

        assert graph.graph_config == config
        assert len(graph.graph_config["nodes"]) == 3
        assert len(graph.graph_config["edges"]) == 2

    def test_scenario_graph_soft_delete(self, db, organization, scenario):
        """Soft delete should work for scenario graphs."""
        graph = ScenarioGraph.objects.create(
            name="To Be Deleted",
            scenario=scenario,
            organization=organization,
        )

        graph_id = graph.id
        graph.delete()

        assert not ScenarioGraph.objects.filter(id=graph_id).exists()
        assert ScenarioGraph.all_objects.filter(id=graph_id).exists()

    def test_multiple_graphs_per_scenario(self, db, organization, scenario):
        """A scenario can have multiple graph versions."""
        graph_v1 = ScenarioGraph.objects.create(
            name="Graph V1",
            scenario=scenario,
            organization=organization,
            version=1,
            is_active=False,
        )

        graph_v2 = ScenarioGraph.objects.create(
            name="Graph V2",
            scenario=scenario,
            organization=organization,
            version=2,
            is_active=True,
        )

        # Both graphs exist
        graphs = ScenarioGraph.objects.filter(scenario=scenario)
        assert graphs.count() == 2

        # Can filter by active status
        active_graphs = ScenarioGraph.objects.filter(scenario=scenario, is_active=True)
        assert active_graphs.count() == 1
        assert active_graphs.first().version == 2

    def test_scenario_graph_does_not_declare_workspace_field(self):
        """Workspace scoping flows transitively through the parent scenario."""
        field_names = {f.name for f in ScenarioGraph._meta.get_fields()}
        assert "workspace" not in field_names

    def test_scenario_graph_rejects_workspace_kwarg(
        self, db, organization, scenario
    ):
        """Passing workspace= to create raises so callers stay on the transitive path."""
        with pytest.raises(TypeError, match="workspace"):
            ScenarioGraph.objects.create(
                scenario=scenario,
                organization=organization,
                name="Reject me",
                workspace=scenario.workspace,
            )


# ============================================================================
# NodeType Class Tests
# ============================================================================


@pytest.mark.unit
class TestNodeTypeClass:
    """Tests for NodeType utility class."""

    def test_node_type_constants(self):
        """Verify NodeType constants are correctly defined."""
        assert NodeType.START == "start"
        assert NodeType.MESSAGE == "message"
        assert NodeType.CONDITION == "condition"
        assert NodeType.END == "end"

    def test_all_types_list(self):
        """ALL_TYPES should contain all node types."""
        assert NodeType.START in NodeType.ALL_TYPES
        assert NodeType.MESSAGE in NodeType.ALL_TYPES
        assert NodeType.CONDITION in NodeType.ALL_TYPES
        assert NodeType.END in NodeType.ALL_TYPES
        assert len(NodeType.ALL_TYPES) == 4

    def test_terminal_types(self):
        """TERMINAL_TYPES should only contain end nodes."""
        assert NodeType.END in NodeType.TERMINAL_TYPES
        assert len(NodeType.TERMINAL_TYPES) == 1
        assert NodeType.START not in NodeType.TERMINAL_TYPES
        assert NodeType.MESSAGE not in NodeType.TERMINAL_TYPES

    def test_non_terminal_types(self):
        """NON_TERMINAL_TYPES should contain all non-end nodes."""
        assert NodeType.START in NodeType.NON_TERMINAL_TYPES
        assert NodeType.MESSAGE in NodeType.NON_TERMINAL_TYPES
        assert NodeType.CONDITION in NodeType.NON_TERMINAL_TYPES
        assert NodeType.END not in NodeType.NON_TERMINAL_TYPES
        assert len(NodeType.NON_TERMINAL_TYPES) == 3

    def test_is_valid(self):
        """is_valid should correctly identify valid node types."""
        assert NodeType.is_valid("start") is True
        assert NodeType.is_valid("message") is True
        assert NodeType.is_valid("condition") is True
        assert NodeType.is_valid("end") is True
        assert NodeType.is_valid("invalid") is False
        assert NodeType.is_valid("") is False

    def test_is_terminal(self):
        """is_terminal should correctly identify terminal nodes."""
        assert NodeType.is_terminal("end") is True
        assert NodeType.is_terminal("start") is False
        assert NodeType.is_terminal("message") is False
        assert NodeType.is_terminal("condition") is False
        assert NodeType.is_terminal("invalid") is False

    def test_get_display_name(self):
        """get_display_name should return proper display names."""
        assert NodeType.get_display_name("start") == "Start"
        assert NodeType.get_display_name("message") == "Message"
        assert NodeType.get_display_name("condition") == "Condition"
        assert NodeType.get_display_name("end") == "End"
        # Unknown types should return title case
        assert NodeType.get_display_name("custom") == "Custom"
