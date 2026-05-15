"""
Unit tests for Scenarios serializers in the simulate app.

Tests cover:
- CreateScenarioSerializer: Input validation for creating scenarios
- AddScenarioRowsSerializer: Input validation for adding rows
- AddScenarioColumnsSerializer: Input validation for adding columns
- EditScenarioSerializer: Input validation for editing scenarios
- ScenariosSerializer: Output serialization
"""

import uuid
from unittest.mock import MagicMock

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Dataset
from simulate.serializers.scenarios import (
    AddScenarioColumnsSerializer,
    AddScenarioRowsSerializer,
    CreateScenarioSerializer,
    EditScenarioPromptsSerializer,
    EditScenarioSerializer,
    ScenariosSerializer,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def request_factory():
    """API request factory for creating mock requests."""
    return APIRequestFactory()


@pytest.fixture
def mock_request(request_factory, user):
    """Create a mock request with authenticated user."""
    request = request_factory.post("/simulate/scenarios/create/")
    # Create the DRF Request wrapper first
    drf_request = Request(request)
    # Then set the user on the DRF request (not the Django request)
    drf_request._user = user
    drf_request._request.user = user
    return drf_request


@pytest.fixture
def source_dataset(db, organization, workspace, user):
    """Create a source dataset for scenario creation tests."""
    return Dataset.no_workspace_objects.create(
        name="Source Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )


# ============================================================================
# CreateScenarioSerializer Tests
# ============================================================================


@pytest.mark.unit
class TestCreateScenarioSerializer:
    """Tests for CreateScenarioSerializer validation logic."""

    def test_create_scenario_serializer_dataset_valid(
        self, mock_request, source_dataset
    ):
        """Valid dataset scenario input should pass validation."""
        data = {
            "name": "Test Dataset Scenario",
            "description": "A test scenario from dataset",
            "kind": "dataset",
            "dataset_id": str(source_dataset.id),
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        assert validated["name"] == "Test Dataset Scenario"
        assert validated["kind"] == "dataset"
        assert validated["dataset_id"] == source_dataset.id

    def test_create_scenario_serializer_script_valid(self, mock_request):
        """Valid script scenario input should pass validation."""
        data = {
            "name": "Test Script Scenario",
            "description": "A test scenario from script",
            "kind": "script",
            "script_url": "https://example.com/script.py",
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        assert validated["name"] == "Test Script Scenario"
        assert validated["kind"] == "script"
        assert validated["script_url"] == "https://example.com/script.py"

    def test_create_scenario_serializer_graph_valid_provided(self, mock_request):
        """Valid graph scenario with provided graph data should pass validation."""
        data = {
            "name": "Test Graph Scenario",
            "description": "A test scenario with graph",
            "kind": "graph",
            "graph": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [{"source": "start", "target": "end"}],
            },
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        assert validated["name"] == "Test Graph Scenario"
        assert validated["kind"] == "graph"
        assert "nodes" in validated["graph"]

    def test_create_scenario_serializer_graph_valid_generated(self, mock_request):
        """Valid graph scenario with generate_graph=True should pass validation."""
        agent_def_id = uuid.uuid4()
        data = {
            "name": "Test Generated Graph Scenario",
            "kind": "graph",
            "generate_graph": True,
            "agent_definition_id": str(agent_def_id),
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        assert validated["generate_graph"] is True
        assert validated["agent_definition_id"] == agent_def_id

    def test_create_scenario_serializer_missing_name(self, mock_request):
        """Missing name should fail validation."""
        data = {
            "kind": "dataset",
            "dataset_id": str(uuid.uuid4()),
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_create_scenario_serializer_empty_name(self, mock_request):
        """Empty or whitespace-only name should fail validation."""
        data = {
            "name": "   ",
            "kind": "dataset",
            "dataset_id": str(uuid.uuid4()),
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "name" in serializer.errors
        # Error can be "empty", "blank", or "whitespace"
        error_msg = str(serializer.errors["name"][0]).lower()
        assert "empty" in error_msg or "blank" in error_msg or "whitespace" in error_msg

    def test_create_scenario_serializer_invalid_kind(self, mock_request):
        """Invalid kind value should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "invalid_type",
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "kind" in serializer.errors

    def test_create_scenario_serializer_dataset_missing_dataset_id(self, mock_request):
        """Dataset kind without dataset_id should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "dataset",
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors
        assert "dataset_id" in str(serializer.errors["non_field_errors"][0]).lower()

    def test_create_scenario_serializer_script_missing_url(self, mock_request):
        """Script kind without script_url should fail validation."""
        data = {
            "name": "Test Script Scenario",
            "kind": "script",
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors
        assert "script_url" in str(serializer.errors["non_field_errors"][0]).lower()

    def test_create_scenario_serializer_graph_missing_requirements(self, mock_request):
        """Graph kind without graph data or generate_graph should fail validation."""
        data = {
            "name": "Test Graph Scenario",
            "kind": "graph",
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors

    def test_create_scenario_serializer_graph_generate_missing_agent(
        self, mock_request
    ):
        """Graph kind with generate_graph=True but no agent_definition_id should fail."""
        data = {
            "name": "Test Graph Scenario",
            "kind": "graph",
            "generate_graph": True,
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors
        assert (
            "agent_definition_id"
            in str(serializer.errors["non_field_errors"][0]).lower()
        )

    def test_create_scenario_serializer_custom_columns_valid(self, mock_request):
        """Valid custom columns should pass validation."""
        data = {
            "name": "Test Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "custom_columns": [
                {
                    "name": "custom_field",
                    "data_type": "text",
                    "description": "A custom text field",
                },
                {
                    "name": "score",
                    "data_type": "integer",
                    "description": "A score value",
                },
            ],
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["custom_columns"]) == 2

    def test_create_scenario_serializer_custom_columns_max_limit(self, mock_request):
        """More than 10 custom columns should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "custom_columns": [
                {"name": f"col_{i}", "data_type": "text", "description": f"Column {i}"}
                for i in range(11)
            ],
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "custom_columns" in serializer.errors

    def test_create_scenario_serializer_custom_columns_invalid_data_type(
        self, mock_request
    ):
        """Invalid data type in custom columns should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "custom_columns": [
                {
                    "name": "bad_col",
                    "data_type": "invalid_type",
                    "description": "Bad column",
                },
            ],
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "custom_columns" in serializer.errors

    def test_create_scenario_serializer_custom_columns_missing_name(self, mock_request):
        """Custom column without name should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "custom_columns": [
                {"data_type": "text", "description": "Missing name"},
            ],
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "custom_columns" in serializer.errors

    def test_create_scenario_serializer_custom_columns_missing_data_type(
        self, mock_request
    ):
        """Custom column without data_type should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "custom_columns": [
                {"name": "col1", "description": "Missing data_type"},
            ],
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "custom_columns" in serializer.errors

    def test_create_scenario_serializer_custom_columns_missing_description(
        self, mock_request
    ):
        """Custom column without description should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "custom_columns": [
                {"name": "col1", "data_type": "text"},
            ],
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "custom_columns" in serializer.errors

    def test_create_scenario_serializer_custom_columns_whitespace_name(
        self, mock_request
    ):
        """Custom column with whitespace-only name should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "custom_columns": [
                {"name": "   ", "data_type": "text", "description": "Whitespace name"},
            ],
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "custom_columns" in serializer.errors

    def test_create_scenario_serializer_dataset_not_found(self, mock_request):
        """Non-existent dataset_id should fail validation."""
        data = {
            "name": "Test Scenario",
            "kind": "dataset",
            "dataset_id": str(uuid.uuid4()),  # Random UUID that doesn't exist
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert not serializer.is_valid()
        assert "dataset_id" in serializer.errors

    def test_create_scenario_serializer_with_simulator_agent_fields(
        self, mock_request, source_dataset
    ):
        """Simulator agent fields should be accepted and validated."""
        data = {
            "name": "Test Scenario with Agent",
            "kind": "dataset",
            "dataset_id": str(source_dataset.id),
            "agent_name": "Custom Agent",
            "agent_prompt": "You are a helpful assistant.",
            "voice_provider": "elevenlabs",
            "voice_name": "adam",
            "model": "gpt-4",
            "llm_temperature": 0.8,
            "initial_message": "Hello!",
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["agent_name"] == "Custom Agent"
        assert serializer.validated_data["llm_temperature"] == 0.8

    def test_create_scenario_serializer_default_values(
        self, mock_request, source_dataset
    ):
        """Default values should be applied when not provided."""
        data = {
            "name": "Test Scenario",
            "dataset_id": str(source_dataset.id),
        }

        serializer = CreateScenarioSerializer(
            data=data, context={"request": mock_request}
        )
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        assert validated["kind"] == "dataset"  # Default kind
        assert validated["no_of_rows"] == 20  # Default no_of_rows
        assert validated["generate_graph"] is False  # Default generate_graph


# ============================================================================
# AddScenarioRowsSerializer Tests
# ============================================================================


@pytest.mark.unit
class TestAddScenarioRowsSerializer:
    """Tests for AddScenarioRowsSerializer validation logic."""

    def test_add_rows_serializer_valid(self):
        """Valid add rows input should pass validation."""
        data = {
            "num_rows": 10,
            "description": "Additional test rows",
        }

        serializer = AddScenarioRowsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["num_rows"] == 10

    def test_add_rows_serializer_min_valid(self):
        """Minimum valid num_rows (10) should pass validation."""
        data = {"num_rows": 10}

        serializer = AddScenarioRowsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_add_rows_serializer_max_valid(self):
        """Maximum valid num_rows (20000) should pass validation."""
        data = {"num_rows": 20000}

        serializer = AddScenarioRowsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_add_rows_serializer_min_rows_violation(self):
        """num_rows less than 10 should fail validation."""
        data = {"num_rows": 9}

        serializer = AddScenarioRowsSerializer(data=data)
        assert not serializer.is_valid()
        assert "num_rows" in serializer.errors

    def test_add_rows_serializer_max_rows_violation(self):
        """num_rows greater than 20000 should fail validation."""
        data = {"num_rows": 20001}

        serializer = AddScenarioRowsSerializer(data=data)
        assert not serializer.is_valid()
        assert "num_rows" in serializer.errors

    def test_add_rows_serializer_negative_rows(self):
        """Negative num_rows should fail validation."""
        data = {"num_rows": -5}

        serializer = AddScenarioRowsSerializer(data=data)
        assert not serializer.is_valid()
        assert "num_rows" in serializer.errors

    def test_add_rows_serializer_missing_num_rows(self):
        """Missing num_rows should fail validation."""
        data = {"description": "No rows specified"}

        serializer = AddScenarioRowsSerializer(data=data)
        assert not serializer.is_valid()
        assert "num_rows" in serializer.errors

    def test_add_rows_serializer_description_optional(self):
        """Description should be optional."""
        data = {"num_rows": 10}

        serializer = AddScenarioRowsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors


# ============================================================================
# AddScenarioColumnsSerializer Tests
# ============================================================================


@pytest.mark.unit
class TestAddScenarioColumnsSerializer:
    """Tests for AddScenarioColumnsSerializer validation logic."""

    def test_add_columns_serializer_valid(self):
        """Valid add columns input should pass validation."""
        data = {
            "columns": [
                {
                    "name": "custom_field",
                    "data_type": "text",
                    "description": "A custom text field",
                },
                {
                    "name": "score",
                    "data_type": "integer",
                    "description": "A score value",
                },
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["columns"]) == 2

    def test_add_columns_serializer_single_column(self):
        """Single column should pass validation."""
        data = {
            "columns": [
                {"name": "new_col", "data_type": "text", "description": "New column"},
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_add_columns_serializer_empty_list(self):
        """Empty columns list should fail validation."""
        data = {"columns": []}

        serializer = AddScenarioColumnsSerializer(data=data)
        assert not serializer.is_valid()
        assert "columns" in serializer.errors

    def test_add_columns_serializer_max_columns(self):
        """Maximum 10 columns should pass validation."""
        data = {
            "columns": [
                {"name": f"col_{i}", "data_type": "text", "description": f"Column {i}"}
                for i in range(10)
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_add_columns_serializer_exceed_max_columns(self):
        """More than 10 columns should fail validation."""
        data = {
            "columns": [
                {"name": f"col_{i}", "data_type": "text", "description": f"Column {i}"}
                for i in range(11)
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert not serializer.is_valid()
        assert "columns" in serializer.errors

    def test_add_columns_serializer_invalid_data_type(self):
        """Invalid data type should fail validation."""
        data = {
            "columns": [
                {
                    "name": "bad_col",
                    "data_type": "invalid_type",
                    "description": "Bad column",
                },
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert not serializer.is_valid()
        assert "columns" in serializer.errors

    def test_add_columns_serializer_missing_name(self):
        """Column without name should fail validation."""
        data = {
            "columns": [
                {"data_type": "text", "description": "Missing name"},
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert not serializer.is_valid()
        assert "columns" in serializer.errors

    def test_add_columns_serializer_missing_data_type(self):
        """Column without data_type should fail validation."""
        data = {
            "columns": [
                {"name": "col1", "description": "Missing data_type"},
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert not serializer.is_valid()
        assert "columns" in serializer.errors

    def test_add_columns_serializer_missing_description(self):
        """Column without description should fail validation."""
        data = {
            "columns": [
                {"name": "col1", "data_type": "text"},
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert not serializer.is_valid()
        assert "columns" in serializer.errors

    def test_add_columns_serializer_whitespace_name(self):
        """Column with whitespace-only name should fail validation."""
        data = {
            "columns": [
                {"name": "   ", "data_type": "text", "description": "Whitespace name"},
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert not serializer.is_valid()
        assert "columns" in serializer.errors

    def test_add_columns_serializer_all_valid_data_types(self):
        """All valid data types should pass validation."""
        valid_types = [
            "text",
            "boolean",
            "integer",
            "float",
            "json",
            "array",
            "image",
            "datetime",
            "audio",
            "document",
        ]
        data = {
            "columns": [
                {
                    "name": f"col_{dtype}",
                    "data_type": dtype,
                    "description": f"Column of type {dtype}",
                }
                for dtype in valid_types
            ]
        }

        serializer = AddScenarioColumnsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors


# ============================================================================
# EditScenarioSerializer Tests
# ============================================================================


@pytest.mark.unit
class TestEditScenarioSerializer:
    """Tests for EditScenarioSerializer validation logic."""

    def test_edit_scenario_serializer_valid(self):
        """Valid edit input should pass validation."""
        data = {
            "name": "Updated Scenario Name",
            "description": "Updated description",
        }

        serializer = EditScenarioSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["name"] == "Updated Scenario Name"

    def test_edit_scenario_serializer_name_only(self):
        """Editing name only should pass validation."""
        data = {"name": "New Name"}

        serializer = EditScenarioSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_edit_scenario_serializer_description_only(self):
        """Editing description only should pass validation."""
        data = {"description": "New description"}

        serializer = EditScenarioSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_edit_scenario_serializer_empty_name(self):
        """Empty or whitespace-only name should fail validation."""
        data = {"name": "   "}

        serializer = EditScenarioSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_edit_scenario_serializer_with_graph(self):
        """Editing with graph data should pass validation."""
        data = {
            "name": "Updated",
            "graph": {"nodes": [{"id": "new"}], "edges": []},
        }

        serializer = EditScenarioSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_edit_scenario_serializer_with_prompt(self):
        """Editing with prompt should pass validation."""
        data = {
            "name": "Updated",
            "prompt": "You are an updated assistant.",
        }

        serializer = EditScenarioSerializer(data=data)
        assert serializer.is_valid(), serializer.errors


# ============================================================================
# EditScenarioPromptsSerializer Tests
# ============================================================================


@pytest.mark.unit
class TestEditScenarioPromptsSerializer:
    """Tests for EditScenarioPromptsSerializer validation logic."""

    def test_edit_prompts_serializer_valid(self):
        """Valid prompts input should pass validation."""
        data = {"prompts": "You are a helpful assistant for customer support."}

        serializer = EditScenarioPromptsSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_edit_prompts_serializer_missing_prompts(self):
        """Missing prompts should fail validation."""
        data = {}

        serializer = EditScenarioPromptsSerializer(data=data)
        assert not serializer.is_valid()
        assert "prompts" in serializer.errors

    def test_edit_prompts_serializer_max_length(self):
        """Prompts exceeding max length should fail validation."""
        data = {"prompts": "x" * 10001}  # Max is 10000

        serializer = EditScenarioPromptsSerializer(data=data)
        assert not serializer.is_valid()
        assert "prompts" in serializer.errors
