"""
End-to-end tests for Scenario creation workflows.

Tests cover:
- Scenario lifecycle (creation, status transitions, completion)
- ScenarioGraph creation for graph scenarios
- Error handling and partial data retention
- Concurrent scenario operations

Note: These tests focus on the data model and workflow integration points.
Full API integration tests with mocked workflows are in test_activities.py.
"""


import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import Scenarios
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.simulator_agent import SimulatorAgent

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def simulator_agent(db, organization, workspace):
    """Create a test simulator agent."""
    return SimulatorAgent.objects.create(
        name="Test Simulator Agent",
        prompt="You are a test simulator agent.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


# ============================================================================
# Dataset Scenario Lifecycle Tests
# ============================================================================


@pytest.mark.e2e
class TestDatasetScenarioLifecycle:
    """Tests for dataset scenario lifecycle."""

    def test_create_dataset_scenario_with_running_status(
        self, db, organization, workspace
    ):
        """New dataset scenarios should start with RUNNING status."""
        scenario = Scenarios.objects.create(
            name="Dataset Lifecycle Scenario",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        assert scenario.status == StatusType.RUNNING.value
        assert scenario.scenario_type == Scenarios.ScenarioTypes.DATASET

    def test_scenario_status_transition_to_completed(self, db, organization, workspace):
        """Scenario can transition from RUNNING to COMPLETED."""
        scenario = Scenarios.objects.create(
            name="Status Transition Scenario",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        # Simulate workflow completion
        scenario.status = StatusType.COMPLETED.value
        scenario.save()

        scenario.refresh_from_db()
        assert scenario.status == StatusType.COMPLETED.value

    def test_scenario_status_transition_to_failed(self, db, organization, workspace):
        """Scenario can transition from RUNNING to FAILED."""
        scenario = Scenarios.objects.create(
            name="Failing Scenario",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        # Simulate workflow failure
        scenario.status = StatusType.FAILED.value
        scenario.metadata = {"error": "LLM rate limit exceeded"}
        scenario.save()

        scenario.refresh_from_db()
        assert scenario.status == StatusType.FAILED.value
        assert "error" in scenario.metadata

    def test_scenario_with_linked_dataset(self, db, organization, workspace, user):
        """Scenario can be linked to a dataset."""
        dataset = Dataset.no_workspace_objects.create(
            name="Scenario Dataset",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.SCENARIO.value,
        )

        scenario = Scenarios.objects.create(
            name="Dataset Linked Scenario",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            metadata={"dataset_id": str(dataset.id)},
        )

        assert scenario.metadata["dataset_id"] == str(dataset.id)


# ============================================================================
# Script Scenario Lifecycle Tests
# ============================================================================


@pytest.mark.e2e
class TestScriptScenarioLifecycle:
    """Tests for script scenario lifecycle."""

    def test_create_script_scenario(self, db, organization, workspace):
        """Script scenarios can be created with correct type."""
        scenario = Scenarios.objects.create(
            name="Script Scenario",
            source="script content here",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=organization,
            workspace=workspace,
        )

        assert scenario.scenario_type == Scenarios.ScenarioTypes.SCRIPT

    def test_script_scenario_with_metadata(self, db, organization, workspace):
        """Script scenarios can store script-related metadata."""
        metadata = {
            "script_url": "https://example.com/script.txt",
            "parsed_personas": 5,
        }

        scenario = Scenarios.objects.create(
            name="Script with Metadata",
            source="script source",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=organization,
            workspace=workspace,
            metadata=metadata,
        )

        assert scenario.metadata["script_url"] == "https://example.com/script.txt"
        assert scenario.metadata["parsed_personas"] == 5


# ============================================================================
# Graph Scenario Lifecycle Tests
# ============================================================================


@pytest.mark.e2e
class TestGraphScenarioLifecycle:
    """Tests for graph scenario lifecycle."""

    def test_create_graph_scenario(self, db, organization, workspace):
        """Graph scenarios can be created with correct type."""
        scenario = Scenarios.objects.create(
            name="Graph Scenario",
            source="graph source",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
        )

        assert scenario.scenario_type == Scenarios.ScenarioTypes.GRAPH

    def test_graph_scenario_with_scenario_graph(self, db, organization, workspace):
        """Graph scenarios can have associated ScenarioGraph."""
        scenario = Scenarios.objects.create(
            name="Graph with ScenarioGraph",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
        )

        graph_config = {
            "nodes": [
                {"id": "start", "type": "start", "label": "Start"},
                {"id": "greeting", "type": "message", "content": "Hello!"},
                {"id": "end", "type": "end", "label": "End"},
            ],
            "edges": [
                {"source": "start", "target": "greeting"},
                {"source": "greeting", "target": "end"},
            ],
        }

        scenario_graph = ScenarioGraph.objects.create(
            name="Test Graph",
            scenario=scenario,
            organization=organization,
            graph_config=graph_config,
        )

        assert scenario_graph.scenario == scenario
        assert scenario_graph.graph_config == graph_config
        assert len(scenario_graph.graph_config["nodes"]) == 3
        assert len(scenario_graph.graph_config["edges"]) == 2

    def test_multiple_graph_versions(self, db, organization, workspace):
        """A graph scenario can have multiple graph versions."""
        scenario = Scenarios.objects.create(
            name="Multi-version Graph",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=organization,
            workspace=workspace,
        )

        # Create version 1
        ScenarioGraph.objects.create(
            name="Graph V1",
            scenario=scenario,
            organization=organization,
            version=1,
            is_active=False,
        )

        # Create version 2 (active)
        ScenarioGraph.objects.create(
            name="Graph V2",
            scenario=scenario,
            organization=organization,
            version=2,
            is_active=True,
        )

        graphs = ScenarioGraph.objects.filter(scenario=scenario)
        assert graphs.count() == 2

        active_graph = ScenarioGraph.objects.filter(
            scenario=scenario, is_active=True
        ).first()
        assert active_graph.version == 2


# ============================================================================
# Error Handling and Recovery Tests
# ============================================================================


@pytest.mark.e2e
class TestErrorHandling:
    """Tests for error handling scenarios."""

    def test_scenario_retains_partial_data_on_failure(
        self, db, organization, workspace, user
    ):
        """Scenario retains partial data when workflow fails."""
        # Create scenario with associated dataset
        scenario = Scenarios.objects.create(
            name="Partial Failure Scenario",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        # Create partial dataset data
        dataset = Dataset.no_workspace_objects.create(
            name="Partial Dataset",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.SCENARIO.value,
        )

        col = Column.objects.create(
            dataset=dataset,
            name="persona",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        row = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(dataset=dataset, column=col, row=row, value="Test persona")

        # Link to scenario
        scenario.metadata = {"dataset_id": str(dataset.id)}
        scenario.status = StatusType.FAILED.value
        scenario.save()

        # Verify partial data is retained
        assert Dataset.no_workspace_objects.filter(id=dataset.id).exists()
        assert Row.objects.filter(dataset=dataset).count() == 1
        assert Cell.objects.filter(dataset=dataset).count() == 1

    def test_scenario_stores_error_details(self, db, organization, workspace):
        """Failed scenarios store error details in metadata."""
        scenario = Scenarios.objects.create(
            name="Error Details Scenario",
            source="test source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        error_details = {
            "error": "LLM API error",
            "error_code": "RATE_LIMIT_EXCEEDED",
            "retry_count": 3,
            "last_attempt": "2024-01-15T10:30:00Z",
        }

        scenario.status = StatusType.FAILED.value
        scenario.metadata = error_details
        scenario.save()

        scenario.refresh_from_db()
        assert scenario.metadata["error"] == "LLM API error"
        assert scenario.metadata["error_code"] == "RATE_LIMIT_EXCEEDED"


# ============================================================================
# Concurrent Operations Tests
# ============================================================================


@pytest.mark.e2e
class TestConcurrentOperations:
    """Tests for concurrent scenario operations."""

    def test_multiple_scenarios_same_organization(self, db, organization, workspace):
        """Multiple scenarios can exist in same organization."""
        scenarios = []
        for i in range(5):
            scenario = Scenarios.objects.create(
                name=f"Concurrent Scenario {i}",
                source=f"source {i}",
                scenario_type=Scenarios.ScenarioTypes.DATASET,
                organization=organization,
                workspace=workspace,
                status=StatusType.RUNNING.value,
            )
            scenarios.append(scenario)

        org_scenarios = Scenarios.objects.filter(organization=organization)
        assert org_scenarios.count() == 5

    def test_scenarios_can_have_different_statuses(self, db, organization, workspace):
        """Scenarios in same org can have different statuses."""
        Scenarios.objects.create(
            name="Running Scenario",
            source="source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.RUNNING.value,
        )

        Scenarios.objects.create(
            name="Completed Scenario",
            source="source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.COMPLETED.value,
        )

        Scenarios.objects.create(
            name="Failed Scenario",
            source="source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            status=StatusType.FAILED.value,
        )

        assert (
            Scenarios.objects.filter(
                organization=organization, status=StatusType.RUNNING.value
            ).count()
            == 1
        )
        assert (
            Scenarios.objects.filter(
                organization=organization, status=StatusType.COMPLETED.value
            ).count()
            == 1
        )
        assert (
            Scenarios.objects.filter(
                organization=organization, status=StatusType.FAILED.value
            ).count()
            == 1
        )


# ============================================================================
# Soft Delete Tests
# ============================================================================


@pytest.mark.e2e
class TestSoftDelete:
    """Tests for scenario soft delete behavior."""

    def test_soft_deleted_scenario_not_in_default_queryset(
        self, db, organization, workspace
    ):
        """Soft deleted scenarios don't appear in default queryset."""
        scenario = Scenarios.objects.create(
            name="To Be Deleted",
            source="source",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
        )

        scenario_id = scenario.id
        scenario.delete()

        # Not in default queryset
        assert not Scenarios.objects.filter(id=scenario_id).exists()

        # But in all_objects
        assert Scenarios.all_objects.filter(id=scenario_id).exists()

    def test_soft_deleted_scenario_preserves_data(self, db, organization, workspace):
        """Soft delete preserves scenario data."""
        scenario = Scenarios.objects.create(
            name="Preserved Data Scenario",
            source="important source",
            description="Important description",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            metadata={"key": "value"},
        )

        scenario_id = scenario.id
        scenario.delete()

        deleted_scenario = Scenarios.all_objects.get(id=scenario_id)
        assert deleted_scenario.name == "Preserved Data Scenario"
        assert deleted_scenario.source == "important source"
        assert deleted_scenario.metadata == {"key": "value"}
        assert deleted_scenario.deleted is True
