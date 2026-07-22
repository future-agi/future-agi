from rest_framework import serializers

from simulate.models import AgentVersion


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
        data["configuration_snapshot"] = snapshot
        try:
            creds = instance.credentials
            data["api_key"] = creds.get_masked_api_key()
        except AgentVersion.credentials.RelatedObjectDoesNotExist:
            pass
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
