import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from simulate.models.simulator_agent import SimulatorAgent


def _payload(**overrides):
    payload = {
        "name": "QA Simulator",
        "prompt": "You are a careful evaluator.",
        "voice_provider": "elevenlabs",
        "voice_name": "marissa",
        "model": "gpt-4",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Existing Simulator",
        prompt="Existing prompt.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.mark.integration
@pytest.mark.api
class TestCreateSimulatorAgentView:
    def test_create_success(self, auth_client, organization):
        response = auth_client.post(
            "/simulate/simulator-agents/create/",
            _payload(),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == "QA Simulator"
        assert SimulatorAgent.objects.filter(
            id=data["id"], organization=organization
        ).exists()

    def test_create_persists_request_workspace(self, auth_client, workspace):
        response = auth_client.post(
            "/simulate/simulator-agents/create/",
            _payload(name="Workspace Simulator"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        simulator_agent = SimulatorAgent.no_workspace_objects.get(
            id=response.json()["id"]
        )
        assert simulator_agent.workspace_id == workspace.id

    def test_create_rejects_unknown_body_field(self, auth_client):
        response = auth_client.post(
            "/simulate/simulator-agents/create/",
            _payload(legacy_extra="ignore me"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["legacy_extra"] == ["Unknown field."]


@pytest.mark.integration
@pytest.mark.api
class TestEditSimulatorAgentView:
    def test_edit_success(self, auth_client, simulator_agent):
        response = auth_client.put(
            f"/simulate/simulator-agents/{simulator_agent.id}/edit/",
            {"name": "Updated Simulator"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        simulator_agent.refresh_from_db()
        assert simulator_agent.name == "Updated Simulator"

    def test_edit_rejects_unknown_body_field(self, auth_client, simulator_agent):
        response = auth_client.put(
            f"/simulate/simulator-agents/{simulator_agent.id}/edit/",
            {"name": "Updated Simulator", "legacy_extra": "ignore me"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["legacy_extra"] == ["Unknown field."]


@pytest.mark.integration
@pytest.mark.api
class TestSimulatorAgentWorkspaceScope:
    def test_read_update_delete_scope_to_request_workspace(
        self, auth_client, organization, workspace, user, simulator_agent
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Simulator Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = SimulatorAgent.no_workspace_objects.create(
            name="Hidden Simulator",
            prompt="Hidden prompt.",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=other_workspace,
        )

        list_response = auth_client.get(
            "/simulate/simulator-agents/",
            {"search": "Simulator", "limit": 50},
        )
        assert list_response.status_code == status.HTTP_200_OK
        listed_ids = {row["id"] for row in list_response.json()["results"]}
        assert str(simulator_agent.id) in listed_ids
        assert str(hidden_agent.id) not in listed_ids

        detail_response = auth_client.get(
            f"/simulate/simulator-agents/{hidden_agent.id}/"
        )
        edit_response = auth_client.put(
            f"/simulate/simulator-agents/{hidden_agent.id}/edit/",
            {"name": "Leaked Hidden Simulator"},
            format="json",
        )
        delete_response = auth_client.delete(
            f"/simulate/simulator-agents/{hidden_agent.id}/delete/"
        )

        assert detail_response.status_code == status.HTTP_404_NOT_FOUND
        assert edit_response.status_code == status.HTTP_404_NOT_FOUND
        assert delete_response.status_code == status.HTTP_404_NOT_FOUND

        hidden_agent.refresh_from_db()
        assert hidden_agent.name == "Hidden Simulator"
        assert hidden_agent.deleted is False
        assert hidden_agent.deleted_at is None

    def test_delete_stamps_deleted_at(self, auth_client, simulator_agent):
        response = auth_client.delete(
            f"/simulate/simulator-agents/{simulator_agent.id}/delete/"
        )

        assert response.status_code == status.HTTP_200_OK
        simulator_agent.refresh_from_db()
        assert simulator_agent.deleted is True
        assert simulator_agent.deleted_at is not None
