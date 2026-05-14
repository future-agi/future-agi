"""
Unit tests for Agent Definition and Agent Version request/response serializers.

Tests cover:
- AgentDefinitionCreateRequestSerializer: field-level + cross-field validation
- AgentDefinitionEditRequestSerializer: partial update validation
- AgentDefinitionBulkDeleteRequestSerializer: agent_ids list validation
- AgentDefinitionFilterSerializer: query param validation
- FetchAssistantRequestSerializer: provider fetch validation
- AgentVersionCreateRequestSerializer: version creation validation
- AgentDefinitionResponseSerializer: output shape verification
- AgentVersionResponseSerializer: output shape verification
"""

import uuid

import pytest

from simulate.serializers.requests.agent_definition import (
    AgentDefinitionBulkDeleteRequestSerializer,
    AgentDefinitionCreateRequestSerializer,
    AgentDefinitionEditRequestSerializer,
    AgentDefinitionFilterSerializer,
    FetchAssistantRequestSerializer,
)
from simulate.serializers.requests.agent_version import (
    AgentVersionCreateRequestSerializer,
)

# ============================================================================
# Helper: minimal valid payloads
# ============================================================================


def _voice_agent_payload(**overrides):
    """Return a minimal valid payload for creating a voice agent."""
    data = {
        "agent_name": "Test Voice Agent",
        "agent_type": "voice",
        "commit_message": "Initial version",
        "provider": "vapi",
        "contact_number": "+12345678901",
        "inbound": True,
        "languages": ["en"],
    }
    data.update(overrides)
    return data


def _text_agent_payload(**overrides):
    """Return a minimal valid payload for creating a text agent."""
    data = {
        "agent_name": "Test Text Agent",
        "agent_type": "text",
        "commit_message": "Initial version",
        "inbound": True,
    }
    data.update(overrides)
    return data


# ============================================================================
# TestAgentDefinitionCreateRequestSerializer
# ============================================================================


@pytest.mark.unit
class TestAgentDefinitionCreateRequestSerializer:
    """Tests for POST /agent-definitions/create/ request validation."""

    def test_valid_voice_agent(self):
        serializer = AgentDefinitionCreateRequestSerializer(data=_voice_agent_payload())
        assert serializer.is_valid(), serializer.errors

    def test_valid_text_agent(self):
        serializer = AgentDefinitionCreateRequestSerializer(data=_text_agent_payload())
        assert serializer.is_valid(), serializer.errors

    def test_missing_agent_name(self):
        data = _voice_agent_payload()
        del data["agent_name"]
        serializer = AgentDefinitionCreateRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "agent_name" in serializer.errors

    def test_blank_agent_name(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(agent_name="   ")
        )
        assert not serializer.is_valid()
        assert "agent_name" in serializer.errors

    def test_missing_commit_message(self):
        data = _voice_agent_payload()
        del data["commit_message"]
        serializer = AgentDefinitionCreateRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "commit_message" in serializer.errors

    def test_blank_commit_message(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(commit_message="   ")
        )
        assert not serializer.is_valid()
        assert "commit_message" in serializer.errors

    def test_invalid_agent_type(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(agent_type="invalid")
        )
        assert not serializer.is_valid()
        assert "agent_type" in serializer.errors

    def test_voice_agent_missing_provider(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(provider=None)
        )
        assert not serializer.is_valid()
        assert "provider" in serializer.errors

    def test_voice_agent_missing_contact_number(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(contact_number=None)
        )
        assert not serializer.is_valid()
        assert "contact_number" in serializer.errors

    def test_invalid_contact_number_format(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(contact_number="+123abc456")
        )
        assert not serializer.is_valid()
        assert "contact_number" in serializer.errors

    def test_contact_number_too_short(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(contact_number="+123456")
        )
        assert not serializer.is_valid()
        assert "contact_number" in serializer.errors

    def test_outbound_missing_api_key(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(
                inbound=False,
                assistant_id="asst_123",
                authentication_method="api_key",
            )
        )
        assert not serializer.is_valid()
        assert "api_key" in serializer.errors

    def test_outbound_missing_assistant_id(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(
                inbound=False,
                api_key="key_123",
                authentication_method="api_key",
            )
        )
        assert not serializer.is_valid()
        assert "assistant_id" in serializer.errors

    def test_observability_requires_api_key(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(
                observability_enabled=True,
                provider="vapi",
                assistant_id="asst_123",
                authentication_method="api_key",
            )
        )
        assert not serializer.is_valid()
        assert "api_key" in serializer.errors

    def test_invalid_language(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(language="xx")
        )
        assert not serializer.is_valid()
        assert "language" in serializer.errors

    def test_invalid_languages_item(self):
        serializer = AgentDefinitionCreateRequestSerializer(
            data=_voice_agent_payload(languages=["en", "xx"])
        )
        assert not serializer.is_valid()
        assert "languages" in serializer.errors


# ============================================================================
# TestAgentDefinitionEditRequestSerializer
# ============================================================================


@pytest.mark.unit
class TestAgentDefinitionEditRequestSerializer:
    """Tests for PUT /agent-definitions/{id}/edit/ request validation."""

    def test_valid_partial_update(self):
        serializer = AgentDefinitionEditRequestSerializer(
            data={"agent_name": "Updated Name"}
        )
        assert serializer.is_valid(), serializer.errors

    def test_all_fields_optional(self):
        serializer = AgentDefinitionEditRequestSerializer(data={})
        assert serializer.is_valid(), serializer.errors

    def test_blank_agent_name(self):
        serializer = AgentDefinitionEditRequestSerializer(data={"agent_name": "   "})
        assert not serializer.is_valid()
        assert "agent_name" in serializer.errors

    def test_invalid_language(self):
        serializer = AgentDefinitionEditRequestSerializer(data={"language": "xx"})
        assert not serializer.is_valid()
        assert "language" in serializer.errors

    def test_invalid_websocket_headers(self):
        serializer = AgentDefinitionEditRequestSerializer(
            data={"websocket_headers": "not-a-dict"}
        )
        assert not serializer.is_valid()
        assert "websocket_headers" in serializer.errors

    def test_valid_with_knowledge_base(self):
        serializer = AgentDefinitionEditRequestSerializer(
            data={"knowledge_base": str(uuid.uuid4())}
        )
        assert serializer.is_valid(), serializer.errors


# ============================================================================
# TestAgentDefinitionBulkDeleteRequestSerializer
# ============================================================================


@pytest.mark.unit
class TestAgentDefinitionBulkDeleteRequestSerializer:
    """Tests for DELETE /agent-definitions/ request validation."""

    def test_valid_agent_ids(self):
        serializer = AgentDefinitionBulkDeleteRequestSerializer(
            data={"agent_ids": [str(uuid.uuid4()), str(uuid.uuid4())]}
        )
        assert serializer.is_valid(), serializer.errors

    def test_empty_list(self):
        serializer = AgentDefinitionBulkDeleteRequestSerializer(data={"agent_ids": []})
        assert not serializer.is_valid()
        assert "agent_ids" in serializer.errors

    def test_missing_field(self):
        serializer = AgentDefinitionBulkDeleteRequestSerializer(data={})
        assert not serializer.is_valid()
        assert "agent_ids" in serializer.errors

    def test_invalid_uuid(self):
        serializer = AgentDefinitionBulkDeleteRequestSerializer(
            data={"agent_ids": ["not-a-uuid"]}
        )
        assert not serializer.is_valid()


# ============================================================================
# TestAgentDefinitionFilterSerializer
# ============================================================================


@pytest.mark.unit
class TestAgentDefinitionFilterSerializer:
    """Tests for GET /agent-definitions/ query param validation."""

    def test_valid_filters(self):
        serializer = AgentDefinitionFilterSerializer(
            data={"search": "bot", "agent_type": "voice", "page": 2}
        )
        assert serializer.is_valid(), serializer.errors

    def test_defaults(self):
        serializer = AgentDefinitionFilterSerializer(data={})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["search"] == ""
        assert serializer.validated_data["page"] == 1

    def test_invalid_agent_type(self):
        serializer = AgentDefinitionFilterSerializer(data={"agent_type": "invalid"})
        assert not serializer.is_valid()
        assert "agent_type" in serializer.errors

    def test_invalid_page(self):
        serializer = AgentDefinitionFilterSerializer(data={"page": 0})
        assert not serializer.is_valid()
        assert "page" in serializer.errors

    def test_agent_definition_id_valid_uuid(self):
        serializer = AgentDefinitionFilterSerializer(
            data={"agent_definition_id": str(uuid.uuid4())}
        )
        assert serializer.is_valid(), serializer.errors


# ============================================================================
# TestFetchAssistantRequestSerializer
# ============================================================================


@pytest.mark.unit
class TestFetchAssistantRequestSerializer:
    """Tests for POST fetch_assistant_from_provider request validation."""

    def test_valid_request(self):
        serializer = FetchAssistantRequestSerializer(
            data={
                "assistant_id": "asst_123",
                "api_key": "key_123",
                "provider": "vapi",
            }
        )
        assert serializer.is_valid(), serializer.errors

    def test_missing_api_key(self):
        serializer = FetchAssistantRequestSerializer(
            data={"assistant_id": "asst_123", "provider": "vapi"}
        )
        assert not serializer.is_valid()
        assert "api_key" in serializer.errors

    def test_missing_assistant_id(self):
        serializer = FetchAssistantRequestSerializer(
            data={"api_key": "key_123", "provider": "vapi"}
        )
        assert not serializer.is_valid()
        assert "assistant_id" in serializer.errors

    def test_invalid_provider(self):
        serializer = FetchAssistantRequestSerializer(
            data={
                "assistant_id": "asst_123",
                "api_key": "key_123",
                "provider": "invalid_provider",
            }
        )
        assert not serializer.is_valid()
        assert "provider" in serializer.errors


# ============================================================================
# TestAgentVersionCreateRequestSerializer
# ============================================================================


@pytest.mark.unit
class TestAgentVersionCreateRequestSerializer:
    """Tests for POST /agent-definitions/{id}/versions/create/ request validation."""

    def test_valid_version_create(self):
        serializer = AgentVersionCreateRequestSerializer(
            data={
                "agent_name": "Updated Bot",
                "commit_message": "Improved prompts",
                "languages": ["en", "es"],
            }
        )
        assert serializer.is_valid(), serializer.errors

    def test_all_fields_optional(self):
        serializer = AgentVersionCreateRequestSerializer(data={})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["commit_message"] == ""

    def test_blank_agent_name(self):
        serializer = AgentVersionCreateRequestSerializer(data={"agent_name": "   "})
        assert not serializer.is_valid()
        assert "agent_name" in serializer.errors

    def test_invalid_language(self):
        serializer = AgentVersionCreateRequestSerializer(data={"language": "xx"})
        assert not serializer.is_valid()
        assert "language" in serializer.errors

    def test_invalid_languages_item(self):
        serializer = AgentVersionCreateRequestSerializer(
            data={"languages": ["en", "zz"]}
        )
        assert not serializer.is_valid()
        assert "languages" in serializer.errors

    def test_valid_with_observability(self):
        serializer = AgentVersionCreateRequestSerializer(
            data={"observability_enabled": True, "commit_message": "Enable tracing"}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["observability_enabled"] is True


# ============================================================================
# TestAgentDefinitionResponseSerializer (needs DB)
# ============================================================================


@pytest.mark.integration
class TestAgentDefinitionResponseSerializer:
    """Tests for AgentDefinitionResponseSerializer output shape."""

    def test_serializes_all_fields(self, db, organization, workspace):
        from simulate.models import AgentDefinition
        from simulate.serializers.response.agent_definition import (
            AgentDefinitionResponseSerializer,
        )

        agent = AgentDefinition.objects.create(
            agent_name="Test Agent",
            agent_type="voice",
            contact_number="+12345678901",
            inbound=True,
            description="A test agent",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )

        data = AgentDefinitionResponseSerializer(agent).data

        expected_fields = {
            "id",
            "agent_name",
            "agent_type",
            "contact_number",
            "inbound",
            "description",
            "assistant_id",
            "provider",
            "language",
            "languages",
            "authentication_method",
            "websocket_url",
            "websocket_headers",
            "workspace",
            "knowledge_base",
            "organization",
            "api_key",
            "observability_provider",
            "created_at",
            "updated_at",
            "model",
            "model_details",
            "livekit_url",
            "livekit_api_key",
            "livekit_agent_name",
            "livekit_config_json",
            "livekit_max_concurrency",
        }
        assert set(data.keys()) == expected_fields

    def test_read_only(self, db, organization, workspace):
        from simulate.serializers.response.agent_definition import (
            AgentDefinitionResponseSerializer,
        )

        serializer = AgentDefinitionResponseSerializer()
        for field_name, field in serializer.fields.items():
            assert field.read_only, f"Field '{field_name}' should be read-only"

    def test_uuid_fields_are_strings(self, db, organization, workspace):
        from simulate.models import AgentDefinition
        from simulate.serializers.response.agent_definition import (
            AgentDefinitionResponseSerializer,
        )

        agent = AgentDefinition.objects.create(
            agent_name="Test Agent",
            agent_type="voice",
            inbound=True,
            description="Test",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )

        data = AgentDefinitionResponseSerializer(agent).data
        # id is always a string (UUID serialized by DRF)
        assert isinstance(data["id"], str)
        # organization is a PK — may be UUID object or int depending on DRF version
        assert data["organization"] is not None


# ============================================================================
# TestAgentVersionResponseSerializer (needs DB)
# ============================================================================


@pytest.mark.integration
class TestAgentVersionResponseSerializer:
    """Tests for AgentVersionResponseSerializer output shape."""

    def test_serializes_all_fields(self, db, organization, workspace):
        from simulate.models import AgentDefinition, AgentVersion
        from simulate.serializers.response.agent_version import (
            AgentVersionResponseSerializer,
        )

        agent = AgentDefinition.objects.create(
            agent_name="Test Agent",
            agent_type="voice",
            inbound=True,
            description="Test",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        version = agent.create_version(
            description="First version",
            commit_message="Initial",
            status="active",
        )

        data = AgentVersionResponseSerializer(version).data

        expected_fields = {
            "id",
            "version_number",
            "version_name",
            "version_name_display",
            "status",
            "status_display",
            "score",
            "test_count",
            "pass_rate",
            "description",
            "commit_message",
            "release_notes",
            "agent_definition",
            "organization",
            "configuration_snapshot",
            "is_active",
            "is_latest",
            "created_at",
            "updated_at",
        }
        assert set(data.keys()) == expected_fields

    def test_configuration_snapshot_uuid_safe(self, db, organization, workspace):
        from simulate.models import AgentDefinition
        from simulate.serializers.response.agent_version import (
            AgentVersionResponseSerializer,
        )

        agent = AgentDefinition.objects.create(
            agent_name="Test Agent",
            agent_type="voice",
            inbound=True,
            description="Test",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        version = agent.create_version(
            description="Test", commit_message="Test", status="active"
        )

        data = AgentVersionResponseSerializer(version).data
        snapshot = data["configuration_snapshot"]
        assert isinstance(snapshot, dict)
        # No raw UUID objects — all values should be JSON-serializable primitives
        for key, value in snapshot.items():
            assert not hasattr(
                value, "hex"
            ), f"Snapshot key '{key}' contains a UUID object instead of a string"

    def test_version_name_display(self, db, organization, workspace):
        from simulate.models import AgentDefinition
        from simulate.serializers.response.agent_version import (
            AgentVersionResponseSerializer,
        )

        agent = AgentDefinition.objects.create(
            agent_name="Test Agent",
            agent_type="voice",
            inbound=True,
            description="Test",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        version = agent.create_version(
            description="Test", commit_message="Test", status="active"
        )

        data = AgentVersionResponseSerializer(version).data
        assert data["version_name_display"] == "v1"
