import structlog
from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from agentcc.services.credential_manager import mask_key
from simulate.models import AgentVersion
from simulate.services.agent_definition import resolve_api_key_for_version
from simulate.services.agent_setup import agent_summary
from tracer.services.observability_switch import snapshot_as_shown

logger = structlog.get_logger(__name__)


class AgentVersionPreflightCheckSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True)
    label = serializers.CharField(allow_blank=True)
    status = serializers.CharField(allow_blank=True)
    missing = serializers.ListField(child=serializers.CharField())


class AgentVersionPreflightReportSerializer(serializers.Serializer):
    """What the check results show of a stored report: nothing else leaves the server."""

    checks = AgentVersionPreflightCheckSerializer(many=True)
    missing = serializers.ListField(child=serializers.CharField())
    invalid = serializers.ListField(child=serializers.CharField())
    error = serializers.CharField(allow_null=True)


def _preflight(context, version):
    """The check's (status, report) the view read for this version, else nulls.

    The fields sit on a side table, so the views read a page of them in one
    query and pass them in the context; a version with no row, or a serializer
    given no context, reports nothing rather than querying per row.
    """
    return (context.get("preflight") or {}).get(version.id, (None, None))


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
    agent_kind = serializers.SerializerMethodField()
    api_key = serializers.SerializerMethodField()
    preflight_status = serializers.SerializerMethodField()
    preflight_report = serializers.SerializerMethodField()

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
            "agent_kind",
            "api_key",
            "preflight_status",
            "preflight_report",
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
        snapshot = snapshot_as_shown(snapshot, instance.agent_definition)
        data["configuration_snapshot"] = snapshot
        try:
            creds = instance.credentials
            # The persisted snapshot omits secrets, so the edit form's LiveKit
            # key/secret fields render blank. Surface display-only masked
            # values here (never persisted) so the form shows a credential is
            # set; a masked value round-trips untouched via ``_apply_secrets``.
            if isinstance(snapshot, dict) and creds.provider_type == "livekit":
                snapshot = {**snapshot}
                snapshot["livekit_api_key"] = (
                    creds.get_masked_api_key() if creds.api_key else ""
                )
                snapshot["livekit_api_secret"] = creds.get_masked_api_secret()
                data["configuration_snapshot"] = snapshot
        except AgentVersion.credentials.RelatedObjectDoesNotExist:
            pass
        return data

    def get_version_name_display(self, obj):
        """Get formatted version name for display."""
        if obj.version_name:
            return obj.version_name
        return f"v{obj.version_number}"

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_agent_kind(self, obj) -> str | None:
        try:
            return agent_summary(obj.agent_definition_id).kind
        except Exception:
            logger.warning(
                "agent_kind_unavailable",
                agent_version_id=str(obj.id),
                exc_info=True,
            )
            return None

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_api_key(self, obj) -> str | None:
        api_key = resolve_api_key_for_version(obj)
        return mask_key(api_key) if api_key else None

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_preflight_status(self, obj) -> str | None:
        return _preflight(self.context, obj)[0]

    @swagger_serializer_method(
        serializer_or_field=AgentVersionPreflightReportSerializer(allow_null=True)
    )
    def get_preflight_report(self, obj) -> dict | None:
        # Imported here: the service module imports serializers of its own.
        from simulate.services.harness_agent_versions import public_preflight_report

        return public_preflight_report(_preflight(self.context, obj)[1])


class AgentVersionListResponseSerializer(serializers.ModelSerializer):
    """
    Response serializer for GET /agent-definitions/{id}/versions/ (list).
    Simplified fields for list view. All fields are read-only.
    """

    version_name_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    is_active = serializers.ReadOnlyField()
    is_latest = serializers.ReadOnlyField()
    preflight_status = serializers.SerializerMethodField()
    preflight_report = serializers.SerializerMethodField()

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
            "preflight_status",
            "preflight_report",
            "created_at",
        ]
        read_only_fields = fields

    def get_version_name_display(self, obj):
        """Get formatted version name for display."""
        if obj.version_name:
            return obj.version_name
        return f"v{obj.version_number}"

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_preflight_status(self, obj) -> str | None:
        return _preflight(self.context, obj)[0]

    @swagger_serializer_method(
        serializer_or_field=AgentVersionPreflightReportSerializer(allow_null=True)
    )
    def get_preflight_report(self, obj) -> dict | None:
        # Imported here: the service module imports serializers of its own.
        from simulate.services.harness_agent_versions import public_preflight_report

        return public_preflight_report(_preflight(self.context, obj)[1])


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
