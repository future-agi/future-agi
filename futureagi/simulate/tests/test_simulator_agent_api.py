import pytest
from rest_framework import status

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
