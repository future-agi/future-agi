"""
API tests for Scenarios endpoints in the simulate app.

Tests cover:
- ScenariosListView: GET /simulate/scenarios/
- CreateScenarioView: POST /simulate/scenarios/create/
- ScenarioDetailView: GET /simulate/scenarios/<uuid>/
- EditScenarioView: PUT /simulate/scenarios/<uuid>/edit/
- DeleteScenarioView: DELETE /simulate/scenarios/<uuid>/delete/
- EditScenarioPromptsView: PUT /simulate/scenarios/<uuid>/prompts/
- AddScenarioRowsView: POST /simulate/scenarios/<uuid>/add-rows/
- AddScenarioColumnsView: POST /simulate/scenarios/<uuid>/add-columns/
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import AgentDefinition, Scenarios
from simulate.models.simulator_agent import SimulatorAgent

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def allow_ee_feature_checks():
    """Scenario API tests exercise validation/workflow behavior, not plan gates."""
    with patch("tfc.ee_gating.check_ee_feature", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _allow_ee_feature_checks_for_scenario_api(allow_ee_feature_checks):
    yield


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


@pytest.fixture
def dataset(db, organization, user, workspace):
    """Create a test dataset (no rows). Use ``dataset_min_rows`` when the test
    exercises the scenario-create Import Dataset path (which enforces a row
    floor)."""
    return Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )


@pytest.fixture
def dataset_min_rows(db, dataset):
    """Dataset seeded with the scenario-create minimum number of rows
    (``no_of_rows.min_value``) for tests that exercise the Import Dataset path.
    """
    Row.objects.bulk_create(
        [Row(dataset=dataset, order=i) for i in range(10)]
    )
    return dataset


@pytest.fixture
def dataset_with_rows(db, dataset):
    """Create a dataset with columns and rows."""
    # Create columns
    col1 = Column.objects.create(
        dataset=dataset,
        name="input",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    col2 = Column.objects.create(
        dataset=dataset,
        name="output",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )

    # Update dataset column order
    dataset.column_order = [str(col1.id), str(col2.id)]
    dataset.save()

    # Create rows
    row1 = Row.objects.create(dataset=dataset, order=0)
    row2 = Row.objects.create(dataset=dataset, order=1)

    # Create cells
    Cell.objects.create(dataset=dataset, column=col1, row=row1, value="input1")
    Cell.objects.create(dataset=dataset, column=col2, row=row1, value="output1")
    Cell.objects.create(dataset=dataset, column=col1, row=row2, value="input2")
    Cell.objects.create(dataset=dataset, column=col2, row=row2, value="output2")

    return dataset


@pytest.fixture
def scenario(db, organization, workspace, agent_definition, simulator_agent, dataset):
    """Create a test scenario."""
    return Scenarios.objects.create(
        name="Test Scenario",
        description="A test scenario for API testing",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def scenario_without_dataset(
    db, organization, workspace, agent_definition, simulator_agent
):
    """Create a test scenario without a dataset."""
    return Scenarios.objects.create(
        name="Test Scenario No Dataset",
        description="A test scenario without dataset",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.GRAPH,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def outbound_voice_agent_definition(db, organization, workspace):
    """Create a test agent definition for outbound voice."""
    return AgentDefinition.objects.create(
        agent_name="Outbound Voice Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567891",
        inbound=False,
        description="Outbound voice agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def text_agent_definition(db, organization, workspace):
    """Create a test agent definition for text/chat."""
    return AgentDefinition.objects.create(
        agent_name="Text Chat Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="+1234567892",
        inbound=False,
        description="Text chat agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def prompt_template(db, organization, workspace):
    """Create a test prompt template."""
    return PromptTemplate.objects.create(
        name="Test Prompt Template",
        description="A test prompt template",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_version(db, prompt_template):
    """Create a test prompt version."""
    return PromptVersion.objects.create(
        original_template=prompt_template,
        template_version="v1",
        is_default=True,
        commit_message="Initial version",
    )


@pytest.fixture
def scenario_outbound_voice(
    db,
    organization,
    workspace,
    outbound_voice_agent_definition,
    simulator_agent,
    dataset,
):
    """Create a scenario with outbound voice agent."""
    return Scenarios.objects.create(
        name="Outbound Voice Scenario",
        description="A scenario with outbound voice agent",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=outbound_voice_agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def scenario_text_chat(
    db, organization, workspace, text_agent_definition, simulator_agent, dataset
):
    """Create a scenario with text/chat agent."""
    return Scenarios.objects.create(
        name="Text Chat Scenario",
        description="A scenario with text chat agent",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=text_agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def scenario_prompt(
    db, organization, workspace, prompt_template, prompt_version, dataset
):
    """Create a prompt-based scenario (no agent_definition)."""
    return Scenarios.objects.create(
        name="Prompt Scenario",
        description="A prompt-based scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        source_type=Scenarios.SourceTypes.PROMPT,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        prompt_template=prompt_template,
        prompt_version=prompt_version,
        status=StatusType.COMPLETED.value,
    )


# ============================================================================
# ScenariosListView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestScenariosListView:
    """Tests for GET /simulate/scenarios/"""

    def test_list_scenarios_success(self, auth_client, scenario):
        """Test listing scenarios returns paginated results."""
        response = auth_client.get("/simulate/scenarios/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert "count" in data
        assert data["count"] >= 1
        assert any(s["id"] == str(scenario.id) for s in data["results"])

    def test_list_scenarios_unauthenticated(self, api_client):
        """Test listing scenarios without authentication returns 401/403."""
        response = api_client.get("/simulate/scenarios/")

        # API may return 401 or 403 depending on auth mechanism
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_list_scenarios_with_search(self, auth_client, scenario):
        """Test listing scenarios with search filter."""
        response = auth_client.get("/simulate/scenarios/", {"search": "Test Scenario"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] >= 1
        assert any(s["name"] == "Test Scenario" for s in data["results"])

    def test_list_scenarios_with_search_no_results(self, auth_client, scenario):
        """Test listing scenarios with search that returns no results."""
        response = auth_client.get(
            "/simulate/scenarios/", {"search": "NonExistentScenario12345"}
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 0
        assert len(data["results"]) == 0

    def test_list_scenarios_excludes_deleted(self, auth_client, scenario):
        """Test that deleted scenarios are not returned."""
        # Mark scenario as deleted
        scenario.deleted = True
        scenario.save()

        response = auth_client.get("/simulate/scenarios/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert not any(s["id"] == str(scenario.id) for s in data["results"])

    def test_list_scenarios_filter_by_agent_definition(
        self, auth_client, scenario, agent_definition
    ):
        """Test listing scenarios filtered by agent_definition_id."""
        response = auth_client.get(
            "/simulate/scenarios/",
            {"agent_definition_id": str(agent_definition.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] >= 1

    def test_list_scenarios_agent_type_inbound_voice(self, auth_client, scenario):
        """Test that inbound voice agent scenario returns agent_type 'inbound'."""
        response = auth_client.get("/simulate/scenarios/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = next(s for s in data["results"] if s["id"] == str(scenario.id))
        assert result["agent_type"] == "inbound"

    def test_list_scenarios_agent_type_outbound_voice(
        self, auth_client, scenario_outbound_voice
    ):
        """Test that outbound voice agent scenario returns agent_type 'outbound'."""
        response = auth_client.get("/simulate/scenarios/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = next(
            s for s in data["results"] if s["id"] == str(scenario_outbound_voice.id)
        )
        assert result["agent_type"] == "outbound"

    def test_list_scenarios_agent_type_chat(self, auth_client, scenario_text_chat):
        """Test that text agent scenario returns agent_type 'chat'."""
        response = auth_client.get("/simulate/scenarios/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = next(
            s for s in data["results"] if s["id"] == str(scenario_text_chat.id)
        )
        assert result["agent_type"] == "chat"

    def test_list_scenarios_agent_type_prompt(self, auth_client, scenario_prompt):
        """Test that prompt-based scenario returns agent_type 'prompt'."""
        response = auth_client.get("/simulate/scenarios/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = next(s for s in data["results"] if s["id"] == str(scenario_prompt.id))
        assert result["agent_type"] == "prompt"

    def test_list_scenarios_pagination(
        self,
        auth_client,
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        dataset,
    ):
        """Test that pagination works correctly."""
        # Create multiple scenarios
        for i in range(15):
            Scenarios.objects.create(
                name=f"Scenario {i}",
                description=f"Description {i}",
                source="Test source",
                scenario_type=Scenarios.ScenarioTypes.DATASET,
                organization=organization,
                workspace=workspace,
                agent_definition=agent_definition,
                simulator_agent=simulator_agent,
                status=StatusType.COMPLETED.value,
            )

        response = auth_client.get("/simulate/scenarios/", {"limit": 10})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["results"]) == 10
        assert data["count"] >= 15


# ============================================================================
# ScenarioDetailView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestScenarioDetailView:
    """Tests for GET /simulate/scenarios/<uuid>/"""

    def test_get_scenario_detail_success(self, auth_client, scenario):
        """Test getting scenario details."""
        response = auth_client.get(f"/simulate/scenarios/{scenario.id}/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(scenario.id)
        assert data["name"] == scenario.name
        assert data["description"] == scenario.description
        assert data["scenario_type"] == scenario.scenario_type
        assert "graph" in data
        assert "prompts" in data
        assert "dataset_rows" in data

    def test_get_scenario_detail_unauthenticated(self, api_client, scenario):
        """Test getting scenario without authentication returns 401/403."""
        response = api_client.get(f"/simulate/scenarios/{scenario.id}/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_get_scenario_detail_not_found(self, auth_client):
        """Test getting non-existent scenario returns 404."""
        fake_id = uuid.uuid4()
        response = auth_client.get(f"/simulate/scenarios/{fake_id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_scenario_detail_deleted_returns_404(self, auth_client, scenario):
        """Test that deleted scenario returns 404."""
        scenario.deleted = True
        scenario.save()

        response = auth_client.get(f"/simulate/scenarios/{scenario.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_scenario_detail_other_organization(self, auth_client, scenario, db):
        """Test that user cannot access scenario from other organization."""
        from accounts.models.organization import Organization

        other_org = Organization.objects.create(name="Other Org")
        scenario.organization = other_org
        scenario.save()

        response = auth_client.get(f"/simulate/scenarios/{scenario.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_scenario_detail_with_dataset_rows(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test scenario detail includes dataset row count."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        response = auth_client.get(f"/simulate/scenarios/{scenario.id}/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["dataset_rows"] == 2


# ============================================================================
# CreateScenarioView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestCreateScenarioView:
    """Tests for POST /simulate/scenarios/create/"""

    @patch("simulate.views.scenarios.start_create_dataset_scenario_workflow_sync")
    def test_create_scenario_dataset_success(
        self, mock_workflow, auth_client, agent_definition, dataset_min_rows
    ):
        """Test creating a dataset scenario successfully."""
        payload = {
            "name": "New Dataset Scenario",
            "description": "A new scenario from dataset",
            "dataset_id": str(dataset_min_rows.id),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["status"] == "processing"
        assert "scenario" in data
        assert data["scenario"]["name"] == "New Dataset Scenario"
        mock_workflow.assert_called_once()

    @patch("simulate.views.scenarios.start_create_script_scenario_workflow_sync")
    def test_create_scenario_script_success(
        self, mock_workflow, auth_client, agent_definition
    ):
        """Test creating a script scenario successfully."""
        payload = {
            "name": "New Script Scenario",
            "description": "A new scenario from script",
            "kind": "script",
            "script_url": "https://example.com/script.txt",
            "agent_definition_id": str(agent_definition.id),
            "no_of_rows": 10,
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["status"] == "processing"
        mock_workflow.assert_called_once()

    @patch("simulate.views.scenarios.start_create_graph_scenario_workflow_sync")
    def test_create_scenario_graph_success(
        self, mock_workflow, auth_client, agent_definition
    ):
        """Test creating a graph scenario with generate_graph=True."""
        payload = {
            "name": "New Graph Scenario",
            "description": "A new scenario from graph",
            "kind": "graph",
            "generate_graph": True,
            "agent_definition_id": str(agent_definition.id),
            "no_of_rows": 15,
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["status"] == "processing"
        mock_workflow.assert_called_once()

    @patch("simulate.views.scenarios.start_create_graph_scenario_workflow_sync")
    def test_create_scenario_graph_with_data(
        self, mock_workflow, auth_client, agent_definition
    ):
        """Test creating a graph scenario with provided graph data."""
        payload = {
            "name": "New Graph Scenario",
            "kind": "graph",
            "graph": {"nodes": [], "edges": []},
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_workflow.assert_called_once()

    def test_create_scenario_unauthenticated(
        self, api_client, agent_definition, dataset
    ):
        """Test creating scenario without authentication returns 401/403."""
        payload = {
            "name": "New Scenario",
            "dataset_id": str(dataset.id),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
        }

        response = api_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_create_scenario_missing_name(self, auth_client, agent_definition, dataset):
        """Test creating scenario without name returns 400."""
        payload = {
            "dataset_id": str(dataset.id),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_empty_name(self, auth_client, agent_definition, dataset):
        """Test creating scenario with empty name returns 400."""
        payload = {
            "name": "   ",
            "dataset_id": str(dataset.id),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_invalid_kind(self, auth_client, agent_definition):
        """Test creating scenario with invalid kind returns 400."""
        payload = {
            "name": "New Scenario",
            "kind": "invalid_kind",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_dataset_missing_dataset_id(
        self, auth_client, agent_definition
    ):
        """Test creating dataset scenario without dataset_id returns 400."""
        payload = {
            "name": "New Scenario",
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_script_missing_script_url(
        self, auth_client, agent_definition
    ):
        """Test creating script scenario without script_url returns 400."""
        payload = {
            "name": "New Scenario",
            "kind": "script",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_graph_missing_requirements(
        self, auth_client, agent_definition
    ):
        """Test creating graph scenario without generate_graph or graph data returns 400."""
        payload = {
            "name": "New Scenario",
            "kind": "graph",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_nonexistent_dataset(self, auth_client, agent_definition):
        """Test creating scenario with non-existent dataset returns 400."""
        payload = {
            "name": "New Scenario",
            "dataset_id": str(uuid.uuid4()),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("simulate.views.scenarios.start_create_dataset_scenario_workflow_sync")
    def test_create_scenario_with_custom_columns(
        self, mock_workflow, auth_client, agent_definition, dataset_min_rows
    ):
        """Test creating scenario with custom columns."""
        payload = {
            "name": "New Scenario with Custom Cols",
            "dataset_id": str(dataset_min_rows.id),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
            "custom_columns": [
                {
                    "name": "custom_col1",
                    "data_type": "text",
                    "description": "A custom column",
                }
            ],
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED

    def test_create_scenario_custom_columns_invalid_data_type(
        self, auth_client, agent_definition, dataset
    ):
        """Test creating scenario with invalid custom column data type returns 400."""
        payload = {
            "name": "New Scenario",
            "dataset_id": str(dataset.id),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
            "custom_columns": [
                {
                    "name": "custom_col1",
                    "data_type": "invalid_type",
                    "description": "A custom column",
                }
            ],
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_custom_columns_missing_description(
        self, auth_client, agent_definition, dataset
    ):
        """Test creating scenario with custom column missing description returns 400."""
        payload = {
            "name": "New Scenario",
            "dataset_id": str(dataset.id),
            "kind": "dataset",
            "agent_definition_id": str(agent_definition.id),
            "custom_columns": [
                {
                    "name": "custom_col1",
                    "data_type": "text",
                }
            ],
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_scenario_duplicate_custom_column_names(
        self, auth_client, agent_definition, dataset
    ):
        """Test creating script/graph scenario with duplicate custom column names returns 400."""
        payload = {
            "name": "New Scenario",
            "kind": "script",
            "script_url": "https://example.com/script.txt",
            "agent_definition_id": str(agent_definition.id),
            "custom_columns": [
                {"name": "col1", "data_type": "text", "description": "First column"},
                {
                    "name": "col1",
                    "data_type": "text",
                    "description": "Duplicate column",
                },
            ],
        }

        response = auth_client.post(
            "/simulate/scenarios/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# EditScenarioView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestEditScenarioView:
    """Tests for PUT /simulate/scenarios/<uuid>/edit/"""

    def test_edit_scenario_name_success(self, auth_client, scenario):
        """Test editing scenario name."""
        payload = {"name": "Updated Scenario Name"}

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["scenario"]["name"] == "Updated Scenario Name"

        # Verify in database
        scenario.refresh_from_db()
        assert scenario.name == "Updated Scenario Name"

    def test_edit_scenario_description_success(self, auth_client, scenario):
        """Test editing scenario description."""
        payload = {"description": "Updated description"}

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        scenario.refresh_from_db()
        assert scenario.description == "Updated description"

    def test_edit_scenario_name_and_description(self, auth_client, scenario):
        """Test editing both name and description."""
        payload = {
            "name": "New Name",
            "description": "New Description",
        }

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        scenario.refresh_from_db()
        assert scenario.name == "New Name"
        assert scenario.description == "New Description"

    def test_edit_scenario_unauthenticated(self, api_client, scenario):
        """Test editing scenario without authentication returns 401/403."""
        payload = {"name": "Updated Name"}

        response = api_client.put(
            f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_edit_scenario_not_found(self, auth_client):
        """Test editing non-existent scenario returns 404."""
        fake_id = uuid.uuid4()
        payload = {"name": "Updated Name"}

        response = auth_client.put(
            f"/simulate/scenarios/{fake_id}/edit/", payload, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_edit_scenario_empty_name(self, auth_client, scenario):
        """Test editing scenario with empty name returns 400."""
        payload = {"name": "   "}

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_edit_scenario_deleted_returns_404(self, auth_client, scenario):
        """Test editing deleted scenario returns 404."""
        scenario.deleted = True
        scenario.save()

        payload = {"name": "Updated Name"}

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_edit_scenario_other_organization(self, auth_client, scenario, db):
        """Test that user cannot edit scenario from other organization."""
        from accounts.models.organization import Organization

        other_org = Organization.objects.create(name="Other Org")
        scenario.organization = other_org
        scenario.save()

        payload = {"name": "Updated Name"}

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# DeleteScenarioView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestDeleteScenarioView:
    """Tests for DELETE /simulate/scenarios/<uuid>/delete/"""

    def test_delete_scenario_success(self, auth_client, scenario):
        """Test soft deleting a scenario."""
        response = auth_client.delete(f"/simulate/scenarios/{scenario.id}/delete/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Scenario deleted successfully"

        # Verify soft delete in database
        scenario.refresh_from_db()
        assert scenario.deleted is True

    def test_delete_scenario_unauthenticated(self, api_client, scenario):
        """Test deleting scenario without authentication returns 401/403."""
        response = api_client.delete(f"/simulate/scenarios/{scenario.id}/delete/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_delete_scenario_not_found(self, auth_client):
        """Test deleting non-existent scenario returns 404."""
        fake_id = uuid.uuid4()

        response = auth_client.delete(f"/simulate/scenarios/{fake_id}/delete/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_scenario_already_deleted(self, auth_client, scenario):
        """Test deleting already deleted scenario returns 404."""
        scenario.deleted = True
        scenario.save()

        response = auth_client.delete(f"/simulate/scenarios/{scenario.id}/delete/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_scenario_other_organization(self, auth_client, scenario, db):
        """Test that user cannot delete scenario from other organization."""
        from accounts.models.organization import Organization

        other_org = Organization.objects.create(name="Other Org")
        scenario.organization = other_org
        scenario.save()

        response = auth_client.delete(f"/simulate/scenarios/{scenario.id}/delete/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# EditScenarioPromptsView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestEditScenarioPromptsView:
    """Tests for PUT /simulate/scenarios/<uuid>/prompts/"""

    def test_edit_prompts_success(self, auth_client, scenario):
        """Test editing scenario prompts."""
        payload = {"prompts": "You are an updated test agent prompt."}

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/prompts/", payload, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["prompts"] == "You are an updated test agent prompt."

    def test_edit_prompts_unauthenticated(self, api_client, scenario):
        """Test editing prompts without authentication returns 401/403."""
        payload = {"prompts": "Updated prompt"}

        response = api_client.put(
            f"/simulate/scenarios/{scenario.id}/prompts/", payload, format="json"
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_edit_prompts_not_found(self, auth_client):
        """Test editing prompts for non-existent scenario returns 404."""
        fake_id = uuid.uuid4()
        payload = {"prompts": "Updated prompt"}

        response = auth_client.put(
            f"/simulate/scenarios/{fake_id}/prompts/", payload, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_edit_prompts_missing_field(self, auth_client, scenario):
        """Test editing prompts without prompts field returns 400."""
        payload = {}

        response = auth_client.put(
            f"/simulate/scenarios/{scenario.id}/prompts/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# AddScenarioRowsView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestAddScenarioRowsView:
    """Tests for POST /simulate/scenarios/<uuid>/add-rows/"""

    @patch("simulate.views.scenarios.start_add_scenario_rows_workflow_sync")
    def test_add_rows_success(
        self, mock_workflow, auth_client, scenario, dataset_with_rows
    ):
        """Test adding rows to a scenario."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {"num_rows": 10, "description": "Additional test rows"}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-rows/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["num_rows"] == 10
        mock_workflow.assert_called_once()

    def test_add_rows_unauthenticated(self, api_client, scenario):
        """Test adding rows without authentication returns 401/403."""
        payload = {"num_rows": 10}

        response = api_client.post(
            f"/simulate/scenarios/{scenario.id}/add-rows/", payload, format="json"
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_add_rows_scenario_not_found(self, auth_client):
        """Test adding rows to non-existent scenario returns 404."""
        fake_id = uuid.uuid4()
        payload = {"num_rows": 10}

        response = auth_client.post(
            f"/simulate/scenarios/{fake_id}/add-rows/", payload, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_rows_no_dataset(self, auth_client, scenario_without_dataset):
        """Test adding rows to scenario without dataset returns 400."""
        payload = {"num_rows": 5}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario_without_dataset.id}/add-rows/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not have an associated dataset" in response.json()["result"]

    def test_add_rows_invalid_num_rows_zero(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test adding 0 rows returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {"num_rows": 0}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-rows/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_rows_invalid_num_rows_exceeds_max(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test adding more than 20000 rows returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {"num_rows": 20001}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-rows/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_rows_missing_num_rows(self, auth_client, scenario, dataset_with_rows):
        """Test adding rows without num_rows field returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-rows/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# AddScenarioColumnsView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestAddScenarioColumnsView:
    """Tests for POST /simulate/scenarios/<uuid>/add-columns/"""

    @patch("simulate.views.scenarios.start_add_columns_workflow_sync")
    def test_add_columns_success(
        self, mock_workflow, auth_client, scenario, dataset_with_rows
    ):
        """Test adding columns to a scenario."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {
            "columns": [
                {
                    "name": "new_column",
                    "data_type": "text",
                    "description": "A new column",
                }
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert "new_column" in data["columns"]
        mock_workflow.assert_called_once()

    @patch("simulate.views.scenarios.start_add_columns_workflow_sync")
    def test_add_multiple_columns_success(
        self, mock_workflow, auth_client, scenario, dataset_with_rows
    ):
        """Test adding multiple columns at once."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {
            "columns": [
                {"name": "col1", "data_type": "text", "description": "Column 1"},
                {"name": "col2", "data_type": "text", "description": "Column 2"},
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert "col1" in data["columns"]
        assert "col2" in data["columns"]

    def test_add_columns_unauthenticated(self, api_client, scenario):
        """Test adding columns without authentication returns 401/403."""
        payload = {
            "columns": [
                {"name": "col1", "data_type": "text", "description": "Column 1"}
            ]
        }

        response = api_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_add_columns_scenario_not_found(self, auth_client):
        """Test adding columns to non-existent scenario returns 404."""
        fake_id = uuid.uuid4()
        payload = {
            "columns": [
                {"name": "col1", "data_type": "text", "description": "Column 1"}
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{fake_id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_columns_no_dataset(self, auth_client, scenario_without_dataset):
        """Test adding columns to scenario without dataset returns 400."""
        payload = {
            "columns": [
                {"name": "col1", "data_type": "text", "description": "Column 1"}
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{scenario_without_dataset.id}/add-columns/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_columns_empty_list(self, auth_client, scenario, dataset_with_rows):
        """Test adding empty columns list returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {"columns": []}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_columns_missing_name(self, auth_client, scenario, dataset_with_rows):
        """Test adding column without name returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {"columns": [{"data_type": "text", "description": "Missing name"}]}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_columns_missing_data_type(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test adding column without data_type returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {"columns": [{"name": "col1", "description": "Missing data type"}]}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_columns_missing_description(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test adding column without description returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {"columns": [{"name": "col1", "data_type": "text"}]}

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_columns_invalid_data_type(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test adding column with invalid data_type returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {
            "columns": [
                {
                    "name": "col1",
                    "data_type": "invalid_type",
                    "description": "Invalid type",
                }
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_columns_duplicate_names_in_request(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test adding columns with duplicate names in same request returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {
            "columns": [
                {"name": "same_name", "data_type": "text", "description": "First"},
                {"name": "same_name", "data_type": "text", "description": "Second"},
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_columns_existing_column_name(
        self, auth_client, scenario, dataset_with_rows
    ):
        """Test adding column with name that already exists returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        # "input" column already exists in dataset_with_rows
        payload = {
            "columns": [
                {"name": "input", "data_type": "text", "description": "Duplicate"}
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Check for error message in response (may be under 'error' or 'detail')
        data = response.json()
        error_msg = data.get("error") or data.get("detail") or str(data)
        assert "already exists" in error_msg.lower() or "input" in str(data).lower()

    def test_add_columns_exceeds_max(self, auth_client, scenario, dataset_with_rows):
        """Test adding more than 10 columns at once returns 400."""
        scenario.dataset = dataset_with_rows
        scenario.save()

        payload = {
            "columns": [
                {"name": f"col{i}", "data_type": "text", "description": f"Col {i}"}
                for i in range(11)
            ]
        }

        response = auth_client.post(
            f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# GetMultiDatasetsColumnConfigs Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestGetMultiDatasetsColumnConfigs:
    """Tests for GET /simulate/scenarios/get-columns/"""

    def test_get_columns_success(self, auth_client, scenario):
        """Test getting column configs for multiple scenarios."""
        import json

        response = auth_client.get(
            "/simulate/scenarios/get-columns/",
            {"scenarios": json.dumps([str(scenario.id)])},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "column_configs" in data

    def test_get_columns_unauthenticated(self, api_client, scenario):
        """Test getting columns without authentication returns 401/403."""
        import json

        response = api_client.get(
            "/simulate/scenarios/get-columns/",
            {"scenarios": json.dumps([str(scenario.id)])},
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_get_columns_empty_list(self, auth_client):
        """Test getting columns with empty list."""
        import json

        response = auth_client.get(
            "/simulate/scenarios/get-columns/",
            {"scenarios": json.dumps([])},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["column_configs"] == []
