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

from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials

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
            assert result["agent_type"] == "voice"

    def test_rejects_unknown_query_params(self, auth_client):
        response = auth_client.get("/simulate/agent-definitions/?legacyFilter=voice")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacyFilter"] == ["Unknown field."]

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
        assert "latest_version" in result
        assert "latest_version_id" in result

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
            "agent_name": "New Voice Bot",
            "agent_type": "voice",
            "provider": "vapi",
            "contact_number": "+12345678901",
            "inbound": True,
            "commit_message": "Initial version",
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

    def test_create_voice_agent_response_masks_api_key(self, auth_client):
        raw_api_key = "sk-agent-definition-raw-secret-123456"
        payload = {
            "agent_name": "Masked Voice Bot",
            "agent_type": "voice",
            "provider": "vapi",
            "contact_number": "+12345678901",
            "inbound": True,
            "commit_message": "Initial version",
            "languages": ["en"],
            "description": "A voice bot with provider credentials",
            "api_key": raw_api_key,
            "assistant_id": "asst_masked",
            "authentication_method": "api_key",
        }

        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        serialized = response.json()
        assert raw_api_key not in str(serialized)
        assert serialized["agent"]["api_key"] != raw_api_key
        assert serialized["agent"]["api_key"].startswith("sk-a")

        detail = auth_client.get(
            f"/simulate/agent-definitions/{serialized['agent']['id']}/"
        )
        assert detail.status_code == status.HTTP_200_OK
        assert raw_api_key not in str(detail.json())

    def test_create_text_agent_success(self, auth_client):
        payload = {
            "agent_name": "New Text Bot",
            "agent_type": "text",
            "commit_message": "Initial text agent",
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

    def test_create_text_agent_does_not_seed_empty_provider_credentials(
        self, auth_client
    ):
        payload = {
            "agent_name": "New Text Bot Without Credentials",
            "agent_type": "text",
            "commit_message": "Initial text agent",
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
        agent_id = response.json()["agent"]["id"]
        assert not ProviderCredentials.objects.filter(
            agent_definition_id=agent_id
        ).exists()
        assert not ProviderCredentials.objects.filter(
            agent_version__agent_definition_id=agent_id
        ).exists()

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

    def test_create_rejects_unknown_fields(self, auth_client):
        payload = {
            "agent_name": "New Text Bot",
            "agent_type": "text",
            "commit_message": "Initial text agent",
            "inbound": True,
            "legacy_extra": "should-not-be-accepted",
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_invalid_agent_type(self, auth_client):
        payload = {
            "agent_name": "Bad Bot",
            "agent_type": "invalid",
            "commit_message": "Test",
        }
        response = auth_client.post(
            "/simulate/agent-definitions/create/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_voice_without_provider(self, auth_client):
        payload = {
            "agent_name": "Voice Bot",
            "agent_type": "voice",
            "contact_number": "+12345678901",
            "commit_message": "Test",
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
            "agent_name": "Voice Bot",
            "agent_type": "voice",
            "provider": "vapi",
            "commit_message": "Test",
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
            "agent_name": "No Lang Bot",
            "agent_type": "voice",
            "provider": "vapi",
            "contact_number": "+12345678901",
            "inbound": True,
            "commit_message": "Initial version",
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
            "agent_name": "Versioned Bot",
            "agent_type": "text",
            "commit_message": "Initial",
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
            {"agent_name": "Test"},
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
        assert "active_version" in data
        assert "version_count" in data
        assert data["version_count"] == 1

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
            {"agent_name": "Updated Agent Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent definition updated successfully"
        assert data["agent"]["agent_name"] == "Updated Agent Name"

    def test_edit_masked_api_key_preserves_existing_secret(self, auth_client):
        raw_api_key = "sk-edit-preserve-secret-123456"
        create_response = auth_client.post(
            "/simulate/agent-definitions/create/",
            {
                "agent_name": "Edit Masked Secret Bot",
                "agent_type": "voice",
                "provider": "vapi",
                "contact_number": "+12345678901",
                "inbound": True,
                "commit_message": "Initial version",
                "languages": ["en"],
                "description": "A voice bot with credentials",
                "api_key": raw_api_key,
                "assistant_id": "asst_edit_masked",
                "authentication_method": "api_key",
            },
            format="json",
        )
        assert create_response.status_code == status.HTTP_201_CREATED
        agent_id = create_response.json()["agent"]["id"]
        masked_api_key = create_response.json()["agent"]["api_key"]

        response = auth_client.put(
            f"/simulate/agent-definitions/{agent_id}/edit/",
            {
                "description": "Updated without rotating credentials",
                "api_key": masked_api_key,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        agent = AgentDefinition.objects.get(id=agent_id)
        assert agent.latest_version.credentials.get_api_key() == raw_api_key
        assert raw_api_key not in str(response.json())

    def test_partial_update(self, auth_client, agent_definition):
        original_description = agent_definition.description
        response = auth_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {"agent_name": "Only Name Changed"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        agent_definition.refresh_from_db()
        assert agent_definition.agent_name == "Only Name Changed"
        assert agent_definition.description == original_description

    def test_invalid_data(self, auth_client, agent_definition):
        response = auth_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {"agent_name": "   "},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_edit_rejects_unknown_fields(self, auth_client, agent_definition):
        response = auth_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {
                "agent_name": "Updated Agent Name",
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_not_found(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.put(
            f"/simulate/agent-definitions/{fake_id}/edit/",
            {"agent_name": "Test"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.put(
            f"/simulate/agent-definitions/{agent_definition.id}/edit/",
            {"agent_name": "Test"},
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
            {"agent_ids": [str(agent_definition.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agents deleted successfully"
        assert data["agents_updated"] == 1
        assert data["versions_updated"] >= 1

    def test_empty_list(self, auth_client):
        response = auth_client.delete(
            "/simulate/agent-definitions/",
            {"agent_ids": []},
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

    def test_bulk_delete_rejects_unknown_fields(self, auth_client, agent_definition):
        response = auth_client.delete(
            "/simulate/agent-definitions/",
            {
                "agent_ids": [str(agent_definition.id)],
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_nonexistent_ids(self, auth_client):
        response = auth_client.delete(
            "/simulate/agent-definitions/",
            {"agent_ids": [str(uuid.uuid4())]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["agents_updated"] == 0

    def test_unauthenticated(self, api_client):
        response = api_client.delete(
            "/simulate/agent-definitions/",
            {"agent_ids": [str(uuid.uuid4())]},
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

    def test_delete_soft_deletes_versions(self, auth_client, agent_definition):
        version = agent_definition.create_version(
            description="Version to cascade",
            commit_message="Initial",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        response = auth_client.delete(
            f"/simulate/agent-definitions/{agent_definition.id}/delete/"
        )

        assert response.status_code == status.HTTP_200_OK
        agent_definition.refresh_from_db()
        version.refresh_from_db()
        assert agent_definition.deleted is True
        assert agent_definition.deleted_at is not None
        assert version.deleted is True
        assert version.deleted_at is not None

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
        with (
            patch("tfc.ee_gating.check_ee_feature", return_value=None),
            patch("simulate.views.agent_definition.VapiService") as MockVapi,
        ):
            mock_instance = MagicMock()
            mock_instance.get_assistant.return_value = mock_assistant
            MockVapi.return_value = mock_instance

            response = auth_client.post(
                self.URL,
                {
                    "assistant_id": "asst_123",
                    "api_key": "key_123",
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
        with (
            patch("tfc.ee_gating.check_ee_feature", return_value=None),
            patch("simulate.views.agent_definition.VapiService") as MockVapi,
        ):
            mock_instance = MagicMock()
            mock_instance.get_assistant.side_effect = Exception("Invalid API key")
            MockVapi.return_value = mock_instance

            response = auth_client.post(
                self.URL,
                {
                    "assistant_id": "bad",
                    "api_key": "bad",
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
                    "assistant_id": "asst_123",
                    "api_key": "key_123",
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

    def test_rejects_unknown_fields(self, auth_client):
        response = auth_client.post(
            self.URL,
            {
                "assistant_id": "asst_123",
                "api_key": "key_123",
                "provider": "vapi",
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_unauthenticated(self, api_client):
        response = api_client.post(
            self.URL,
            {"assistant_id": "a", "api_key": "b", "provider": "vapi"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestAgentDefinitionOperationsCRUD:
    """Regression coverage for the router-backed agent definition CRUD API."""

    URL = "/simulate/api/agent-definition-operations/"

    def test_text_agent_crud_does_not_use_read_only_serializer(self, auth_client):
        payload = {
            "agent_name": "Operations Text Bot",
            "agent_type": "text",
            "inbound": True,
            "languages": ["en"],
            "description": "Created through the operations ViewSet.",
            "model": "gpt-4o-mini",
            "model_details": {"source": "test"},
        }
        response = auth_client.post(self.URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        created = response.json()
        agent_id = created["id"]
        assert created["agent_name"] == payload["agent_name"]
        assert created["inbound"] is True

        patch_response = auth_client.patch(
            f"{self.URL}{agent_id}/",
            {"description": "Updated through the operations ViewSet."},
            format="json",
        )
        assert patch_response.status_code == status.HTTP_200_OK
        assert patch_response.json()["description"] == (
            "Updated through the operations ViewSet."
        )

        delete_response = auth_client.delete(f"{self.URL}{agent_id}/")
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT

    def test_create_rejects_unknown_fields(self, auth_client):
        response = auth_client.post(
            self.URL,
            {
                "agent_name": "Operations Text Bot",
                "agent_type": "text",
                "inbound": True,
                "languages": ["en"],
                "description": "Created through the operations ViewSet.",
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        details = response.data.get("details", response.data)
        assert details["legacy_extra"] == ["Unknown field."]

    def test_delete_soft_deletes_versions(self, auth_client, agent_definition):
        version = agent_definition.create_version(
            description="Version to cascade through operations API",
            commit_message="Initial",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        response = auth_client.delete(f"{self.URL}{agent_definition.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        agent_definition.refresh_from_db()
        version.refresh_from_db()
        assert agent_definition.deleted is True
        assert agent_definition.deleted_at is not None
        assert version.deleted is True
        assert version.deleted_at is not None

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def test_list_operations_returns_workspace_scoped_agents(
        self,
        auth_client,
        organization,
        workspace,
        user,
        agent_definition,
        agent_definition_text,
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Operations Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = AgentDefinition.no_workspace_objects.create(
            agent_name="Hidden Operations Agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="Hidden from the requesting workspace",
            organization=organization,
            workspace=other_workspace,
            languages=["en"],
        )

        response = auth_client.get(self.URL)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert "count" in data
        listed_ids = {row["id"] for row in data["results"]}
        assert str(agent_definition.id) in listed_ids
        assert str(agent_definition_text.id) in listed_ids
        assert str(hidden_agent.id) not in listed_ids
        # Detail-serializer shape: assert on a real field returned by
        # AgentDefinitionResponseSerializer.
        sample = next(
            row for row in data["results"] if row["id"] == str(agent_definition.id)
        )
        assert sample["agent_name"] == agent_definition.agent_name
        assert sample["agent_type"] == agent_definition.agent_type

    def test_list_operations_unauthenticated_returns_401(self, api_client):
        response = api_client.get(self.URL)
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    # ------------------------------------------------------------------
    # retrieve
    # ------------------------------------------------------------------

    def test_retrieve_operation_returns_agent_fields(
        self, auth_client, agent_definition
    ):
        response = auth_client.get(f"{self.URL}{agent_definition.id}/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(agent_definition.id)
        assert data["agent_name"] == agent_definition.agent_name
        assert data["agent_type"] == agent_definition.agent_type
        assert data["inbound"] is True
        assert data["provider"] == agent_definition.provider
        assert data["contact_number"] == agent_definition.contact_number

    def test_retrieve_operation_unauthenticated_returns_401(
        self, api_client, agent_definition
    ):
        response = api_client.get(f"{self.URL}{agent_definition.id}/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_retrieve_operation_not_found_returns_404(self, auth_client):
        response = auth_client.get(f"{self.URL}{uuid.uuid4()}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_operation_other_workspace_returns_404(
        self, auth_client, organization, user
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Retrieve Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = AgentDefinition.no_workspace_objects.create(
            agent_name="Hidden Retrieve Agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="Hidden retrieve target",
            organization=organization,
            workspace=other_workspace,
            languages=["en"],
        )

        response = auth_client.get(f"{self.URL}{hidden_agent.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_agent.refresh_from_db()
        assert hidden_agent.agent_name == "Hidden Retrieve Agent"

    # ------------------------------------------------------------------
    # update (PUT)
    # ------------------------------------------------------------------

    def test_put_update_operation_persists_changes(
        self, auth_client, agent_definition_text
    ):
        payload = {
            "agent_name": "Renamed Operations Text Bot",
            "agent_type": agent_definition_text.agent_type,
            "inbound": True,
            "languages": ["en"],
            "description": "Renamed via PUT.",
        }
        response = auth_client.put(
            f"{self.URL}{agent_definition_text.id}/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["agent_name"] == "Renamed Operations Text Bot"
        assert body["description"] == "Renamed via PUT."

        agent_definition_text.refresh_from_db()
        assert agent_definition_text.agent_name == "Renamed Operations Text Bot"
        assert agent_definition_text.description == "Renamed via PUT."

    def test_put_update_operation_unauthenticated_returns_401(
        self, api_client, agent_definition_text
    ):
        response = api_client.put(
            f"{self.URL}{agent_definition_text.id}/",
            {
                "agent_name": "Should Not Persist",
                "agent_type": agent_definition_text.agent_type,
                "inbound": True,
                "languages": ["en"],
            },
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
        agent_definition_text.refresh_from_db()
        assert agent_definition_text.agent_name == "Test Text Agent"

    def test_put_update_operation_not_found_returns_404(self, auth_client):
        response = auth_client.put(
            f"{self.URL}{uuid.uuid4()}/",
            {
                "agent_name": "Ghost Bot",
                "agent_type": "text",
                "inbound": True,
                "languages": ["en"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_put_update_operation_other_workspace_returns_404(
        self, auth_client, organization, user
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Update Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = AgentDefinition.no_workspace_objects.create(
            agent_name="Hidden Update Agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="Hidden update target",
            organization=organization,
            workspace=other_workspace,
            languages=["en"],
        )

        response = auth_client.put(
            f"{self.URL}{hidden_agent.id}/",
            {
                "agent_name": "Leaked Hidden Agent",
                "agent_type": hidden_agent.agent_type,
                "inbound": True,
                "languages": ["en"],
                "description": "Should not persist",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_agent.refresh_from_db()
        assert hidden_agent.agent_name == "Hidden Update Agent"
        assert hidden_agent.description == "Hidden update target"

    # ------------------------------------------------------------------
    # partial_update (PATCH)
    # ------------------------------------------------------------------

    def test_partial_update_operation_persists_field_change(
        self, auth_client, agent_definition_text
    ):
        original_name = agent_definition_text.agent_name
        original_type = agent_definition_text.agent_type
        original_inbound = agent_definition_text.inbound

        response = auth_client.patch(
            f"{self.URL}{agent_definition_text.id}/",
            {"description": "new-value"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["description"] == "new-value"

        agent_definition_text.refresh_from_db()
        assert agent_definition_text.description == "new-value"
        assert agent_definition_text.agent_name == original_name
        assert agent_definition_text.agent_type == original_type
        assert agent_definition_text.inbound == original_inbound

    def test_partial_update_operation_ignores_read_only_fields(
        self, auth_client, agent_definition_text, organization
    ):
        original_id = agent_definition_text.id
        original_org_id = agent_definition_text.organization_id
        other_org_id = uuid.uuid4()

        response = auth_client.patch(
            f"{self.URL}{agent_definition_text.id}/",
            {
                "id": str(uuid.uuid4()),
                "organization": str(other_org_id),
                "description": "read-only-check",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        agent_definition_text.refresh_from_db()
        assert agent_definition_text.id == original_id
        assert agent_definition_text.organization_id == original_org_id
        assert agent_definition_text.description == "read-only-check"

    def test_partial_update_operation_unauthenticated_returns_401(
        self, api_client, agent_definition_text
    ):
        response = api_client.patch(
            f"{self.URL}{agent_definition_text.id}/",
            {"description": "should-not-persist"},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
        agent_definition_text.refresh_from_db()
        assert agent_definition_text.description == "Test text agent"

    def test_partial_update_operation_not_found_returns_404(self, auth_client):
        response = auth_client.patch(
            f"{self.URL}{uuid.uuid4()}/",
            {"description": "ghost"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_partial_update_operation_other_workspace_returns_404(
        self, auth_client, organization, user
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Patch Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = AgentDefinition.no_workspace_objects.create(
            agent_name="Hidden Patch Agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="Hidden patch target",
            organization=organization,
            workspace=other_workspace,
            languages=["en"],
        )

        response = auth_client.patch(
            f"{self.URL}{hidden_agent.id}/",
            {"description": "leaked"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_agent.refresh_from_db()
        assert hidden_agent.description == "Hidden patch target"

    # ------------------------------------------------------------------
    # destroy (DELETE)
    # ------------------------------------------------------------------

    def test_destroy_operation_soft_deletes(self, auth_client, agent_definition):
        response = auth_client.delete(f"{self.URL}{agent_definition.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        agent_definition.refresh_from_db()
        assert agent_definition.deleted is True
        assert agent_definition.deleted_at is not None

    def test_destroy_operation_unauthenticated_returns_401(
        self, api_client, agent_definition
    ):
        response = api_client.delete(f"{self.URL}{agent_definition.id}/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
        agent_definition.refresh_from_db()
        assert agent_definition.deleted is False
        assert agent_definition.deleted_at is None

    def test_destroy_operation_not_found_returns_404(self, auth_client):
        response = auth_client.delete(f"{self.URL}{uuid.uuid4()}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_destroy_operation_other_workspace_returns_404(
        self, auth_client, organization, user
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Destroy Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = AgentDefinition.no_workspace_objects.create(
            agent_name="Hidden Destroy Agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="Hidden destroy target",
            organization=organization,
            workspace=other_workspace,
            languages=["en"],
        )

        response = auth_client.delete(f"{self.URL}{hidden_agent.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_agent.refresh_from_db()
        assert hidden_agent.deleted is False
        assert hidden_agent.deleted_at is None
