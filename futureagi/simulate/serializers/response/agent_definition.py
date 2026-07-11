from django.conf import settings
from rest_framework import serializers

from agentcc.services.credential_manager import mask_key
from simulate.models import AgentDefinition, AgentVersion

MASKED_CREDENTIAL_VALUE = "********"


class AgentDefinitionResponseSerializer(serializers.ModelSerializer):
    """An agent definition is the durable record of a deployed or
    in-development conversational agent (voice or text).

    Definitions carry the agent's identity (name, description, provider,
    model), routing config (phone, assistant id, voice/transport stack),
    and live LiveKit credentials when applicable. Versions of a definition
    track config changes over time. Use list_agents to discover agent IDs,
    list_agent_versions to see version history. Use this output contract
    for create, edit, restore, and detail responses — all fields are
    read-only on the output side.
    """

    livekit_url = serializers.SerializerMethodField()
    livekit_api_key = serializers.SerializerMethodField()
    livekit_agent_name = serializers.SerializerMethodField()
    livekit_config_json = serializers.SerializerMethodField()
    livekit_max_concurrency = serializers.SerializerMethodField()
    api_key = serializers.SerializerMethodField()

    class Meta:
        model = AgentDefinition
        fields = [
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
        ]
        read_only_fields = fields

    @staticmethod
    def _get_latest_creds(obj):
        """Get ProviderCredentials from the active or latest version."""
        version = obj.active_version or obj.latest_version
        if not version:
            return None
        try:
            return version.credentials
        except AgentVersion.credentials.RelatedObjectDoesNotExist:
            pass
        # Active version may exist without credentials (e.g. legacy data
        # with multiple active versions). Fall back to latest version.
        fallback = obj.latest_version
        if fallback and fallback.pk != version.pk:
            try:
                return fallback.credentials
            except AgentVersion.credentials.RelatedObjectDoesNotExist:
                pass
        return None

    @staticmethod
    def _get_livekit_creds(obj):
        creds = AgentDefinitionResponseSerializer._get_latest_creds(obj)
        if not creds or creds.provider_type != "livekit":
            return None
        return creds

    def get_livekit_url(self, obj):
        creds = self._get_livekit_creds(obj)
        return creds.server_url if creds else ""

    def get_livekit_api_key(self, obj):
        creds = self._get_livekit_creds(obj)
        return self._get_masked_api_key(creds)

    def get_livekit_agent_name(self, obj):
        creds = self._get_livekit_creds(obj)
        return creds.agent_name if creds else ""

    def get_livekit_config_json(self, obj):
        creds = self._get_livekit_creds(obj)
        return creds.config_json if creds else None

    def get_livekit_max_concurrency(self, obj):
        creds = self._get_livekit_creds(obj)
        return (
            creds.max_concurrency if creds else settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
        )

    def get_api_key(self, obj):
        creds = self._get_latest_creds(obj)
        if creds and creds.provider_type != "livekit":
            return self._get_masked_api_key(creds)
        if creds and creds.provider_type == "livekit":
            return ""
        return mask_key(obj.api_key or "")

    @staticmethod
    def _get_masked_api_key(creds):
        if not creds or not creds.api_key:
            return ""
        try:
            return creds.get_masked_api_key()
        except ValueError:
            return MASKED_CREDENTIAL_VALUE


class AgentDefinitionCreateResponseSerializer(serializers.Serializer):
    """
    Response serializer for POST /agent-definitions/create/.
    Shape: {"message": "...", "agent": {...}}
    """

    message = serializers.CharField(read_only=True)
    agent = AgentDefinitionResponseSerializer(read_only=True)


class AgentDefinitionEditResponseSerializer(serializers.Serializer):
    """
    Response serializer for PUT /agent-definitions/{id}/edit/.
    Shape: {"message": "...", "agent": {...}}
    """

    message = serializers.CharField(read_only=True)
    agent = AgentDefinitionResponseSerializer(read_only=True)


class AgentDefinitionListResponseSerializer(serializers.ModelSerializer):
    """
    Response serializer for GET /agent-definitions/ (list).
    Includes latest_version and latest_version_id computed fields.
    All fields are read-only.
    """

    latest_version = serializers.SerializerMethodField()
    latest_version_id = serializers.SerializerMethodField()

    class Meta:
        model = AgentDefinition
        fields = [
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
            "websocket_url",
            "websocket_headers",
            "workspace",
            "knowledge_base",
            "organization",
            "created_at",
            "updated_at",
            "latest_version",
            "latest_version_id",
            "model_details",
            "model",
        ]
        read_only_fields = fields

    def get_latest_version(self, obj):
        """Get the latest version number for the agent."""
        if hasattr(obj, "_latest_version"):
            return obj._latest_version
        version = obj.latest_version
        return version.version_number if version else None

    def get_latest_version_id(self, obj):
        """Get the latest version id for the agent."""
        if hasattr(obj, "_latest_version_id"):
            return obj._latest_version_id
        version = obj.latest_version
        return version.id if version else None


class AgentDefinitionDetailResponseSerializer(serializers.Serializer):
    """
    Response serializer for GET /agent-definitions/{id}/.
    Shape: {**agent_data, "versions": [...], "active_version": {...}, "version_count": N}
    """

    # Agent fields are spread at top level via to_representation
    versions = serializers.ListField(read_only=True)
    active_version = serializers.DictField(read_only=True, allow_null=True)
    version_count = serializers.IntegerField(read_only=True)


class AgentDefinitionBulkDeleteResponseSerializer(serializers.Serializer):
    """
    Response serializer for DELETE /agent-definitions/ (bulk delete).
    Shape: {"message": "...", "agents_updated": N, "versions_updated": N}
    """

    message = serializers.CharField(read_only=True)
    agents_updated = serializers.IntegerField(read_only=True)
    versions_updated = serializers.IntegerField(read_only=True)


class AgentDefinitionDeleteResponseSerializer(serializers.Serializer):
    """
    Response serializer for DELETE /agent-definitions/{id}/delete/.
    Shape: {"message": "..."}
    """

    message = serializers.CharField(read_only=True)


class FetchAssistantResponseSerializer(serializers.Serializer):
    """
    Response serializer for POST fetch_assistant_from_provider.
    Inner shape (wrapped by _gm.success_response):
    {name, assistant_id, prompt, provider, commit_message}
    """

    name = serializers.CharField(read_only=True, allow_null=True)
    assistant_id = serializers.CharField(read_only=True)
    prompt = serializers.CharField(read_only=True, allow_null=True)
    provider = serializers.CharField(read_only=True)
    commit_message = serializers.CharField(read_only=True, allow_null=True)
