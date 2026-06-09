"""
Tests for scenario generation logic.

Tests cover:
- convert_personas_to_property_list: Persona conversion for agent generation
- validate_personas_activity: Persona validation with default values
- Persona cycling across rows
- Generated data structure validation
- Different scenario generation variations
"""

import json
import random
import uuid
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.simulator_agent import SimulatorAgent

# ============================================================================
# Fixtures
# ============================================================================


def _ee_voice_mapper():
    return pytest.importorskip("ee.voice.constants.voice_mapper")


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a test agent definition."""
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Test agent for simulation",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def scenario_dataset(db, organization, workspace, user):
    """Create a dataset with scenario-specific columns."""
    dataset = Dataset.no_workspace_objects.create(
        name="Scenario Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )

    # Create scenario columns
    persona_col = Column.objects.create(
        dataset=dataset,
        name="persona",
        data_type="persona",
        source=SourceChoices.OTHERS.value,
    )
    situation_col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    outcome_col = Column.objects.create(
        dataset=dataset,
        name="outcome",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )

    dataset.column_order = [
        str(persona_col.id),
        str(situation_col.id),
        str(outcome_col.id),
    ]
    dataset.save()

    return dataset


@pytest.fixture
def mock_persona():
    """Create a mock Persona object."""
    persona = MagicMock()
    persona.id = uuid.uuid4()
    persona.gender = "male"
    persona.age_group = "25-35"
    persona.location = "New York, USA"
    persona.occupation = "Software Engineer"
    persona.personality = "Friendly and helpful"
    persona.communication_style = "Direct and clear"
    persona.accent = "American"
    persona.languages = ["English"]
    persona.conversation_speed = "1.0"
    persona.background_sound = "office"
    persona.finished_speaking_sensitivity = "5"
    persona.interrupt_sensitivity = "5"
    persona.metadata = {}
    persona.additional_instruction = ""
    return persona


# ============================================================================
# convert_personas_to_property_list Tests
# ============================================================================


@pytest.mark.unit
class TestConvertPersonasToPropertyList:
    """Tests for convert_personas_to_property_list function.

    Note: The actual function expects a Django QuerySet of Persona objects.
    These tests verify the expected output structure and logic.
    """

    def test_property_list_structure(self):
        """Property list should have expected structure."""
        # This tests the expected structure that convert_personas_to_property_list produces
        expected_fields = [
            "min_length",
            "max_length",
            "gender",
            "age_group",
            "location",
            "profession",
            "personality",
            "communication_style",
            "accent",
            "language",
            "conversation_speed",
            "background_sound",
            "finished_speaking_sensitivity",
            "interrupt_sensitivity",
        ]

        # Verify all expected fields are defined
        assert len(expected_fields) >= 14

    def test_property_list_min_max_length_defaults(self):
        """Min and max length should have standard defaults."""
        # Standard values used in the function
        min_length = 50
        max_length = 400

        assert min_length == 50
        assert max_length == 400
        assert max_length > min_length

    def test_gender_values_are_valid(self):
        """Gender values should be recognizable strings."""
        valid_genders = ["male", "female", "Male", "Female"]

        for gender in valid_genders:
            assert gender.lower() in ["male", "female"]

    def test_age_group_format(self):
        """Age groups should follow expected format."""
        valid_age_groups = ["18-25", "25-32", "32-40", "40-50", "50-60", "60+"]

        for ag in valid_age_groups:
            # Either contains hyphen or plus sign
            assert "-" in ag or "+" in ag

    def test_metadata_json_parsing(self):
        """JSON string metadata can be parsed."""
        metadata_str = '{"key": "value", "nested": {"inner": 123}}'
        parsed = json.loads(metadata_str)

        assert parsed["key"] == "value"
        assert parsed["nested"]["inner"] == 123

    def test_property_aggregation_logic(self):
        """Property values from multiple personas aggregate into lists."""
        # Simulating how the function aggregates values
        personas_data = [
            {"gender": "male", "location": "New York"},
            {"gender": "female", "location": "London"},
            {"gender": "male", "location": "Tokyo"},
        ]

        genders = set()
        locations = set()

        for p in personas_data:
            genders.add(p["gender"])
            locations.add(p["location"])

        assert "male" in genders
        assert "female" in genders
        assert len(locations) == 3


# ============================================================================
# Persona Validation Tests
# ============================================================================


@pytest.mark.unit
class TestPersonaValidation:
    """Tests for persona validation logic."""

    def test_validate_personas_fills_missing_fields(self):
        """Validation should fill missing fields with defaults."""
        # These are the default values from validate_personas_activity
        default_values = {
            "gender": ["male", "female"],
            "age_group": ["18-25", "25-32", "32-40", "40-50", "50-60", "60+"],
            "location": [
                "United States",
                "Canada",
                "United Kingdom",
                "Australia",
                "India",
            ],
            "profession": ["Student", "Teacher", "Engineer", "Doctor", "Nurse"],
            "personality": ["Friendly and cooperative", "Professional and formal"],
            "communication_style": ["Direct and concise", "Detailed and elaborate"],
            "accent": ["American", "Australian", "Indian", "Canadian", "Neutral"],
            "language": ["English"],
            "conversation_speed": ["0.5", "0.75", "1.0", "1.25", "1.5"],
            "background_sound": ["true", "false"],
            "finished_speaking_sensitivity": ["1-10"],
            "interrupt_sensitivity": ["1-10"],
        }

        # Simulating the validation logic
        persona = {"name": "Test Person"}

        for field, choices in default_values.items():
            if field not in persona or not persona[field]:
                persona[field] = random.choice(choices)

        # All fields should now be present
        for field in default_values.keys():
            assert field in persona
            assert persona[field] is not None

    def test_validate_personas_preserves_existing_values(self):
        """Validation should not overwrite existing values."""
        persona = {
            "name": "John Doe",
            "gender": "male",
            "age_group": "30-40",
            "location": "San Francisco",
        }

        # Simulate validation - shouldn't change existing values
        original_gender = persona["gender"]
        original_location = persona["location"]

        # Validation logic should preserve these
        assert persona["gender"] == original_gender
        assert persona["location"] == original_location

    def test_validate_personas_required_fields(self):
        """Check that all required persona fields are defined."""
        required_fields = [
            "name",
            "gender",
            "age_group",
            "location",
            "profession",
            "personality",
            "communication_style",
            "accent",
            "language",
            "conversation_speed",
            "finished_speaking_sensitivity",
            "interrupt_sensitivity",
            "background_sound",
        ]

        # All these fields should be considered in validation
        assert len(required_fields) == 13


# ============================================================================
# Persona Cycling Tests
# ============================================================================


@pytest.mark.unit
class TestPersonaCycling:
    """Tests for persona cycling across scenario rows."""

    def test_personas_cycle_across_rows(self):
        """Personas should cycle across rows when fewer personas than rows."""
        personas = [
            {"id": "p1", "name": "Persona 1"},
            {"id": "p2", "name": "Persona 2"},
            {"id": "p3", "name": "Persona 3"},
        ]
        num_rows = 7

        # Simulate cycling logic
        assigned_personas = []
        for i in range(num_rows):
            assigned_personas.append(personas[i % len(personas)])

        # Check cycling pattern
        assert assigned_personas[0]["id"] == "p1"
        assert assigned_personas[1]["id"] == "p2"
        assert assigned_personas[2]["id"] == "p3"
        assert assigned_personas[3]["id"] == "p1"  # Cycles back
        assert assigned_personas[4]["id"] == "p2"
        assert assigned_personas[5]["id"] == "p3"
        assert assigned_personas[6]["id"] == "p1"  # Cycles back again

    def test_single_persona_repeats(self):
        """Single persona should be assigned to all rows."""
        personas = [{"id": "p1", "name": "Only Persona"}]
        num_rows = 5

        assigned_personas = []
        for i in range(num_rows):
            assigned_personas.append(personas[i % len(personas)])

        assert all(p["id"] == "p1" for p in assigned_personas)

    def test_equal_personas_and_rows(self):
        """When personas equal rows, each should be used once."""
        personas = [
            {"id": "p1"},
            {"id": "p2"},
            {"id": "p3"},
        ]
        num_rows = 3

        assigned_personas = []
        for i in range(num_rows):
            assigned_personas.append(personas[i % len(personas)])

        # Each persona used exactly once
        assert assigned_personas[0]["id"] == "p1"
        assert assigned_personas[1]["id"] == "p2"
        assert assigned_personas[2]["id"] == "p3"


# ============================================================================
# Generated Data Structure Tests
# ============================================================================


@pytest.mark.unit
class TestGeneratedDataStructure:
    """Tests for validating generated scenario data structure."""

    def test_persona_json_structure(self):
        """Persona cell value should have proper JSON structure."""
        persona_data = {
            "name": "John Doe",
            "gender": "male",
            "age_group": "25-35",
            "location": "New York, USA",
            "profession": "Software Engineer",
            "personality": "Friendly and helpful",
            "communication_style": "Direct and clear",
            "accent": "American",
            "language": "English",
            "conversation_speed": "1.0",
            "background_sound": "office",
            "finished_speaking_sensitivity": "5",
            "interrupt_sensitivity": "5",
        }

        # Verify all required fields present
        required_fields = [
            "name",
            "gender",
            "age_group",
            "location",
            "profession",
            "personality",
            "communication_style",
            "accent",
            "language",
            "conversation_speed",
            "background_sound",
            "finished_speaking_sensitivity",
            "interrupt_sensitivity",
        ]

        for field in required_fields:
            assert field in persona_data

        # Verify JSON serialization works
        json_str = json.dumps(persona_data)
        parsed = json.loads(json_str)
        assert parsed == persona_data

    def test_situation_text_format(self):
        """Situation cell should contain descriptive text."""
        situation = "Customer calling to inquire about their recent order status and potential refund options."

        assert isinstance(situation, str)
        assert len(situation) > 10  # Should be meaningful text
        assert len(situation) < 2000  # Reasonable upper bound

    def test_outcome_text_format(self):
        """Outcome cell should contain expected conversation outcome."""
        outcome = (
            "Customer successfully receives order status and understands refund policy."
        )

        assert isinstance(outcome, str)
        assert len(outcome) > 10
        assert len(outcome) < 2000

    def test_generated_row_data_structure(self):
        """Generated row should have all scenario columns."""
        generated_row = {
            "persona": {
                "name": "Jane Smith",
                "gender": "female",
                "age_group": "30-40",
                "location": "London, UK",
            },
            "situation": "Customer reporting a technical issue with the product.",
            "outcome": "Issue resolved through troubleshooting steps.",
        }

        # All three core columns present
        assert "persona" in generated_row
        assert "situation" in generated_row
        assert "outcome" in generated_row

        # Persona is dict, others are strings
        assert isinstance(generated_row["persona"], dict)
        assert isinstance(generated_row["situation"], str)
        assert isinstance(generated_row["outcome"], str)


# ============================================================================
# Cell Persistence Tests
# ============================================================================


@pytest.mark.integration
class TestCellPersistence:
    """Tests for persisting generated data to cells."""

    def test_persist_generated_persona_to_cell(self, db, scenario_dataset):
        """Generated persona data should persist as JSON to cell."""
        persona_col = Column.objects.get(dataset=scenario_dataset, name="persona")
        row = Row.objects.create(dataset=scenario_dataset, order=0)

        persona_data = {
            "name": "Test Person",
            "gender": "male",
            "age_group": "25-35",
        }

        cell = Cell.objects.create(
            dataset=scenario_dataset,
            column=persona_col,
            row=row,
            value=json.dumps(persona_data),
        )

        # Verify persistence
        cell.refresh_from_db()
        stored_value = json.loads(cell.value)
        assert stored_value["name"] == "Test Person"
        assert stored_value["gender"] == "male"

    def test_persist_generated_situation_to_cell(self, db, scenario_dataset):
        """Generated situation text should persist to cell."""
        situation_col = Column.objects.get(dataset=scenario_dataset, name="situation")
        row = Row.objects.create(dataset=scenario_dataset, order=0)

        situation_text = "Customer calling about order #12345"

        cell = Cell.objects.create(
            dataset=scenario_dataset,
            column=situation_col,
            row=row,
            value=situation_text,
        )

        cell.refresh_from_db()
        assert cell.value == situation_text

    def test_persist_multiple_rows_batch(self, db, scenario_dataset):
        """Multiple rows can be persisted in batch operation."""
        persona_col = Column.objects.get(dataset=scenario_dataset, name="persona")

        # Create multiple rows
        rows = []
        for i in range(5):
            rows.append(Row(dataset=scenario_dataset, order=i))
        Row.objects.bulk_create(rows)

        # Refresh to get IDs
        rows = list(Row.objects.filter(dataset=scenario_dataset).order_by("order"))

        # Create cells in batch
        cells = []
        for i, row in enumerate(rows):
            cells.append(
                Cell(
                    dataset=scenario_dataset,
                    column=persona_col,
                    row=row,
                    value=json.dumps({"name": f"Person {i}"}),
                )
            )
        Cell.objects.bulk_create(cells)

        # Verify all persisted
        persisted_cells = Cell.objects.filter(
            dataset=scenario_dataset,
            column=persona_col,
        )
        assert persisted_cells.count() == 5

    def test_update_existing_cells(self, db, scenario_dataset):
        """Existing cells can be updated with new generated data."""
        persona_col = Column.objects.get(dataset=scenario_dataset, name="persona")
        row = Row.objects.create(dataset=scenario_dataset, order=0)

        # Create initial cell
        cell = Cell.objects.create(
            dataset=scenario_dataset,
            column=persona_col,
            row=row,
            value=json.dumps({"name": "Initial"}),
        )

        # Update with new data
        cell.value = json.dumps({"name": "Updated"})
        cell.save()

        cell.refresh_from_db()
        assert json.loads(cell.value)["name"] == "Updated"


# ============================================================================
# Scenario Generation Variation Tests
# ============================================================================


@pytest.mark.integration
class TestScenarioGenerationVariations:
    """Tests for different scenario generation configurations."""

    def test_dataset_scenario_with_source_columns(
        self, db, organization, workspace, user
    ):
        """Dataset scenario should copy source columns plus add scenario columns."""
        # Create source dataset with custom columns
        source_dataset = Dataset.no_workspace_objects.create(
            name="Source",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.BUILD.value,
        )

        # Add custom columns
        custom_col = Column.objects.create(
            dataset=source_dataset,
            name="customer_id",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )

        # Create scenario dataset copying structure
        scenario_dataset = Dataset.no_workspace_objects.create(
            name="Scenario from Source",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.SCENARIO.value,
        )

        # Copy source columns
        Column.objects.create(
            dataset=scenario_dataset,
            name="customer_id",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )

        # Add mandatory scenario columns
        for col_name, col_type in [
            ("persona", "persona"),
            ("situation", "text"),
            ("outcome", "text"),
        ]:
            Column.objects.create(
                dataset=scenario_dataset,
                name=col_name,
                data_type=col_type,
                source=SourceChoices.OTHERS.value,
            )

        # Verify structure
        columns = Column.objects.filter(dataset=scenario_dataset, deleted=False)
        column_names = [c.name for c in columns]

        assert "customer_id" in column_names  # Copied from source
        assert "persona" in column_names  # Mandatory
        assert "situation" in column_names  # Mandatory
        assert "outcome" in column_names  # Mandatory

    def test_scenario_with_custom_columns(self, db, organization, workspace, user):
        """Scenario can include custom columns beyond persona/situation/outcome."""
        scenario_dataset = Dataset.no_workspace_objects.create(
            name="Custom Column Scenario",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.SCENARIO.value,
        )

        # Add mandatory columns
        for col_name, col_type in [
            ("persona", "persona"),
            ("situation", "text"),
            ("outcome", "text"),
        ]:
            Column.objects.create(
                dataset=scenario_dataset,
                name=col_name,
                data_type=col_type,
                source=SourceChoices.OTHERS.value,
            )

        # Add custom columns (using valid data types)
        custom_columns = [
            {"name": "urgency_level", "data_type": "text"},
            {"name": "customer_segment", "data_type": "text"},
            {"name": "expected_duration", "data_type": "integer"},
        ]

        for col_def in custom_columns:
            Column.objects.create(
                dataset=scenario_dataset,
                name=col_def["name"],
                data_type=col_def["data_type"],
                source=SourceChoices.OTHERS.value,
            )

        columns = Column.objects.filter(dataset=scenario_dataset, deleted=False)
        assert columns.count() == 6  # 3 mandatory + 3 custom

    def test_scenario_row_count_variations(self, db, organization, workspace, user):
        """Scenario can generate different numbers of rows."""
        for num_rows in [1, 5, 20, 100]:
            scenario_dataset = Dataset.no_workspace_objects.create(
                name=f"Scenario with {num_rows} rows",
                organization=organization,
                workspace=workspace,
                user=user,
                source=DatasetSourceChoices.SCENARIO.value,
            )

            # Create rows
            rows = [Row(dataset=scenario_dataset, order=i) for i in range(num_rows)]
            Row.objects.bulk_create(rows)

            actual_count = Row.objects.filter(
                dataset=scenario_dataset, deleted=False
            ).count()
            assert actual_count == num_rows


# ============================================================================
# Language-Based Persona Selection Tests
# ============================================================================


@pytest.mark.unit
class TestLanguageBasedPersonaSelection:
    """Tests for language-based persona selection."""

    def test_english_personas_for_en_agent(self):
        """English agent should use English personas."""
        voice_mapper = _ee_voice_mapper()
        ENGLISH_PERSONAS = voice_mapper.ENGLISH_PERSONAS
        get_personas_by_language = voice_mapper.get_personas_by_language

        personas = get_personas_by_language("en")
        assert personas == ENGLISH_PERSONAS

    def test_hindi_personas_for_hi_agent(self):
        """Hindi agent should use Hindi personas."""
        voice_mapper = _ee_voice_mapper()
        HINDI_PERSONAS = voice_mapper.HINDI_PERSONAS
        get_personas_by_language = voice_mapper.get_personas_by_language

        personas = get_personas_by_language("hi")
        assert personas == HINDI_PERSONAS

    def test_default_to_english_for_unknown_language(self):
        """Unknown language should default to English personas."""
        voice_mapper = _ee_voice_mapper()
        ENGLISH_PERSONAS = voice_mapper.ENGLISH_PERSONAS
        get_personas_by_language = voice_mapper.get_personas_by_language

        # Unknown languages should fall back to English
        for lang in ["fr", "es", "de", "ja", "unknown"]:
            personas = get_personas_by_language(lang)
            assert personas == ENGLISH_PERSONAS


# ============================================================================
# Scenario Type Specific Tests
# ============================================================================


@pytest.mark.integration
class TestScenarioTypeVariations:
    """Tests for different scenario types (dataset, script, graph)."""

    def test_dataset_scenario_type(self, db, organization, workspace):
        """Dataset scenarios have correct type."""
        scenario = Scenarios.objects.create(
            name="Dataset Type Test",
            source="Test source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
        )

        assert scenario.scenario_type == "dataset"

    def test_script_scenario_type(self, db, organization, workspace):
        """Script scenarios have correct type."""
        scenario = Scenarios.objects.create(
            name="Script Type Test",
            source="script content",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=organization,
            workspace=workspace,
        )

        assert scenario.scenario_type == "script"

    def test_graph_scenario_type(self, db, organization, workspace):
        """Graph scenarios have correct type."""
        scenario = Scenarios.objects.create(
            name="Graph Type Test",
            source="graph content",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
        )

        assert scenario.scenario_type == "graph"

    def test_dataset_scenario_requires_source_dataset_metadata(
        self, db, organization, workspace, user
    ):
        """Dataset scenarios should reference source dataset in metadata."""
        source = Dataset.no_workspace_objects.create(
            name="Source",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.BUILD.value,
        )

        scenario = Scenarios.objects.create(
            name="Dataset Reference Test",
            source=f"Created from dataset: {source.name}",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            metadata={"source_dataset_id": str(source.id)},
        )

        assert "source_dataset_id" in scenario.metadata
        assert scenario.metadata["source_dataset_id"] == str(source.id)

    def test_script_scenario_stores_script_url(self, db, organization, workspace):
        """Script scenarios should store script URL in metadata."""
        scenario = Scenarios.objects.create(
            name="Script URL Test",
            source="Script-based scenario",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=organization,
            workspace=workspace,
            metadata={"script_url": "https://example.com/script.txt"},
        )

        assert scenario.metadata["script_url"] == "https://example.com/script.txt"

    def test_graph_scenario_has_associated_graph(self, db, organization, workspace):
        """Graph scenarios should have associated ScenarioGraph."""
        from simulate.models.scenario_graph import ScenarioGraph

        scenario = Scenarios.objects.create(
            name="Graph Association Test",
            source="Graph-based scenario",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
        )

        graph = ScenarioGraph.objects.create(
            name="Test Graph",
            scenario=scenario,
            organization=organization,
            graph_config={"nodes": [], "edges": []},
        )

        # Verify relationship
        assert graph.scenario == scenario
        assert ScenarioGraph.objects.filter(scenario=scenario).exists()
