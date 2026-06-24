from rest_framework import serializers

from agentcc.services.credential_manager import mask_key
from simulate.models import AgentVersion

# Provider-secret fields that may live in an AgentVersion.configuration_snapshot
# and must never be returned to API clients in cleartext (they mirror the
# credentials encrypted in ProviderCredentials). "Keys" get a partial mask
# (first/last 4) so the UI can show *which* credential is set; the raw secret is
# fully hidden. Keep in sync with the snapshot schema.
_SNAPSHOT_MASKED_KEYS = ("api_key", "livekit_api_key")
_SNAPSHOT_HIDDEN_SECRETS = ("livekit_api_secret",)


def mask_snapshot_secrets(snapshot):
    """Return a shallow copy of an agent configuration_snapshot with every
    provider secret masked.

    Safe on any input: a non-dict is returned unchanged. Use this everywhere a
    configuration_snapshot is embedded in an API response so plaintext provider
    secrets never reach clients, logs, or browser history.
    """
    if not isinstance(snapshot, dict):
        return snapshot
    masked = dict(snapshot)
    for field in _SNAPSHOT_MASKED_KEYS:
        if masked.get(field):
            masked[field] = mask_key(masked[field])
    for field in _SNAPSHOT_HIDDEN_SECRETS:
        if masked.get(field):
            masked[field] = "********"
    return masked


class AgentVersionResponseSerializer(serializers.ModelSerializer):
    """
    Response serializer for AgentVersion detail endpoints.
    Used by: create, activate, restore, detail responses.
    All fields are read-only — this is a pure output contract.
    """

    is_active = serializers.ReadOnlyField()
    is_latest = serializers.ReadOnlyField()
    version_name_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = AgentVersion
        fields = [
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
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        """Ensure configuration_snapshot is JSON-safe by stringifying UUIDs."""
        data = super().to_representation(instance)
        snapshot = data.get("configuration_snapshot")
        if isinstance(snapshot, dict):
            for key in ["organization", "knowledge_base", "workspace", "id"]:
                if key in snapshot and snapshot[key] is not None:
                    snapshot[key] = str(snapshot[key])
            # Mask every provider secret so the frontend knows a credential is
            # set but can't read it. livekit_api_key was previously left in
            # cleartext — mask_snapshot_secrets closes that gap.
            snapshot = mask_snapshot_secrets(snapshot)
        data["configuration_snapshot"] = snapshot
        return data

    def get_version_name_display(self, obj):
        """Get formatted version name for display."""
        if obj.version_name:
            return obj.version_name
        return f"v{obj.version_number}"


class AgentVersionListResponseSerializer(serializers.ModelSerializer):
    """
    Response serializer for GET /agent-definitions/{id}/versions/ (list).
    Simplified fields for list view. All fields are read-only.
    """

    version_name_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    is_active = serializers.ReadOnlyField()
    is_latest = serializers.ReadOnlyField()

    class Meta:
        model = AgentVersion
        fields = [
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
            "is_active",
            "is_latest",
            "created_at",
        ]
        read_only_fields = fields

    def get_version_name_display(self, obj):
        """Get formatted version name for display."""
        if obj.version_name:
            return obj.version_name
        return f"v{obj.version_number}"


class AgentVersionCreateResponseSerializer(serializers.Serializer):
    """
    Response serializer for POST /agent-definitions/{id}/versions/create/.
    Shape: {"message": "...", "version": {...}}
    """

    message = serializers.CharField(read_only=True)
    version = AgentVersionResponseSerializer(read_only=True)


class AgentVersionActivateResponseSerializer(serializers.Serializer):
    """
    Response serializer for POST /agent-definitions/{id}/versions/{id}/activate/.
    Shape: {"message": "...", "version": {...}}
    """

    message = serializers.CharField(read_only=True)
    version = AgentVersionResponseSerializer(read_only=True)


class AgentVersionDeleteResponseSerializer(serializers.Serializer):
    """
    Response serializer for DELETE /agent-definitions/{id}/versions/{id}/delete/.
    Shape: {"message": "..."}
    """

    message = serializers.CharField(read_only=True)


class AgentVersionRestoreResponseSerializer(serializers.Serializer):
    """
    Response serializer for POST /agent-definitions/{id}/versions/{id}/restore/.
    Shape: {"message": "...", "agent": {...}, "version": {...}}
    """

    message = serializers.CharField(read_only=True)
    agent = serializers.DictField(read_only=True)
    version = AgentVersionResponseSerializer(read_only=True)
