import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from simulate.models.simulator_agent import SimulatorAgent

EXPECTED_SIMULATOR_AGENT_KEYS = {
    "id",
    "name",
    "prompt",
    "voice_provider",
    "voice_name",
    "interrupt_sensitivity",
    "conversation_speed",
    "finished_speaking_sensitivity",
    "model",
    "llm_temperature",
    "max_call_duration_in_minutes",
    "initial_message_delay",
    "initial_message",
    "created_at",
    "updated_at",
    "organization",
    "deleted",
    "deleted_at",
    "logo_url",
}


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


@pytest.mark.integration
@pytest.mark.api
class TestSimulatorAgentListHappyPath:
    """Functional tests for GET /simulate/simulator-agents/ (list)."""

    def test_list_returns_seeded_agents_with_id_and_name(
        self, auth_client, organization, workspace
    ):
        seeded = [
            SimulatorAgent.objects.create(
                name=f"List Agent {index}",
                prompt="Prompt.",
                voice_provider="elevenlabs",
                voice_name="marissa",
                model="gpt-4",
                organization=organization,
                workspace=workspace,
            )
            for index in range(3)
        ]

        response = auth_client.get("/simulate/simulator-agents/?limit=50")

        assert response.status_code == status.HTTP_200_OK, response.content
        results = response.json()["results"]
        listed_by_id = {row["id"]: row["name"] for row in results}
        for agent in seeded:
            assert str(agent.id) in listed_by_id
            assert listed_by_id[str(agent.id)] == agent.name

    def test_list_search_filter_returns_only_matching_agents(
        self, auth_client, organization, workspace
    ):
        matching = SimulatorAgent.objects.create(
            name="Foo Matcher Agent",
            prompt="Prompt.",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )
        non_matching = SimulatorAgent.objects.create(
            name="Bar Unrelated Agent",
            prompt="Prompt.",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.get("/simulate/simulator-agents/?search=Foo&limit=50")

        assert response.status_code == status.HTTP_200_OK, response.content
        listed_ids = {row["id"] for row in response.json()["results"]}
        assert str(matching.id) in listed_ids
        assert str(non_matching.id) not in listed_ids

    def test_list_pagination_respects_limit_and_reports_full_count(
        self, auth_client, organization, workspace
    ):
        for index in range(15):
            SimulatorAgent.objects.create(
                name=f"Pagination Agent {index:02d}",
                prompt="Prompt.",
                voice_provider="elevenlabs",
                voice_name="marissa",
                model="gpt-4",
                organization=organization,
                workspace=workspace,
            )

        response = auth_client.get("/simulate/simulator-agents/?limit=5")

        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        assert len(body["results"]) == 5
        assert body["count"] >= 15

    def test_list_response_body_key_shape(
        self, auth_client, organization, workspace
    ):
        agent = SimulatorAgent.objects.create(
            name="Shape Agent",
            prompt="Prompt.",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.get("/simulate/simulator-agents/?search=Shape&limit=50")

        assert response.status_code == status.HTTP_200_OK, response.content
        row = next(
            r for r in response.json()["results"] if r["id"] == str(agent.id)
        )
        assert set(row.keys()) == EXPECTED_SIMULATOR_AGENT_KEYS
        uuid.UUID(row["id"])
        assert row["name"] == "Shape Agent"
        assert row["voice_provider"] == "elevenlabs"
        assert row["voice_name"] == "marissa"
        assert row["model"] == "gpt-4"
        assert row["prompt"] == "Prompt."
        assert row["deleted"] is False

    def test_list_ordered_by_created_at_desc(
        self, auth_client, organization, workspace
    ):
        now = timezone.now()
        agents = []
        for index in range(3):
            agent = SimulatorAgent.objects.create(
                name=f"Ordering Agent {index}",
                prompt="Prompt.",
                voice_provider="elevenlabs",
                voice_name="marissa",
                model="gpt-4",
                organization=organization,
                workspace=workspace,
            )
            SimulatorAgent.no_workspace_objects.filter(id=agent.id).update(
                created_at=now - timedelta(minutes=10 - index)
            )
            agents.append(agent)

        response = auth_client.get(
            "/simulate/simulator-agents/?search=Ordering Agent&limit=50"
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        listed_ids = [
            row["id"]
            for row in response.json()["results"]
            if row["name"].startswith("Ordering Agent ")
        ]
        expected = [str(a.id) for a in reversed(agents)]
        assert listed_ids == expected


@pytest.mark.integration
@pytest.mark.api
class TestSimulatorAgentDetailView:
    def test_get_detail_returns_expected_body_shape(
        self, auth_client, simulator_agent
    ):
        response = auth_client.get(
            f"/simulate/simulator-agents/{simulator_agent.id}/"
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        assert set(body.keys()) == EXPECTED_SIMULATOR_AGENT_KEYS
        assert body["id"] == str(simulator_agent.id)
        assert body["name"] == simulator_agent.name
        assert body["prompt"] == simulator_agent.prompt
        assert body["voice_provider"] == simulator_agent.voice_provider
        assert body["voice_name"] == simulator_agent.voice_name
        assert body["model"] == simulator_agent.model
        assert body["deleted"] is False

    def test_get_detail_not_found_returns_404(self, auth_client):
        response = auth_client.get(
            f"/simulate/simulator-agents/{uuid.uuid4()}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_detail_unauthenticated_returns_401(
        self, api_client, simulator_agent
    ):
        response = api_client.get(
            f"/simulate/simulator-agents/{simulator_agent.id}/"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.api
class TestSimulatorAgentDeleteView:
    def test_delete_already_deleted_returns_404(
        self, auth_client, simulator_agent
    ):
        first = auth_client.delete(
            f"/simulate/simulator-agents/{simulator_agent.id}/delete/"
        )
        assert first.status_code == status.HTTP_200_OK

        second = auth_client.delete(
            f"/simulate/simulator-agents/{simulator_agent.id}/delete/"
        )
        assert second.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_not_found_returns_404(self, auth_client):
        response = auth_client.delete(
            f"/simulate/simulator-agents/{uuid.uuid4()}/delete/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
