"""
API integration tests for Agent Version endpoints.

Tests cover:
- AgentVersionListView: GET /simulate/agent-definitions/{id}/versions/
- CreateAgentVersionView: POST /simulate/agent-definitions/{id}/versions/create/
- AgentVersionDetailView: GET /simulate/agent-definitions/{id}/versions/{id}/
- ActivateAgentVersionView: POST /simulate/agent-definitions/{id}/versions/{id}/activate/
- DeleteAgentVersionView: DELETE /simulate/agent-definitions/{id}/versions/{id}/delete/
- RestoreAgentVersionView: POST /simulate/agent-definitions/{id}/versions/{id}/restore/
- AgentVersionEvalSummaryView: GET .../eval-summary/
- AgentVersionCallExecutionView: GET .../call-executions/
"""

import uuid

import pytest
from rest_framework import status

from simulate.models import AgentDefinition, AgentVersion

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a voice agent definition."""
    return AgentDefinition.objects.create(
        agent_name="Test Voice Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        description="Test voice agent",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition):
    """Create an active version."""
    return agent_definition.create_version(
        description="Initial version",
        commit_message="First version",
        status=AgentVersion.StatusChoices.ACTIVE,
    )


@pytest.fixture
def second_version(db, agent_definition, agent_version):
    """Create a second archived version."""
    return agent_definition.create_version(
        description="Second version",
        commit_message="Second version",
        status=AgentVersion.StatusChoices.ARCHIVED,
    )


def _url(agent_id, suffix=""):
    return f"/simulate/agent-definitions/{agent_id}/versions/{suffix}"


def _version_url(agent_id, version_id, suffix=""):
    return f"/simulate/agent-definitions/{agent_id}/versions/{version_id}/{suffix}"


# ============================================================================
# TestListAgentVersions
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestListAgentVersions:
    """Tests for GET /simulate/agent-definitions/{id}/versions/"""

    def test_list_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(_url(agent_definition.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert "count" in data
        assert data["count"] >= 1
        # Verify version fields present
        version_data = data["results"][0]
        assert "id" in version_data
        assert "version_number" in version_data
        assert "version_name_display" in version_data
        assert "is_active" in version_data
        assert "is_latest" in version_data

    def test_agent_not_found(self, auth_client):
        response = auth_client.get(_url(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_pagination(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        response = auth_client.get(_url(agent_definition.id) + "?page=1&limit=1")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["results"]) == 1
        assert data["count"] == 2

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.get(_url(agent_definition.id))
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestCreateAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestCreateAgentVersion:
    """Tests for POST /simulate/agent-definitions/{id}/versions/create/"""

    def test_create_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {
                "commit_message": "Updated prompts",
                "description": "Better refund handling",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["message"] == "Agent version created successfully"
        assert "version" in data
        assert data["version"]["version_number"] == 2

    def test_creates_snapshot(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {"commit_message": "Snapshot test"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        version_id = response.json()["version"]["id"]
        version = AgentVersion.objects.get(id=version_id)
        assert version.configuration_snapshot is not None
        assert isinstance(version.configuration_snapshot, dict)
        assert "agent_name" in version.configuration_snapshot

    def test_updates_agent_fields(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {
                "agent_name": "Renamed Agent",
                "commit_message": "Renamed",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        agent_definition.refresh_from_db()
        assert agent_definition.agent_name == "Renamed Agent"

    def test_agent_not_found(self, auth_client):
        response = auth_client.post(
            _url(uuid.uuid4(), "create/"),
            {"commit_message": "Test"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_invalid_data(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {"agent_name": "   "},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.post(
            _url(agent_definition.id, "create/"),
            {"commit_message": "Test"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestGetAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestGetAgentVersion:
    """Tests for GET /simulate/agent-definitions/{id}/versions/{id}/"""

    def test_get_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(_version_url(agent_definition.id, agent_version.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(agent_version.id)
        assert "configuration_snapshot" in data
        assert isinstance(data["configuration_snapshot"], dict)

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.get(_version_url(uuid.uuid4(), agent_version.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.get(_version_url(agent_definition.id, uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_snapshot_uuids_are_strings(
        self, auth_client, agent_definition, agent_version
    ):
        response = auth_client.get(_version_url(agent_definition.id, agent_version.id))
        data = response.json()
        snapshot = data["configuration_snapshot"]
        for key, value in snapshot.items():
            if value is not None:
                assert isinstance(
                    value, (str, int, float, bool, list, dict)
                ), f"Snapshot key '{key}' has type {type(value)}"

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.get(_version_url(agent_definition.id, agent_version.id))
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestActivateAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestActivateAgentVersion:
    """Tests for POST /simulate/agent-definitions/{id}/versions/{id}/activate/"""

    def test_activate_success(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        response = auth_client.post(
            _version_url(agent_definition.id, second_version.id, "activate/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent version activated successfully"
        assert data["version"]["status"] == "active"

    def test_archives_other_versions(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        auth_client.post(
            _version_url(agent_definition.id, second_version.id, "activate/")
        )
        agent_version.refresh_from_db()
        assert agent_version.status == AgentVersion.StatusChoices.ARCHIVED

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.post(
            _version_url(uuid.uuid4(), agent_version.id, "activate/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.post(
            _version_url(agent_definition.id, uuid.uuid4(), "activate/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.post(
            _version_url(agent_definition.id, agent_version.id, "activate/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestDeleteAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestDeleteAgentVersion:
    """Tests for DELETE /simulate/agent-definitions/{id}/versions/{id}/delete/"""

    def test_delete_success(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        # Delete the archived version (not the only active one)
        response = auth_client.delete(
            _version_url(agent_definition.id, second_version.id, "delete/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent version deleted successfully"

    def test_cannot_delete_only_active(
        self, auth_client, agent_definition, agent_version
    ):
        response = auth_client.delete(
            _version_url(agent_definition.id, agent_version.id, "delete/")
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Cannot delete the only active version" in response.json()["error"]

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.delete(
            _version_url(uuid.uuid4(), agent_version.id, "delete/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.delete(
            _version_url(agent_definition.id, uuid.uuid4(), "delete/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.delete(
            _version_url(agent_definition.id, agent_version.id, "delete/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestRestoreAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRestoreAgentVersion:
    """Tests for POST /simulate/agent-definitions/{id}/versions/{id}/restore/"""

    def test_restore_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _version_url(agent_definition.id, agent_version.id, "restore/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent definition restored successfully from version"
        assert "agent" in data
        assert "version" in data

    def test_restores_agent_fields(self, auth_client, agent_definition, agent_version):
        # Change agent name
        agent_definition.agent_name = "Changed Name"
        agent_definition.save()

        # Restore from version snapshot
        auth_client.post(
            _version_url(agent_definition.id, agent_version.id, "restore/")
        )

        agent_definition.refresh_from_db()
        assert agent_definition.agent_name == "Test Voice Agent"

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.post(
            _version_url(uuid.uuid4(), agent_version.id, "restore/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.post(
            _version_url(agent_definition.id, uuid.uuid4(), "restore/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# TestAgentVersionEvalSummary
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestAgentVersionEvalSummary:
    """Tests for GET .../eval-summary/"""

    def test_empty_eval_summary(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(
            _version_url(agent_definition.id, agent_version.id, "eval-summary/")
        )
        assert response.status_code == status.HTTP_200_OK
        # Empty array when no eval configs exist
        assert response.json() == []

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.get(
            _version_url(agent_definition.id, agent_version.id, "eval-summary/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestAgentVersionCallExecutions
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestAgentVersionCallExecutions:
    """Tests for GET .../call-executions/"""

    def test_empty_call_executions(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(
            _version_url(agent_definition.id, agent_version.id, "call-executions/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert data["count"] == 0

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.get(
            _version_url(agent_definition.id, agent_version.id, "call-executions/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
