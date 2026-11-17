"""Scenario endpoints answer an unexpected error with their own fixed sentence.

Each row forces an unexpected error inside one scenario endpoint. The status
stays as it was, the body carries only the endpoint's sentence, and the
exception text goes to the server log instead of the response.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.simulator_agent import SimulatorAgent
from simulate.views import scenarios as views

pytestmark = [pytest.mark.integration, pytest.mark.api]

LEAK = "internal detail /srv/x host=db-internal"
NEEDLES = ("internal detail", "/srv/x", "db-internal")


def _failing(*args, **kwargs):
    raise Exception(LEAK)


def _assert_fixed_body(
    response, caplog, sentence, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
):
    assert response.status_code == status_code
    body = response.content.decode()
    for needle in NEEDLES:
        assert needle not in body
    assert response.json()["message"] == sentence
    # The exception still reaches the server log, traceback included.
    assert any(LEAK in str(r.exc_info[1]) for r in caplog.records if r.exc_info)


def _graph_payload(agent_definition):
    return {
        "name": "Error Body Graph Scenario",
        "kind": "graph",
        "generate_graph": True,
        "agent_definition_id": str(agent_definition.id),
        "no_of_rows": 15,
    }


@pytest.fixture(autouse=True)
def _allow_ee_feature_checks():
    # These rows exercise error bodies, not plan gates.
    with patch("tfc.ee_gating.check_ee_feature", return_value=None):
        yield


@pytest.fixture
def view_log(caplog):
    caplog.set_level(logging.ERROR, logger=views.__name__)
    return caplog


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Error Body Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Agent for error body tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def scenario(db, organization, workspace, user, agent_definition):
    simulator_agent = SimulatorAgent.objects.create(
        name="Error Body Simulator",
        prompt="You are a test simulator agent.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )
    dataset = Dataset.no_workspace_objects.create(
        name="Error Body Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    column = Column.objects.create(
        dataset=dataset,
        name="input",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save()
    Row.objects.create(dataset=dataset, order=0)
    return Scenarios.objects.create(
        name="Error Body Scenario",
        description="A scenario for error body tests",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


def test_list_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(views, "ScenarioResponseSerializer", _failing)

    response = auth_client.get("/simulate/scenarios/")

    _assert_fixed_body(response, view_log, "Failed to retrieve scenarios.")


def test_get_columns_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(views, "ScenarioMultiDatasetResponseSerializer", _failing)

    response = auth_client.get(
        "/simulate/scenarios/get-columns/",
        {"scenarios": json.dumps([str(scenario.id)])},
    )

    # This endpoint answers an unexpected error with 400; only the body changes.
    _assert_fixed_body(
        response,
        view_log,
        "Error fetching column configs.",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def test_create_workflow_error_body_is_fixed(
    auth_client, agent_definition, monkeypatch, view_log
):
    monkeypatch.setattr(views, "start_create_graph_scenario_workflow_sync", _failing)
    payload = _graph_payload(agent_definition)

    response = auth_client.post("/simulate/scenarios/create/", payload, format="json")

    _assert_fixed_body(response, view_log, "Failed to create scenario.")


def test_create_record_error_body_is_fixed(
    auth_client, agent_definition, monkeypatch, view_log
):
    # The temporary record's helpers wrap the error twice before the handler.
    workflow = MagicMock()
    monkeypatch.setattr(views, "start_create_graph_scenario_workflow_sync", workflow)
    monkeypatch.setattr(views, "generate_simulator_agent_prompt", _failing)
    payload = _graph_payload(agent_definition)

    response = auth_client.post("/simulate/scenarios/create/", payload, format="json")

    _assert_fixed_body(response, view_log, "Failed to create scenario.")
    workflow.assert_not_called()


def test_detail_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(views, "get_dataset_column_config", _failing)

    response = auth_client.get(f"/simulate/scenarios/{scenario.id}/")

    _assert_fixed_body(response, view_log, "Failed to retrieve scenario.")


def test_delete_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(Scenarios, "delete", _failing)

    response = auth_client.delete(f"/simulate/scenarios/{scenario.id}/delete/")

    _assert_fixed_body(response, view_log, "Failed to delete scenario.")


def test_edit_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(Scenarios, "save", _failing)
    payload = {"name": "Renamed Scenario"}

    response = auth_client.put(
        f"/simulate/scenarios/{scenario.id}/edit/", payload, format="json"
    )

    _assert_fixed_body(response, view_log, "Failed to update scenario.")


def test_prompts_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(SimulatorAgent, "save", _failing)
    payload = {"prompts": "You are an updated test agent prompt."}

    response = auth_client.put(
        f"/simulate/scenarios/{scenario.id}/prompts/", payload, format="json"
    )

    _assert_fixed_body(response, view_log, "Failed to update scenario prompts.")


def test_add_rows_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(views, "start_add_scenario_rows_workflow_sync", _failing)
    payload = {"num_rows": 10, "description": "Additional test rows"}

    response = auth_client.post(
        f"/simulate/scenarios/{scenario.id}/add-rows/", payload, format="json"
    )

    _assert_fixed_body(response, view_log, "Failed to add rows.")


def test_add_columns_error_body_is_fixed(auth_client, scenario, monkeypatch, view_log):
    monkeypatch.setattr(views, "start_add_columns_workflow_sync", _failing)
    payload = {
        "columns": [
            {
                "name": "new_column",
                "data_type": "text",
                "description": "A new column",
            },
        ],
    }

    response = auth_client.post(
        f"/simulate/scenarios/{scenario.id}/add-columns/", payload, format="json"
    )

    _assert_fixed_body(response, view_log, "Failed to add columns.")
