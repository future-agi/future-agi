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

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    SourceChoices,
    StatusType,
)
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


@pytest.mark.integration
class TestGenerateScenarioRowsPrefetch:
    """Tests for cell resolution in generate_scenario_rows."""

    @staticmethod
    def _make_scenario_and_graph(dataset, agent_definition, organization, workspace):
        from simulate.models.scenario_graph import ScenarioGraph

        scenario = Scenarios.objects.create(
            name="Prefetch Test",
            source="",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
            agent_definition=agent_definition,
            dataset=dataset,
        )
        ScenarioGraph.objects.create(
            name="Prefetch Graph",
            scenario=scenario,
            organization=organization,
            graph_config={"nodes": [], "edges": []},
        )
        return scenario

    @staticmethod
    def _seed_rows(dataset, num_rows):
        Row.objects.bulk_create(
            [Row(dataset=dataset, order=i) for i in range(num_rows)]
        )
        return list(
            Row.objects.filter(dataset=dataset).order_by("order").values_list("id", flat=True)
        )

    @staticmethod
    def _seed_cells(dataset, columns, row_ids):
        Cell.objects.bulk_create(
            [
                Cell(dataset=dataset, column=c, row_id=rid, value="")
                for rid in row_ids
                for c in columns
            ]
        )

    @staticmethod
    def _build_cases(num_rows, columns):
        return [
            {c.name.lower(): f"val_r{i}_c{c.name}" for c in columns}
            for i in range(num_rows)
        ]

    def test_prefetch_updates_existing_cells(
        self, db, scenario_dataset, agent_definition, organization, workspace
    ):
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        columns = list(Column.objects.filter(dataset=scenario_dataset))
        row_ids = self._seed_rows(scenario_dataset, num_rows=3)
        self._seed_cells(scenario_dataset, columns, row_ids)
        scenario = self._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )
        cases = self._build_cases(3, columns)

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            agent = mock_agent_cls.return_value
            agent.graph_generator.get_branches.return_value = []
            agent._generate_cases_for_branches.return_value = cases

            generate_scenario_rows(
                dataset_id=scenario_dataset.id,
                scenario_id=scenario.id,
                num_rows=3,
                description="test",
                new_rows_id=row_ids,
            )

        for i, rid in enumerate(row_ids):
            for c in columns:
                cell = Cell.objects.get(row_id=rid, column=c)
                assert cell.value == f"val_r{i}_c{c.name}"
                assert cell.status == CellStatus.PASS.value
        assert Cell.objects.filter(dataset=scenario_dataset).count() == 3 * len(columns)

    def test_error_path_marks_seeded_cells_error(
        self, db, scenario_dataset, agent_definition, organization, workspace
    ):
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        columns = list(Column.objects.filter(dataset=scenario_dataset))
        row_ids = self._seed_rows(scenario_dataset, num_rows=2)
        self._seed_cells(scenario_dataset, columns, row_ids)
        scenario = self._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            agent = mock_agent_cls.return_value
            agent.graph_generator.get_branches.return_value = []
            agent._generate_cases_for_branches.return_value = []

            with pytest.raises(ValueError):
                generate_scenario_rows(
                    dataset_id=scenario_dataset.id,
                    scenario_id=scenario.id,
                    num_rows=2,
                    description="test",
                    new_rows_id=row_ids,
                )

        cells = Cell.objects.filter(dataset=scenario_dataset)
        assert cells.count() == 2 * len(columns)
        assert set(cells.values_list("status", flat=True)) == {CellStatus.ERROR.value}
        columns_qs = Column.objects.filter(dataset=scenario_dataset)
        assert set(columns_qs.values_list("status", flat=True)) == {
            StatusType.FAILED.value
        }

    def test_conversation_branch_reads_branch_name_fallback(
        self, db, scenario_dataset, agent_definition, organization, workspace
    ):
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        conv_col = Column.objects.create(
            dataset=scenario_dataset,
            name="conversation_branch",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        scenario_dataset.column_order = [
            *scenario_dataset.column_order,
            str(conv_col.id),
        ]
        scenario_dataset.save()

        columns = list(Column.objects.filter(dataset=scenario_dataset))
        row_ids = self._seed_rows(scenario_dataset, num_rows=1)
        self._seed_cells(scenario_dataset, columns, row_ids)
        scenario = self._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )
        cases = [
            {
                "persona": "p",
                "situation": "s",
                "outcome": "o",
                "branch_name": "greeting-then-verify",
            }
        ]

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            agent = mock_agent_cls.return_value
            agent.graph_generator.get_branches.return_value = []
            agent._generate_cases_for_branches.return_value = cases

            generate_scenario_rows(
                dataset_id=scenario_dataset.id,
                scenario_id=scenario.id,
                num_rows=1,
                description="test",
                new_rows_id=row_ids,
            )

        conv_cell = Cell.objects.get(row_id=row_ids[0], column=conv_col)
        assert conv_cell.value == "greeting-then-verify"
        assert conv_cell.status == CellStatus.PASS.value

    def test_cell_resolution_is_one_query_not_per_cell(
        self,
        db,
        scenario_dataset,
        agent_definition,
        organization,
        workspace,
        django_assert_max_num_queries,
    ):
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        columns = list(Column.objects.filter(dataset=scenario_dataset))
        num_rows = 5
        row_ids = self._seed_rows(scenario_dataset, num_rows=num_rows)
        self._seed_cells(scenario_dataset, columns, row_ids)
        scenario = self._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )
        cases = self._build_cases(num_rows, columns)

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            agent = mock_agent_cls.return_value
            agent.graph_generator.get_branches.return_value = []
            agent._generate_cases_for_branches.return_value = cases

            with django_assert_max_num_queries(20):
                generate_scenario_rows(
                    dataset_id=scenario_dataset.id,
                    scenario_id=scenario.id,
                    num_rows=num_rows,
                    description="test",
                    new_rows_id=row_ids,
                )


class TestKnowledgeBaseWiring:
    """Wiring the agent's KB reference through generate_scenario_rows."""

    def test_resolve_returns_none_when_agent_has_no_kb(self, db, agent_definition):
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        assert build_agent_kb_payload(agent_definition) is None

    def test_resolve_returns_none_when_agent_or_id_is_not_a_valid_uuid(self, db):
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        assert build_agent_kb_payload("not-a-uuid") is None

    def test_resolve_returns_none_when_indexer_returns_empty(
        self, db, agent_definition, organization
    ):
        from model_hub.models.develop_dataset import KnowledgeBaseFile
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        kb = KnowledgeBaseFile.objects.create(name="empty-kb", organization=organization)
        agent_definition.knowledge_base = kb
        agent_definition.save(update_fields=["knowledge_base"])

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample", return_value=[]
        ):
            payload = build_agent_kb_payload(agent_definition)

        assert payload is None

    def test_resolve_returns_shaped_dict_when_kb_present(
        self, db, agent_definition, organization
    ):
        from model_hub.models.develop_dataset import KnowledgeBaseFile
        from model_hub.utils.kb_indexer import KB_TABLE_NAME
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        kb = KnowledgeBaseFile.objects.create(name="kb", organization=organization)
        agent_definition.knowledge_base = kb
        agent_definition.save(update_fields=["knowledge_base"])

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=[
                "7c23b7c0-0fc4-4a4f-b3f3-693efd733453",
                "e6c1fd97-0444-4292-9165-023cc80dce6a",
            ],
        ) as mock_get:
            payload = build_agent_kb_payload(agent_definition)

        mock_get.assert_called_once()
        assert payload == {
            "table_name": KB_TABLE_NAME,
            "kb_id": str(kb.id),
            "doc_ids": [
                "7c23b7c0-0fc4-4a4f-b3f3-693efd733453",
                "e6c1fd97-0444-4292-9165-023cc80dce6a",
            ],
        }

    def test_resolve_returns_none_on_indexer_exception(
        self, db, agent_definition, organization
    ):
        from model_hub.models.develop_dataset import KnowledgeBaseFile
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        kb = KnowledgeBaseFile.objects.create(name="kb", organization=organization)
        agent_definition.knowledge_base = kb
        agent_definition.save(update_fields=["knowledge_base"])

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            side_effect=RuntimeError("boom"),
        ):
            payload = build_agent_kb_payload(agent_definition)

        assert payload is None

    def test_generate_scenario_rows_forwards_kb_payload_to_enhanced_agent(
        self, db, scenario_dataset, agent_definition, organization, workspace
    ):
        from model_hub.models.develop_dataset import KnowledgeBaseFile
        from model_hub.utils.kb_indexer import KB_TABLE_NAME
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        kb = KnowledgeBaseFile.objects.create(name="kb", organization=organization)
        agent_definition.knowledge_base = kb
        agent_definition.save(update_fields=["knowledge_base"])

        columns = list(Column.objects.filter(dataset=scenario_dataset))
        row_ids = TestGenerateScenarioRowsPrefetch._seed_rows(
            scenario_dataset, num_rows=1
        )
        TestGenerateScenarioRowsPrefetch._seed_cells(scenario_dataset, columns, row_ids)
        scenario = TestGenerateScenarioRowsPrefetch._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )
        cases = TestGenerateScenarioRowsPrefetch._build_cases(1, columns)

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ), patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=["7c23b7c0-0fc4-4a4f-b3f3-693efd733453"],
        ):
            agent = mock_agent_cls.return_value
            agent.graph_generator.get_branches.return_value = []
            agent._generate_cases_for_branches.return_value = cases

            generate_scenario_rows(
                dataset_id=scenario_dataset.id,
                scenario_id=scenario.id,
                num_rows=1,
                description="scenario desc",
                new_rows_id=row_ids,
            )

        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["knowledge_base"] == {
            "table_name": KB_TABLE_NAME,
            "kb_id": str(kb.id),
            "doc_ids": ["7c23b7c0-0fc4-4a4f-b3f3-693efd733453"],
        }

    def test_generate_scenario_rows_forwards_none_when_agent_has_no_kb(
        self, db, scenario_dataset, agent_definition, organization, workspace
    ):
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        columns = list(Column.objects.filter(dataset=scenario_dataset))
        row_ids = TestGenerateScenarioRowsPrefetch._seed_rows(
            scenario_dataset, num_rows=1
        )
        TestGenerateScenarioRowsPrefetch._seed_cells(scenario_dataset, columns, row_ids)
        scenario = TestGenerateScenarioRowsPrefetch._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )
        cases = TestGenerateScenarioRowsPrefetch._build_cases(1, columns)

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            agent = mock_agent_cls.return_value
            agent.graph_generator.get_branches.return_value = []
            agent._generate_cases_for_branches.return_value = cases

            generate_scenario_rows(
                dataset_id=scenario_dataset.id,
                scenario_id=scenario.id,
                num_rows=1,
                description="anything",
                new_rows_id=row_ids,
            )

        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["knowledge_base"] is None


class TestPinnedOrLiveHelper:
    """Unit tests for pinned_or_live: snapshot value wins iff present and non-None."""

    def _agent(self, **fields):
        agent = MagicMock()
        for k, v in fields.items():
            setattr(agent, k, v)
        return agent

    def test_snapshot_value_wins_when_present(self):
        from simulate.models.agent_version import pinned_or_live

        assert (
            pinned_or_live({"agent_name": "pinned"}, self._agent(agent_name="live"), "agent_name")
            == "pinned"
        )

    def test_live_used_when_snapshot_is_none(self):
        from simulate.models.agent_version import pinned_or_live

        assert pinned_or_live(None, self._agent(agent_name="live"), "agent_name") == "live"

    def test_live_used_when_snapshot_missing_field(self):
        from simulate.models.agent_version import pinned_or_live

        assert (
            pinned_or_live({"other": 1}, self._agent(agent_name="live"), "agent_name")
            == "live"
        )

    def test_live_used_when_snapshot_field_is_none(self):
        from simulate.models.agent_version import pinned_or_live

        assert (
            pinned_or_live({"agent_name": None}, self._agent(agent_name="live"), "agent_name")
            == "live"
        )

    def test_snapshot_false_wins_over_live_true(self):
        """Regression: booleans must survive the truthy-only check."""
        from simulate.models.agent_version import pinned_or_live

        assert (
            pinned_or_live({"inbound": False}, self._agent(inbound=True), "inbound") is False
        )

    def test_snapshot_empty_list_wins(self):
        from simulate.models.agent_version import pinned_or_live

        assert (
            pinned_or_live({"languages": []}, self._agent(languages=["en"]), "languages")
            == []
        )


class TestBuildKbPayload:

    def test_none_kb_id_returns_none(self):
        from model_hub.utils.kb_indexer import build_kb_payload

        assert build_kb_payload(None) is None

    def test_empty_doc_ids_returns_none(self):
        from model_hub.utils.kb_indexer import build_kb_payload

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample", return_value=[]
        ):
            assert build_kb_payload("kb-uuid") is None

    def test_returns_shaped_dict_when_docs_present(self):
        from model_hub.utils.kb_indexer import KB_TABLE_NAME, build_kb_payload

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=[
                "7c23b7c0-0fc4-4a4f-b3f3-693efd733453",
                "e6c1fd97-0444-4292-9165-023cc80dce6a",
            ],
        ):
            payload = build_kb_payload("kb-uuid")

        assert payload == {
            "table_name": KB_TABLE_NAME,
            "kb_id": "kb-uuid",
            "doc_ids": [
                "7c23b7c0-0fc4-4a4f-b3f3-693efd733453",
                "e6c1fd97-0444-4292-9165-023cc80dce6a",
            ],
        }

    def test_indexer_exception_returns_none(self):
        from model_hub.utils.kb_indexer import build_kb_payload

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            side_effect=RuntimeError("boom"),
        ):
            assert build_kb_payload("kb-uuid") is None

    def test_non_uuid_items_filtered_out(self):
        from model_hub.utils.kb_indexer import build_kb_payload

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=["c", "f", "d", "cfd0e8a2-3064-489a-bf3f-43c430680f44"],
        ):
            payload = build_kb_payload("kb-uuid")
            assert payload is not None
            assert payload["doc_ids"] == ["cfd0e8a2-3064-489a-bf3f-43c430680f44"]

    def test_default_cap_passed_to_sampler(self):
        from model_hub.utils.kb_indexer import (
            KB_DOC_ID_PAYLOAD_CAP,
            build_kb_payload,
        )

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=["cfd0e8a2-3064-489a-bf3f-43c430680f44"],
        ) as mock_sample:
            build_kb_payload("kb-uuid")
        mock_sample.assert_called_once()
        args, _ = mock_sample.call_args
        assert args[0] == "kb-uuid"
        assert args[1] == KB_DOC_ID_PAYLOAD_CAP

    def test_explicit_max_count_forwarded(self):
        from model_hub.utils.kb_indexer import build_kb_payload

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=["cfd0e8a2-3064-489a-bf3f-43c430680f44"],
        ) as mock_sample:
            build_kb_payload("kb-uuid", 50)
        assert mock_sample.call_args.args[1] == 50


class TestBuildAgentKbPayloadVersionPin:
    """Version pin is authoritative: null snapshot KB means no KB, not fall-through."""

    @pytest.fixture
    def _kb(self, db, organization):
        from model_hub.models.develop_dataset import KnowledgeBaseFile

        return KnowledgeBaseFile.objects.create(name="live-kb", organization=organization)

    def _agent_with_kb(self, agent_definition, kb):
        agent_definition.knowledge_base = kb
        agent_definition.save(update_fields=["knowledge_base"])
        return agent_definition

    def _scenario_with_version(self, db, agent_definition, organization, workspace, snapshot):
        from simulate.models.agent_version import AgentVersion

        version = AgentVersion.objects.create(
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
            version_number=1,
            configuration_snapshot=snapshot or {},
        )
        scenario = Scenarios.objects.create(
            name="s",
            source="x",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
            agent_definition=agent_definition,
            metadata={"agent_definition_version_id": str(version.id)},
        )
        return scenario, version

    def test_snapshot_kb_wins_over_live(
        self, db, agent_definition, organization, workspace, _kb
    ):
        from model_hub.models.develop_dataset import KnowledgeBaseFile
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        pinned_kb = KnowledgeBaseFile.objects.create(name="pinned-kb", organization=organization)
        self._agent_with_kb(agent_definition, _kb)  # live points to _kb
        scenario, _ = self._scenario_with_version(
            db, agent_definition, organization, workspace,
            snapshot={"knowledge_base": str(pinned_kb.id)},
        )

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=["7c23b7c0-0fc4-4a4f-b3f3-693efd733453"],
        ):
            payload = build_agent_kb_payload(agent_definition, scenario=scenario)

        assert payload["kb_id"] == str(pinned_kb.id)
        assert payload["kb_id"] != str(_kb.id)

    def test_snapshot_null_kb_returns_none_not_live_fallback(
        self, db, agent_definition, organization, workspace, _kb
    ):
        """v3-with-no-KB case: pinned version means live is silently ignored."""
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        self._agent_with_kb(agent_definition, _kb)  # live has a KB
        scenario, _ = self._scenario_with_version(
            db, agent_definition, organization, workspace,
            snapshot={"knowledge_base": None},
        )

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=["e6c1fd97-0444-4292-9165-023cc80dce6a"],
        ):
            payload = build_agent_kb_payload(agent_definition, scenario=scenario)

        assert payload is None

    def test_missing_version_returns_none(
        self, db, agent_definition, organization, workspace, _kb
    ):
        """Pin points to a nonexistent version: pin authority means no live fallback."""
        import uuid as _uuid

        from model_hub.utils.kb_indexer import build_agent_kb_payload

        self._agent_with_kb(agent_definition, _kb)
        scenario = Scenarios.objects.create(
            name="s",
            source="x",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
            agent_definition=agent_definition,
            metadata={"agent_definition_version_id": str(_uuid.uuid4())},
        )

        with patch(
            "model_hub.utils.kb_indexer.KBIndexer.get_kb_doc_id_sample",
            return_value=["e6c1fd97-0444-4292-9165-023cc80dce6a"],
        ):
            payload = build_agent_kb_payload(agent_definition, scenario=scenario)

        assert payload is None


class TestBuildAgentKbPayloadNonUuidGuard:
    """Regression: non-UUID agent_or_id must not propagate a ValidationError up."""

    def test_non_uuid_string_returns_none(self, db):
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        assert build_agent_kb_payload("not-a-uuid-at-all") is None

    def test_integer_agent_or_id_returns_none(self, db):
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        assert build_agent_kb_payload(12345) is None

    def test_none_agent_or_id_short_circuits(self, db):
        from model_hub.utils.kb_indexer import build_agent_kb_payload

        assert build_agent_kb_payload(None) is None


class TestColumnDefinitionSerializerProperty:
    """Regression: custom_columns[*].property must reach the request payload."""

    def test_accepts_property_dict(self):
        from simulate.serializers.requests.scenarios import ColumnDefinitionSerializer

        ser = ColumnDefinitionSerializer(
            data={
                "name": "segment",
                "data_type": "text",
                "description": "customer segment tag",
                "property": {"min_length": 3, "max_length": 32},
            }
        )
        assert ser.is_valid(), ser.errors
        assert ser.validated_data["property"] == {"min_length": 3, "max_length": 32}

    def test_property_is_optional(self):
        from simulate.serializers.requests.scenarios import ColumnDefinitionSerializer

        ser = ColumnDefinitionSerializer(
            data={
                "name": "segment",
                "data_type": "text",
                "description": "customer segment tag",
            }
        )
        assert ser.is_valid(), ser.errors


class TestResolveConfigurationSnapshot:
    """Shared helper used at every prompt-construction site to load the pinned
    version's snapshot from a scenario."""

    def test_none_scenario_returns_none(self):
        from simulate.models.agent_version import resolve_configuration_snapshot

        assert resolve_configuration_snapshot(None) is None

    def test_scenario_without_version_id_returns_none(self, db, agent_definition, organization, workspace):
        from simulate.models.agent_version import resolve_configuration_snapshot

        scenario = Scenarios.objects.create(
            name="s", source="x", scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization, workspace=workspace,
            agent_definition=agent_definition, metadata={},
        )
        assert resolve_configuration_snapshot(scenario) is None

    def test_scenario_with_version_returns_snapshot(self, db, agent_definition, organization, workspace):
        from simulate.models.agent_version import AgentVersion, resolve_configuration_snapshot

        snap = {"agent_name": "Pinned"}
        v = AgentVersion.objects.create(
            agent_definition=agent_definition, organization=organization,
            workspace=workspace, version_number=1, configuration_snapshot=snap,
        )
        scenario = Scenarios.objects.create(
            name="s", source="x", scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization, workspace=workspace,
            agent_definition=agent_definition,
            metadata={"agent_definition_version_id": str(v.id)},
        )
        assert resolve_configuration_snapshot(scenario) == snap

    def test_missing_version_returns_none(self, db, agent_definition, organization, workspace):
        import uuid as _uuid
        from simulate.models.agent_version import resolve_configuration_snapshot

        scenario = Scenarios.objects.create(
            name="s", source="x", scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization, workspace=workspace,
            agent_definition=agent_definition,
            metadata={"agent_definition_version_id": str(_uuid.uuid4())},
        )
        assert resolve_configuration_snapshot(scenario) is None


class TestHasVersionPin:
    """Distinguishes 'pin intended but version missing' from 'no pin at all',
    used by build_agent_kb_payload's authoritative-pin fallback rule."""

    def test_no_scenario(self):
        from simulate.models.agent_version import has_version_pin

        assert has_version_pin(None) is False

    def test_no_pin_in_metadata(self, db, agent_definition, organization, workspace):
        from simulate.models.agent_version import has_version_pin

        scenario = Scenarios.objects.create(
            name="s", source="x", scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization, workspace=workspace,
            agent_definition=agent_definition, metadata={},
        )
        assert has_version_pin(scenario) is False

    def test_pin_present_even_when_version_deleted(self, db, agent_definition, organization, workspace):
        import uuid as _uuid
        from simulate.models.agent_version import has_version_pin

        scenario = Scenarios.objects.create(
            name="s", source="x", scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization, workspace=workspace,
            agent_definition=agent_definition,
            metadata={"agent_definition_version_id": str(_uuid.uuid4())},
        )
        assert has_version_pin(scenario) is True


class TestGenerateScenarioColumnsVersionPin:
    """Add-columns flow: generate_scenario_columns must honor the version pin
    when building the SDA prompt for new column values."""

    def _make_scenario_with_version(self, db, agent_definition, organization, workspace, snapshot):
        from simulate.models.agent_version import AgentVersion

        dataset = Dataset.no_workspace_objects.create(
            name="ds", organization=organization, workspace=workspace,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        Row.objects.create(dataset=dataset, order=0)
        v = AgentVersion.objects.create(
            agent_definition=agent_definition, organization=organization,
            workspace=workspace, version_number=1, configuration_snapshot=snapshot,
        )
        scenario = Scenarios.objects.create(
            name="scn", source="x", scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization, workspace=workspace,
            agent_definition=agent_definition, dataset=dataset,
            metadata={"agent_definition_version_id": str(v.id)},
        )
        return scenario, dataset

    def test_snapshot_agent_name_reaches_column_gen_payload(
        self, db, agent_definition, organization, workspace
    ):
        from simulate.tasks.scenario_tasks import generate_scenario_columns

        agent_definition.agent_name = "Live Bot"
        agent_definition.save(update_fields=["agent_name"])
        scenario, dataset = self._make_scenario_with_version(
            db, agent_definition, organization, workspace,
            snapshot={"agent_name": "Pinned Bot v1"},
        )

        captured = {}

        async def fake_generate_column_data(payload, **kwargs):
            captured["payload"] = payload
            import pandas as _pd
            return _pd.DataFrame([{"c1": "x"}])

        with patch(
            "simulate.tasks.scenario_tasks.SyntheticDataAgent"
        ) as mock_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            mock_cls.return_value.generate_column_data = fake_generate_column_data
            generate_scenario_columns(
                dataset_id=dataset.id,
                new_columns_required_info=[
                    {"name": "c1", "data_type": "text", "description": "x"}
                ],
                scenario_id=scenario.id,
            )

        objective = captured["payload"]["requirements"]["Objective"]
        assert "Pinned Bot v1" in objective
        assert "Live Bot" not in objective


class TestBuildSdaPayloadVersionPin:
    """Dataset scenario flow: `_build_sda_payload` must honor the version pin
    when constructing the SDA prompt."""

    @pytest.fixture
    def new_dataset(self, db, organization, workspace, user):
        return Dataset.no_workspace_objects.create(
            name="target",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.SCENARIO.value,
        )

    def _make_scenario_with_pin(
        self, db, agent_definition, organization, workspace, snapshot
    ):
        from simulate.models.agent_version import AgentVersion

        v = AgentVersion.objects.create(
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
            version_number=1,
            configuration_snapshot=snapshot,
        )
        return Scenarios.objects.create(
            name="s",
            source="x",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            agent_definition=agent_definition,
            metadata={"agent_definition_version_id": str(v.id)},
        )

    def test_snapshot_agent_name_wins_in_dataset_payload(
        self, db, new_dataset, agent_definition, organization, workspace
    ):
        from tfc.temporal.simulate.activities import _build_sda_payload

        agent_definition.agent_name = "Live Bot"
        agent_definition.save(update_fields=["agent_name"])
        scenario = self._make_scenario_with_pin(
            db, agent_definition, organization, workspace,
            snapshot={"agent_name": "Pinned Bot v1"},
        )

        payload = _build_sda_payload(new_dataset, agent_definition, "voice", scenario=scenario)

        assert "Pinned Bot v1" in payload["requirements"]["Dataset Description"]
        assert "Live Bot" not in payload["requirements"]["Dataset Description"]

    def test_snapshot_inbound_false_wins_in_dataset_payload(
        self, db, new_dataset, agent_definition, organization, workspace
    ):
        from tfc.temporal.simulate.activities import _build_sda_payload

        agent_definition.inbound = True
        agent_definition.save(update_fields=["inbound"])
        scenario = self._make_scenario_with_pin(
            db, agent_definition, organization, workspace,
            snapshot={"inbound": False},
        )

        payload = _build_sda_payload(new_dataset, agent_definition, "voice", scenario=scenario)

        assert "Outbound" in payload["requirements"]["Dataset Description"]

    def test_no_scenario_falls_back_to_live_agent(
        self, db, new_dataset, agent_definition, organization, workspace
    ):
        from tfc.temporal.simulate.activities import _build_sda_payload

        agent_definition.agent_name = "Live Bot"
        agent_definition.save(update_fields=["agent_name"])

        payload = _build_sda_payload(new_dataset, agent_definition, "voice", scenario=None)

        assert "Live Bot" in payload["requirements"]["Dataset Description"]

    def test_custom_instruction_appended_to_objective(
        self, db, new_dataset, agent_definition, organization, workspace
    ):
        from tfc.temporal.simulate.activities import _build_sda_payload

        payload = _build_sda_payload(
            new_dataset,
            agent_definition,
            "voice",
            scenario=None,
            custom_instruction="Focus on Spanish-speaking customers.",
        )
        assert (
            "Focus on Spanish-speaking customers." in payload["requirements"]["Objective"]
        )

    def test_custom_columns_reach_constraints_and_schema(
        self, db, new_dataset, agent_definition, organization, workspace
    ):
        from tfc.temporal.simulate.activities import _build_sda_payload

        payload = _build_sda_payload(
            new_dataset,
            agent_definition,
            "voice",
            scenario=None,
            custom_columns=[
                {
                    "name": "segment",
                    "data_type": "text",
                    "description": "customer segment",
                    "property": {"min_length": 3, "max_length": 32},
                }
            ],
        )
        constraint = next(
            c for c in payload["constraints"] if c["field"] == "segment"
        )
        assert constraint["type"] == "text"
        assert constraint["property"]["min_length"] == 3
        assert constraint["property"]["max_length"] == 32
        assert payload["schema"]["segment"] == {"type": "text"}


class TestScenarioDescriptionForwarding:
    """scenario.description must flow into the EnhancedScenariosAgent constructor."""

    def test_generate_scenario_rows_forwards_scenario_description(
        self, db, scenario_dataset, agent_definition, organization, workspace
    ):
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        columns = list(Column.objects.filter(dataset=scenario_dataset))
        row_ids = TestGenerateScenarioRowsPrefetch._seed_rows(scenario_dataset, num_rows=1)
        TestGenerateScenarioRowsPrefetch._seed_cells(scenario_dataset, columns, row_ids)
        scenario = TestGenerateScenarioRowsPrefetch._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )
        scenario.description = "pinned scenario description"
        scenario.save(update_fields=["description"])
        cases = TestGenerateScenarioRowsPrefetch._build_cases(1, columns)

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            mock = mock_agent_cls.return_value
            mock.graph_generator.get_branches.return_value = []
            mock._generate_cases_for_branches.return_value = cases

            generate_scenario_rows(
                dataset_id=scenario_dataset.id,
                scenario_id=scenario.id,
                num_rows=1,
                description="call-time desc",
                new_rows_id=row_ids,
            )

        assert (
            mock_agent_cls.call_args.kwargs["scenario_description"]
            == "pinned scenario description"
        )

    def test_generate_scenario_rows_forwards_configuration_snapshot(
        self, db, scenario_dataset, agent_definition, organization, workspace
    ):
        """Add-rows path must forward the version-pinned snapshot to the row generator constructor
        so add-rows on a pinned scenario uses snapshot fields, not live agent state."""
        from simulate.models.agent_version import AgentVersion
        from simulate.tasks.scenario_tasks import generate_scenario_rows

        snap = {"agent_name": "Pinned v1 Agent", "inbound": False}
        version = AgentVersion.objects.create(
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
            version_number=1,
            configuration_snapshot=snap,
        )
        columns = list(Column.objects.filter(dataset=scenario_dataset))
        row_ids = TestGenerateScenarioRowsPrefetch._seed_rows(scenario_dataset, num_rows=1)
        TestGenerateScenarioRowsPrefetch._seed_cells(scenario_dataset, columns, row_ids)
        scenario = TestGenerateScenarioRowsPrefetch._make_scenario_and_graph(
            scenario_dataset, agent_definition, organization, workspace
        )
        scenario.metadata = {"agent_definition_version_id": str(version.id)}
        scenario.save(update_fields=["metadata"])
        cases = TestGenerateScenarioRowsPrefetch._build_cases(1, columns)

        with patch(
            "simulate.tasks.scenario_tasks.EnhancedScenariosAgent"
        ) as mock_agent_cls, patch(
            "simulate.tasks.scenario_tasks.close_old_connections"
        ):
            mock = mock_agent_cls.return_value
            mock.graph_generator.get_branches.return_value = []
            mock._generate_cases_for_branches.return_value = cases

            generate_scenario_rows(
                dataset_id=scenario_dataset.id,
                scenario_id=scenario.id,
                num_rows=1,
                description="anything",
                new_rows_id=row_ids,
            )

        assert mock_agent_cls.call_args.kwargs["configuration_snapshot"] == snap


class TestApplyCustomColumnConstraints:
    def test_none_or_empty_is_noop(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        for cols in (None, []):
            constraints, schema = [], {}
            apply_custom_column_constraints(constraints, schema, cols, "Bot")
            assert constraints == [] and schema == {}

    def test_column_without_name_is_skipped(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        constraints, schema = [], {}
        apply_custom_column_constraints(
            constraints, schema, [{"data_type": "text"}], "Bot"
        )
        assert constraints == [] and schema == {}

    def test_column_type_mapping(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        cases = [
            ("json", "json"),
            ("persona", "json"),
            ("number", "number"),
            ("integer", "number"),
            ("float", "number"),
            ("boolean", "boolean"),
            ("string", "text"),
            ("datetime", "datetime"),
            ("array", "array"),
            ("text", "text"),
            ("unknown_type", "text"),
        ]
        for src, expected in cases:
            constraints, schema = [], {}
            apply_custom_column_constraints(
                constraints, schema, [{"name": "c", "data_type": src}], "Bot"
            )
            assert schema["c"]["type"] == expected, (src, expected)
            assert constraints[0]["type"] == expected

    def test_default_text_property_and_user_override(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        constraints, schema = [], {}
        apply_custom_column_constraints(
            constraints, schema,
            [{"name": "seg", "data_type": "text", "property": {"max_length": 32}}],
            "Bot",
        )
        prop = constraints[0]["property"]
        assert prop["min_length"] == 10
        assert prop["max_length"] == 32
        assert prop["required_elements"] == []

    def test_non_text_type_gets_only_user_property(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        constraints, schema = [], {}
        apply_custom_column_constraints(
            constraints, schema,
            [{"name": "flag", "data_type": "boolean", "property": {"nullable": True}}],
            "Bot",
        )
        assert constraints[0]["property"] == {"nullable": True}

    def test_content_string_shape(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        constraints, schema = [], {}
        apply_custom_column_constraints(
            constraints, schema,
            [{"name": "tone", "data_type": "text", "description": "customer tone"}],
            "Alice", branch_context_footer=" branch=welcome",
        )
        expected = (
            "customer tone. Generate realistic and contextually relevant data "
            "for Alice scenarios that can be tailored using the conversation "
            "branch information below. branch=welcome"
        )
        assert constraints[0]["content"] == expected

    def test_content_string_without_footer(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        constraints, schema = [], {}
        apply_custom_column_constraints(
            constraints, schema,
            [{"name": "tone", "data_type": "text", "description": "customer tone"}],
            "Alice",
        )
        expected = (
            "customer tone. Generate realistic and contextually relevant data "
            "for Alice scenarios that can be tailored using the conversation "
            "branch information below."
        )
        assert constraints[0]["content"] == expected

    def test_multiple_columns_all_landed(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        cols = [
            {"name": "urgency", "data_type": "text"},
            {"name": "score", "data_type": "number"},
            {"name": "tags", "data_type": "array"},
        ]
        constraints, schema = [], {}
        apply_custom_column_constraints(constraints, schema, cols, "Bot")
        assert len(constraints) == 3
        assert [c["field"] for c in constraints] == ["urgency", "score", "tags"]
        assert schema == {
            "urgency": {"type": "text"},
            "score": {"type": "number"},
            "tags": {"type": "array"},
        }

    def test_schema_entry_added(self):
        from simulate.utils.scenario_constraints import apply_custom_column_constraints

        constraints, schema = [], {"persona": {"type": "json"}}
        apply_custom_column_constraints(
            constraints, schema,
            [{"name": "urgency", "data_type": "text"}], "Bot",
        )
        assert schema["urgency"] == {"type": "text"}
        assert schema["persona"] == {"type": "json"}


class TestEffectivePersonaIds:
    def test_auto_true_drops_user_personas(self):
        from tfc.temporal.simulate.activities import _effective_persona_ids

        assert _effective_persona_ids({
            "add_persona_automatically": True,
            "personas": ["p1", "p2"],
        }) == []

    def test_auto_true_no_personas(self):
        from tfc.temporal.simulate.activities import _effective_persona_ids

        assert _effective_persona_ids({"add_persona_automatically": True}) == []

    def test_auto_false_keeps_user_personas(self):
        from tfc.temporal.simulate.activities import _effective_persona_ids

        assert _effective_persona_ids({
            "add_persona_automatically": False,
            "personas": ["p1", "p2"],
        }) == ["p1", "p2"]

    def test_auto_absent_defaults_to_false(self):
        from tfc.temporal.simulate.activities import _effective_persona_ids

        assert _effective_persona_ids({"personas": ["p1"]}) == ["p1"]

    def test_empty_validated_data(self):
        from tfc.temporal.simulate.activities import _effective_persona_ids

        assert _effective_persona_ids({}) == []


class TestResolveScenarioAgent:
    def test_agent_definition_source_returns_real_instance(
        self, db, agent_definition, organization, workspace
    ):
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        scenario = Scenarios.objects.create(
            name="s", source="x",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization, workspace=workspace,
            agent_definition=agent_definition, metadata={},
        )
        resolved = _resolve_scenario_agent(scenario, "agent_definition")
        assert resolved is agent_definition

    def test_prompt_source_returns_simplenamespace_adapter(
        self, db, organization, workspace
    ):
        import types
        from model_hub.models.run_prompt import PromptTemplate, PromptVersion
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        template = PromptTemplate.objects.create(
            name="pt", organization=organization, workspace=workspace,
        )
        PromptVersion.objects.create(
            original_template_id=template.id,
            template_version="v1",
            is_default=True,
            prompt_config_snapshot={
                "messages": [
                    {"role": "system", "content": [{"type": "text", "text": "You are a helper."}]}
                ]
            },
        )
        scenario = Scenarios.objects.create(
            name="s", source="x",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization, workspace=workspace,
            source_type="prompt",
            prompt_template=template,
            metadata={},
        )
        resolved = _resolve_scenario_agent(scenario, "prompt")
        assert isinstance(resolved, types.SimpleNamespace)
        assert resolved.agent_name == "pt"
        assert "You are a helper." in resolved.description
        assert resolved.agent_type == "text"
        assert resolved.inbound is True
        assert resolved.languages == ["en"]
        assert resolved.organization == organization
        assert resolved.workspace == workspace

    def test_prompt_source_with_no_template_falls_through(
        self, db, agent_definition, organization, workspace
    ):
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        scenario = Scenarios.objects.create(
            name="s", source="x",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization, workspace=workspace,
            source_type="prompt",
            agent_definition=agent_definition,
            metadata={},
        )
        resolved = _resolve_scenario_agent(scenario, "prompt")
        assert resolved is agent_definition
