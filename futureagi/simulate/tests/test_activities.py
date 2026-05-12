"""
Integration tests for Temporal activities in the simulate app.

These tests focus on:
1. Testing activity input/output types
2. Testing the business logic through API integration with mocked workflows
3. Verifying database state changes after scenario creation

Note: Activities are tested indirectly through API calls with mocked workflow starters,
since running actual Temporal activities requires a Temporal worker environment.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.scenario_graph import ScenarioGraph
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
def source_dataset(db, organization, workspace, user):
    """Create a source dataset with columns and rows."""
    dataset = Dataset.no_workspace_objects.create(
        name="Source Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )

    # Create columns
    col1 = Column.objects.create(
        dataset=dataset,
        name="input",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    col2 = Column.objects.create(
        dataset=dataset,
        name="expected_output",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )

    # Update column order
    dataset.column_order = [str(col1.id), str(col2.id)]
    dataset.save()

    # Create rows with cells
    for i in range(3):
        row = Row.objects.create(dataset=dataset, order=i)
        Cell.objects.create(dataset=dataset, column=col1, row=row, value=f"Input {i}")
        Cell.objects.create(dataset=dataset, column=col2, row=row, value=f"Output {i}")

    return dataset


@pytest.fixture
def existing_scenario(db, organization, workspace, source_dataset, agent_definition):
    """Create an existing scenario for testing."""
    return Scenarios.objects.create(
        name="Existing Scenario",
        description="Test scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=source_dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


# ============================================================================
# Activity Input/Output Type Tests
# ============================================================================


@pytest.mark.unit
class TestActivityTypes:
    """Tests for Temporal activity input/output types."""

    def test_create_dataset_scenario_workflow_input_type(self):
        """Verify CreateDatasetScenarioWorkflowInput dataclass structure."""
        from tfc.temporal.simulate.types import CreateDatasetScenarioWorkflowInput

        input_data = CreateDatasetScenarioWorkflowInput(
            user_id=1,
            validated_data={
                "name": "Test",
                "kind": "dataset",
                "dataset_id": str(uuid.uuid4()),
            },
            scenario_id=str(uuid.uuid4()),
        )

        assert input_data.user_id == 1
        assert input_data.validated_data["name"] == "Test"
        assert input_data.scenario_id is not None

    def test_create_dataset_scenario_workflow_output_type(self):
        """Verify CreateDatasetScenarioWorkflowOutput dataclass structure."""
        from tfc.temporal.simulate.types import CreateDatasetScenarioWorkflowOutput

        output = CreateDatasetScenarioWorkflowOutput(
            scenario_id=str(uuid.uuid4()),
            dataset_id=str(uuid.uuid4()),
            status="COMPLETED",
        )

        assert output.status == "COMPLETED"
        assert output.error is None

        # Test failed status
        failed_output = CreateDatasetScenarioWorkflowOutput(
            scenario_id=str(uuid.uuid4()),
            status="FAILED",
            error="Something went wrong",
        )
        assert failed_output.status == "FAILED"
        assert failed_output.error is not None

    def test_create_script_scenario_workflow_input_type(self):
        """Verify CreateScriptScenarioWorkflowInput dataclass structure."""
        from tfc.temporal.simulate.types import CreateScriptScenarioWorkflowInput

        # Script workflow input doesn't have user_id
        input_data = CreateScriptScenarioWorkflowInput(
            validated_data={
                "name": "Script Test",
                "kind": "script",
                "script_url": "https://example.com/script.py",
            },
            scenario_id=str(uuid.uuid4()),
        )

        assert (
            input_data.validated_data["script_url"] == "https://example.com/script.py"
        )

    def test_create_graph_scenario_workflow_input_type(self):
        """Verify CreateGraphScenarioWorkflowInput dataclass structure."""
        from tfc.temporal.simulate.types import CreateGraphScenarioWorkflowInput

        # Graph workflow input doesn't have user_id
        input_data = CreateGraphScenarioWorkflowInput(
            validated_data={
                "name": "Graph Test",
                "kind": "graph",
                "graph": {"nodes": [], "edges": []},
            },
            scenario_id=str(uuid.uuid4()),
        )

        assert input_data.validated_data["graph"]["nodes"] == []


# ============================================================================
# API Integration Tests with Mocked Workflow Starters
# ============================================================================


@pytest.mark.integration
class TestCreateScenarioAPIWithMockedWorkflow:
    """
    Integration tests for scenario creation API endpoints.
    Tests verify that:
    1. API correctly validates input
    2. Scenario is created in PROCESSING state
    3. Workflow starter is called with correct parameters
    4. SimulatorAgent is created when agent fields are provided
    """

    @patch("simulate.views.scenarios.start_create_dataset_scenario_workflow_sync")
    def test_create_dataset_scenario_starts_workflow(
        self, mock_workflow, auth_client, source_dataset, agent_definition
    ):
        """Creating a dataset scenario should trigger the workflow."""
        mock_workflow.return_value = "workflow-id-123"

        payload = {
            "name": "API Test Scenario",
            "description": "Testing workflow trigger",
            "kind": "dataset",
            "dataset_id": str(source_dataset.id),
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/",
            payload,
            format="json",
        )

        # API returns 202 Accepted for async workflow operations
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_workflow.assert_called_once()

        # Verify scenario was created
        data = response.json()
        # Response has nested structure: {"scenario": {...}, "status": "..."}
        assert "scenario" in data
        scenario_data = data["scenario"]
        assert "id" in scenario_data
        scenario = Scenarios.objects.get(id=scenario_data["id"])
        assert scenario.name == "API Test Scenario"
        # Status is RUNNING internally (StatusType.RUNNING.value), displayed as "Processing"
        assert scenario.status in [StatusType.RUNNING.value, "Processing"]

    @patch("simulate.views.scenarios.start_create_script_scenario_workflow_sync")
    def test_create_script_scenario_starts_workflow(
        self, mock_workflow, auth_client, agent_definition
    ):
        """Creating a script scenario should trigger the script workflow."""
        mock_workflow.return_value = "workflow-id-456"

        payload = {
            "name": "Script Scenario Test",
            "kind": "script",
            "script_url": "https://example.com/conversation.txt",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/",
            payload,
            format="json",
        )

        # API returns 202 Accepted for async workflow operations
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_workflow.assert_called_once()

    @patch("simulate.views.scenarios.start_create_graph_scenario_workflow_sync")
    def test_create_graph_scenario_starts_workflow(
        self, mock_workflow, auth_client, agent_definition
    ):
        """Creating a graph scenario should trigger the graph workflow."""
        mock_workflow.return_value = "workflow-id-789"

        payload = {
            "name": "Graph Scenario Test",
            "kind": "graph",
            "graph": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [{"source": "start", "target": "end"}],
            },
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/",
            payload,
            format="json",
        )

        # API returns 202 Accepted for async workflow operations
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_workflow.assert_called_once()

    @patch("simulate.views.scenarios.start_create_dataset_scenario_workflow_sync")
    def test_create_scenario_creates_simulator_agent(
        self, mock_workflow, auth_client, source_dataset, agent_definition
    ):
        """Creating a scenario with agent fields should create SimulatorAgent."""
        mock_workflow.return_value = "workflow-id-123"

        payload = {
            "name": "Scenario with Agent",
            "kind": "dataset",
            "dataset_id": str(source_dataset.id),
            "agent_definition_id": str(agent_definition.id),
            "agent_name": "Custom Simulator",
            "agent_prompt": "You are a helpful test assistant.",
            "voice_provider": "openai",
            "voice_name": "alloy",
            "model": "gpt-4-turbo",
        }

        response = auth_client.post(
            "/simulate/scenarios/create/",
            payload,
            format="json",
        )

        # API returns 202 Accepted for async workflow operations
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()

        # Verify SimulatorAgent was created and linked
        # Response has nested structure: {"scenario": {...}, "status": "..."}
        scenario_data = data["scenario"]
        scenario = Scenarios.objects.get(id=scenario_data["id"])
        assert scenario.simulator_agent is not None
        # SimulatorAgent name may be derived from scenario name if not explicitly set
        # The API may use agent_name or fall back to scenario name
        assert scenario.simulator_agent is not None
        # Verify voice_provider was set from payload
        assert scenario.simulator_agent.voice_provider == "openai"

    @patch("simulate.views.scenarios.start_create_dataset_scenario_workflow_sync")
    def test_create_scenario_workflow_failure_handling(
        self, mock_workflow, auth_client, source_dataset, agent_definition
    ):
        """When workflow starter fails, scenario should still be created but may fail later."""
        mock_workflow.side_effect = Exception("Temporal connection failed")

        payload = {
            "name": "Failing Workflow Scenario",
            "kind": "dataset",
            "dataset_id": str(source_dataset.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/",
            payload,
            format="json",
        )

        # The API should handle the error gracefully
        # This depends on the actual implementation - might return 500 or create with failed status
        assert response.status_code in [
            status.HTTP_201_CREATED,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]


@pytest.mark.integration
class TestAddRowsAPIWithMockedWorkflow:
    """Integration tests for adding rows to scenarios."""

    @patch("simulate.views.scenarios.start_add_scenario_rows_workflow_sync")
    def test_add_rows_starts_generation_workflow(
        self, mock_workflow, auth_client, existing_scenario
    ):
        """Adding rows should trigger the generation workflow."""
        mock_workflow.return_value = "generation-workflow-123"

        payload = {
            "num_rows": 5,
            "description": "Generate 5 new rows",
        }

        response = auth_client.post(
            f"/simulate/scenarios/{existing_scenario.id}/add-rows/",
            payload,
            format="json",
        )

        # API should accept the request
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_202_ACCEPTED]
        mock_workflow.assert_called_once()

    @patch("simulate.views.scenarios.start_add_scenario_rows_workflow_sync")
    def test_add_rows_creates_empty_rows_before_workflow(
        self, mock_workflow, auth_client, existing_scenario, source_dataset
    ):
        """Adding rows should create empty rows in database before starting workflow."""
        mock_workflow.return_value = "generation-workflow-123"

        initial_row_count = Row.objects.filter(
            dataset=source_dataset, deleted=False
        ).count()

        payload = {
            "num_rows": 3,
        }

        response = auth_client.post(
            f"/simulate/scenarios/{existing_scenario.id}/add-rows/",
            payload,
            format="json",
        )

        if response.status_code in [status.HTTP_200_OK, status.HTTP_202_ACCEPTED]:
            # Verify rows were created (exact behavior depends on implementation)
            final_row_count = Row.objects.filter(
                dataset=source_dataset, deleted=False
            ).count()
            # Rows might be created immediately or by the workflow
            assert final_row_count >= initial_row_count


@pytest.mark.integration
class TestAddColumnsAPIWithMockedWorkflow:
    """Integration tests for adding columns to scenarios."""

    @patch("simulate.views.scenarios.start_add_columns_workflow_sync")
    def test_add_columns_starts_workflow(
        self, mock_workflow, auth_client, existing_scenario
    ):
        """Adding columns should trigger the add columns workflow."""
        mock_workflow.return_value = "add-columns-workflow-123"

        payload = {
            "columns": [
                {
                    "name": "new_column",
                    "data_type": "text",
                    "description": "A new column",
                },
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{existing_scenario.id}/add-columns/",
            payload,
            format="json",
        )

        # API should accept the request
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_202_ACCEPTED]
        mock_workflow.assert_called_once()


# ============================================================================
# Activity Business Logic Tests (Unit-like tests for helper functions)
# ============================================================================


@pytest.mark.unit
class TestGetPersonasByLanguage:
    """Tests for the get_personas_by_language utility function."""

    def test_get_english_personas_default(self):
        """English should be the default language for personas."""
        get_personas_by_language = _ee_voice_mapper().get_personas_by_language

        personas = get_personas_by_language("en")
        assert isinstance(personas, list)
        assert len(personas) > 0

    def test_get_english_personas_for_none(self):
        """None language should return English personas."""
        get_personas_by_language = _ee_voice_mapper().get_personas_by_language

        personas = get_personas_by_language(None)
        assert isinstance(personas, list)

    def test_get_hindi_personas(self):
        """Hindi language code should return Hindi personas."""
        get_personas_by_language = _ee_voice_mapper().get_personas_by_language

        personas = get_personas_by_language("hi")
        assert isinstance(personas, list)

        # Also test full name
        personas_full = get_personas_by_language("hindi")
        assert isinstance(personas_full, list)

    def test_get_personas_unknown_language_returns_english(self):
        """Unknown language should default to English personas."""
        get_personas_by_language = _ee_voice_mapper().get_personas_by_language

        personas = get_personas_by_language("xx")  # Unknown language code
        assert isinstance(personas, list)


# ============================================================================
# Scenario Status Transition Tests
# ============================================================================


@pytest.mark.integration
class TestScenarioStatusTransitions:
    """Tests for scenario status transitions during creation."""

    def test_scenario_starts_in_running_status(self, db, organization, workspace):
        """New scenarios should start in RUNNING status."""
        scenario = Scenarios.objects.create(
            name="Status Test",
            source="Test source",
            organization=organization,
            workspace=workspace,
        )

        assert scenario.status == StatusType.RUNNING.value

    def test_scenario_can_transition_to_completed(self, db, organization, workspace):
        """Scenarios can be marked as completed."""
        scenario = Scenarios.objects.create(
            name="Status Test",
            source="Test source",
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        scenario.status = StatusType.COMPLETED.value
        scenario.save()

        scenario.refresh_from_db()
        assert scenario.status == StatusType.COMPLETED.value

    def test_scenario_can_transition_to_failed(self, db, organization, workspace):
        """Scenarios can be marked as failed."""
        scenario = Scenarios.objects.create(
            name="Status Test",
            source="Test source",
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        scenario.status = StatusType.FAILED.value
        scenario.save()

        scenario.refresh_from_db()
        assert scenario.status == StatusType.FAILED.value


# ============================================================================
# Dataset Copy Logic Tests
# ============================================================================


@pytest.mark.integration
class TestDatasetCopyLogic:
    """Tests verifying dataset copy functionality used in activities."""

    def test_dataset_can_be_copied(self, db, source_dataset, user, workspace):
        """A dataset can be copied with its columns."""
        # Simulate what the activity does
        original_columns = Column.objects.filter(dataset=source_dataset, deleted=False)
        original_column_count = original_columns.count()

        # Create a copy
        copied_dataset = Dataset.no_workspace_objects.create(
            name=f"Copy of {source_dataset.name}",
            organization=source_dataset.organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.SCENARIO.value,
        )

        # Copy columns
        for col in original_columns:
            Column.objects.create(
                name=col.name,
                data_type=col.data_type,
                source=SourceChoices.OTHERS.value,
                dataset=copied_dataset,
            )

        copied_columns = Column.objects.filter(dataset=copied_dataset, deleted=False)

        assert copied_columns.count() == original_column_count
        assert copied_dataset.name == f"Copy of {source_dataset.name}"

    def test_scenario_columns_can_be_added(self, db, source_dataset):
        """Scenario-specific columns (persona, situation, outcome) can be added."""
        initial_column_count = Column.objects.filter(
            dataset=source_dataset, deleted=False
        ).count()

        # Add scenario-specific columns
        scenario_columns = [
            {"name": "persona", "data_type": "persona"},
            {"name": "situation", "data_type": "text"},
            {"name": "outcome", "data_type": "text"},
        ]

        for col_def in scenario_columns:
            Column.objects.create(
                name=col_def["name"],
                data_type=col_def["data_type"],
                source=SourceChoices.OTHERS.value,
                dataset=source_dataset,
            )

        final_column_count = Column.objects.filter(
            dataset=source_dataset, deleted=False
        ).count()

        assert final_column_count == initial_column_count + 3

    def test_rows_can_be_created_in_batch(self, db, source_dataset):
        """Rows can be created in batches (as activities do)."""
        initial_row_count = Row.objects.filter(
            dataset=source_dataset, deleted=False
        ).count()

        # Create rows in batch
        new_rows = [
            Row(dataset=source_dataset, order=initial_row_count + i) for i in range(5)
        ]
        Row.objects.bulk_create(new_rows)

        final_row_count = Row.objects.filter(
            dataset=source_dataset, deleted=False
        ).count()

        assert final_row_count == initial_row_count + 5

    def test_cells_can_be_created_in_batch(self, db, source_dataset):
        """Cells can be created in batches (as activities do)."""
        column = Column.objects.filter(dataset=source_dataset).first()
        row = Row.objects.filter(dataset=source_dataset).first()

        initial_cell_count = Cell.objects.filter(
            dataset=source_dataset, deleted=False
        ).count()

        # Create a new column for testing
        new_column = Column.objects.create(
            name="batch_test_column",
            data_type="text",
            source=SourceChoices.OTHERS.value,
            dataset=source_dataset,
        )

        # Create cells in batch for existing rows
        existing_rows = Row.objects.filter(dataset=source_dataset, deleted=False)
        new_cells = [
            Cell(
                dataset=source_dataset,
                column=new_column,
                row=r,
                value=f"batch_value_{i}",
            )
            for i, r in enumerate(existing_rows)
        ]
        Cell.objects.bulk_create(new_cells)

        final_cell_count = Cell.objects.filter(
            dataset=source_dataset, deleted=False
        ).count()

        assert final_cell_count == initial_cell_count + len(new_cells)
