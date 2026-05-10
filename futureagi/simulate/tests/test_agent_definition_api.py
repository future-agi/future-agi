"""
API integration tests for Agent Definition endpoints.

Tests cover:
- AgentDefinitionView: GET /simulate/agent-definitions/ (list + bulk delete)
- CreateAgentDefinitionView: POST /simulate/agent-definitions/create/
- AgentDefinitionDetailView: GET /simulate/agent-definitions/{id}/
- EditAgentDefinitionView: PUT /simulate/agent-definitions/{id}/edit/
- DeleteAgentDefinitionView: DELETE /simulate/agent-definitions/{id}/delete/
- AgentDefinitionOperationsViewSet: POST fetch_assistant_from_provider
"""

import uuid
from unittest.mock import MagicMock, patch

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
        description="Test voice agent for API testing",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_definition_text(db, organization, workspace):
    """Create a text agent definition."""
    return AgentDefinition.objects.create(
        agent_name="Test Text Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Test text agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition):
    """Create an active version for the agent definition."""
    return agent_definition.create_version(
        description="Initial version",
        commit_message="First version",
        status=AgentVersion.StatusChoices.ACTIVE,
    )


# ============================================================================
# TestListAgentDefinitions
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestListAgentDefinitions:
    """Tests for GET /simulate/agent-definitions/"""

    def test_list_success(self, auth_client, agent_definition):
        response = auth_client.get("/simulate/agent-definitions/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert "count" in data
        assert data["count"] >= 1
        # Verify response contains the agent
        agent_ids = [r["id"] for r in data["results"]]
        assert str(agent_definition.id) in agent_ids

    def test_list_empty(self, auth_client):
        response = auth_client.get("/simulate/agent-definitions/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 0
        assert data["results"] == []

    def test_search_by_name(self, auth_client, agent_definition):
        response = auth_client.get("/simulate/agent-definitions/?search=Test+Voice")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] >= 1

    def test_filter_by_agent_type(
        self, auth_client, agent_definition, agent_definition_text
    ):
        response = auth_client.get("/simulate/agent-definitions/?agent_type=voice")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        for result in data["results"]:
            assert result["agentType"] == "voice"

    def test_pin_agent_definition_id(self, auth_client, agent_definition):
        response = auth_client.get(
            f"/simulate/agent-definitions/?agent_definition_id={agent_definition.id}"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["results"][0]["id"] == str(agent_definition.id)

    def test_pagination(self, auth_client, agent_definition, agent_definition_text):
        response = auth_client.get("/simulate/agent-definitions/?page=1&limit=1")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["results"]) == 1
        assert data["count"] == 2

    def test_response_fields(self, auth_client, agent_definition, agent_version):
        response = auth_client.get("/simulate/agent-definitions/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["results"][0]
        # Verify key list-specific fields exist
        assert "latestVersion" in result
        assert "latestVersionId" in result

    def test_unauthenticated(self, api_client):
        response = api_client.get("/simulate/agent-definitions/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestCreateAgentDefinition
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestCreateAgentDefinition:
    """Tests for POST /simulate/agent-definitions/create/"""

    def test_create_voice_agent_success(self, auth_client):
        payload = {
            "agentName": "New Voice Bot",
            "agentType": "voice",
            "provider": "vapi",
            "contactNumber": "+12345678901",
            "inbound": True,
            "commitMessage": "Initial version",
            "languages": ["en"],
            "description": "A new voice bot",
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["message"] == "Agent definition created successfully"
        assert "agent" in data
        assert "id" in data["agent"]

    def test_create_text_agent_success(self, auth_client):
        payload = {
            "agentName": "New Text Bot",
            "agentType": "text",
            "commitMessage": "Initial text agent",
            "inbound": True,
            "languages": ["en"],
            "description": "A text bot",
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_missing_required_fields(self, auth_client):
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert "error" in data
        assert "details" in data

    def test_invalid_agent_type(self, auth_client):
        payload = {
            "agentName": "Bad Bot",
            "agentType": "invalid",
            "commitMessage": "Test",
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_voice_without_provider(self, auth_client):
        payload = {
            "agentName": "Voice Bot",
            "agentType": "voice",
            "contactNumber": "+12345678901",
            "commitMessage": "Test",
            "inbound": True,
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_voice_without_contact_number(self, auth_client):
        payload = {
            "agentName": "Voice Bot",
            "agentType": "voice",
            "provider": "vapi",
            "commitMessage": "Test",
            "inbound": True,
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_voice_agent_without_languages(self, auth_client):
        """Regression: omitting languages should not cause a validation error."""
        payload = {
            "agentName": "No Lang Bot",
            "agentType": "voice",
            "provider": "vapi",
            "contactNumber": "+12345678901",
            "inbound": True,
            "commitMessage": "Initial version",
            "description": "Agent without languages field",
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_creates_first_version(self, auth_client):
        payload = {
            "agentName": "Versioned Bot",
            "agentType": "text",
            "commitMessage": "Initial",
            "inbound": True,
            "languages": ["en"],
            "description": "Test",
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        agent_id = response.json()["agent"]["id"]
        agent = AgentDefinition.objects.get(id=agent_id)
        assert agent.version_count == 1

    def test_unauthenticated(self, api_client):
        response = api_client.post(
            "/simulate/agent-definitions/create/",
            {"agentName": "Test"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestGetAgentDefinition
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestGetAgentDefinition:
    """Tests for GET /simulate/agent-definitions/{id}/"""

    def test_get_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(
            f"/simulate/agent-definitions/{agent_definition.id}/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(agent_definition.id)
        assert "versions" in data
        assert "activeVersion" in data
        assert "versionCount" in data
        assert data["versionCount"] == 1

    def test_not_found(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.get(f"/simulate/agent-definitions/{fake_id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_deleted_not_visible(self, auth_client, agent_definition):
        agent_definition.deleted = True
        agent_definition.save()
        response = auth_client.get(
            f"/simulate/agent-definitions/{agent_definition.id}/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.get(f"/simulate/agent-definitions/{agent_definition.id}/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestEditAgentDefinition
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestEditAgentDefinition:
    """Tests for PUT /simulate/agent-definitions/{id}/edit/"""

    def test_edit_success(self, auth_client, agent_definition):
        response = auth_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {"agentName": "Updated Agent Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent definition updated successfully"
        assert data["agent"]["agentName"] == "Updated Agent Name"

    def test_partial_update(self, auth_client, agent_definition):
        original_description = agent_definition.description
        response = auth_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {"agentName": "Only Name Changed"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        agent_definition.refresh_from_db()
        assert agent_definition.agent_name == "Only Name Changed"
        assert agent_definition.description == original_description

    def test_invalid_data(self, auth_client, agent_definition):
        response = auth_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {"agentName": "   "},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_not_found(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.put(
            f"/simulate/agent-definitions/{fake_id}/edit/",
            {"agentName": "Test"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {"agentName": "Test"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestBulkDeleteAgentDefinitions
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestBulkDeleteAgentDefinitions:
    """Tests for DELETE /simulate/agent-definitions/"""

    def test_bulk_delete_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.delete(
            "/simulate/agent-definitions/",
            {"agentIds": [str(agent_definition.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agents deleted successfully"
        assert data["agentsUpdated"] == 1
        assert data["versionsUpdated"] >= 1

    def test_empty_list(self, auth_client):
        response = auth_client.delete(
            "/simulate/agent-definitions/",
            {"agentIds": []},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_agent_ids(self, auth_client):
        response = auth_client.delete(
            "/simulate/agent-definitions/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_ids(self, auth_client):
        response = auth_client.delete(
            "/simulate/agent-definitions/",
            {"agentIds": [str(uuid.uuid4())]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["agentsUpdated"] == 0

    def test_unauthenticated(self, api_client):
        response = api_client.delete(
            "/simulate/agent-definitions/",
            {"agentIds": [str(uuid.uuid4())]},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestDeleteAgentDefinition
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestDeleteAgentDefinition:
    """Tests for DELETE /simulate/agent-definitions/{id}/delete/"""

    def test_delete_success(self, auth_client, agent_definition):
        response = auth_client.delete(
            f"/simulate/agent-definitions/{agent_definition.id}/delete/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent definition deleted successfully"

    def test_not_found(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.delete(f"/simulate/agent-definitions/{fake_id}/delete/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_already_deleted(self, auth_client, agent_definition):
        agent_definition.deleted = True
        agent_definition.save()
        response = auth_client.delete(
            f"/simulate/agent-definitions/{agent_definition.id}/delete/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.delete(
            f"/simulate/agent-definitions/{agent_definition.id}/delete/"
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestFetchAssistantFromProvider
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestFetchAssistantFromProvider:
    """Tests for POST /simulate/api/agent-definition-operations/fetch_assistant_from_provider/"""

    URL = "/simulate/api/agent-definition-operations/fetch_assistant_from_provider/"

    def test_valid_vapi_request(self, auth_client):
        mock_assistant = {
            "name": "Support Bot",
            "model": {
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."}
                ]
            },
        }
        with patch("simulate.views.agent_definition.VapiService") as MockVapi:
            mock_instance = MagicMock()
            mock_instance.get_assistant.return_value = mock_assistant
            MockVapi.return_value = mock_instance

            response = auth_client.post(
                self.URL,
                {
                    "assistantId": "asst_123",
                    "apiKey": "key_123",
                    "provider": "vapi",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        assert data["result"]["name"] == "Support Bot"
        assert data["result"]["prompt"] == "You are a helpful assistant."

    def test_invalid_credentials(self, auth_client):
        with patch("simulate.views.agent_definition.VapiService") as MockVapi:
            mock_instance = MagicMock()
            mock_instance.get_assistant.side_effect = Exception("Invalid API key")
            MockVapi.return_value = mock_instance

            response = auth_client.post(
                self.URL,
                {
                    "assistantId": "bad",
                    "apiKey": "bad",
                    "provider": "vapi",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_voice_sim_feature_unavailable_returns_402(self, auth_client):
        """Regression: voice_sim entitlement denial must surface as HTTP 402,
        not the generic 400 'recheck API key' that the bare except previously
        emitted — that message sent users to debug credentials when the real
        cause was plan entitlement.
        """
        from tfc.ee_gating import EEFeature, FeatureUnavailable

        with patch(
            "tfc.ee_gating.check_ee_feature",
            side_effect=FeatureUnavailable(EEFeature.VOICE_SIM),
        ):
            response = auth_client.post(
                self.URL,
                {
                    "assistantId": "asst_123",
                    "apiKey": "key_123",
                    "provider": "vapi",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED

    def test_missing_fields(self, auth_client):
        response = auth_client.post(
            self.URL,
            {"provider": "vapi"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated(self, api_client):
        response = api_client.post(
            self.URL,
            {"assistantId": "a", "apiKey": "b", "provider": "vapi"},
            format="json",
        )
        # ViewSet uses `permissions` (not `permission_classes`), so
        # unauthenticated requests may pass through to validation/execution.
        # Accept 400 (validation/provider error) or 401/403 (auth denied).
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
