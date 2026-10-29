import structlog
from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from agentcc.services.credential_manager import mask_key
from simulate.models import AgentVersion
from simulate.services.agent_definition import resolve_api_key_for_version
from simulate.services.agent_setup import agent_summary
from tracer.services.observability_switch import snapshot_as_shown

logger = structlog.get_logger(__name__)

# The non-secret facts a code version's source carries; anything else the
# runtime recorded (installation ids, access hints) stays server-side.
SOURCE_FACTS = ("repository", "ref", "commit_sha", "subfolder")


class AgentVersionSourceSerializer(serializers.Serializer):
    kind = serializers.CharField()
    repository = serializers.CharField(required=False)
    ref = serializers.CharField(required=False)
    commit_sha = serializers.CharField(required=False)
    subfolder = serializers.CharField(required=False)


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
    source = serializers.SerializerMethodField()

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
            "source",
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
        serializer_or_field=AgentVersionSourceSerializer(allow_null=True)
    )
    def get_source(self, obj) -> dict | None:
        """Where a versioned agent was read from; null for a version without a runtime."""
        try:
            runtime = obj.harness_runtime
        except AgentVersion.harness_runtime.RelatedObjectDoesNotExist:
            return None
        facts = runtime.source or {}
        return {
            "kind": runtime.source_kind,
            **{name: facts[name] for name in SOURCE_FACTS if facts.get(name)},
        }


class AgentVersionListResponseSerializer(serializers.ModelSerializer):
    """
    Response serializer for GET /agent-definitions/{id}/versions/ (list).
    Simplified fields for list view. All fields are read-only.
    """

    version_name_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    is_active = serializers.ReadOnlyField()
    is_latest = serializers.ReadOnlyField()
    in_production = serializers.ReadOnlyField(source="is_active")
    origin = serializers.SerializerMethodField()
    preflight_status = serializers.SerializerMethodField()
    environments_count = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()

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
            "in_production",
            "origin",
            "preflight_status",
            "environments_count",
            "source",
            "created_at",
        ]
        read_only_fields = fields

    def get_version_name_display(self, obj):
        """Get formatted version name for display."""
        if obj.version_name:
            return obj.version_name
        return f"v{obj.version_number}"

    def _runtime(self, obj):
        return (self.context.get("runtimes") or {}).get(obj.id)

    def get_origin(self, obj) -> str | None:
        runtime = self._runtime(obj)
        return (runtime.trigger or None) if runtime is not None else None

    def get_preflight_status(self, obj) -> str | None:
        runtime = self._runtime(obj)
        return runtime.preflight_status if runtime is not None else None

    def get_environments_count(self, obj) -> int:
        return int((self.context.get("environments_count") or {}).get(obj.id, 0))

    @swagger_serializer_method(serializer_or_field=AgentVersionSourceSerializer)
    def get_source(self, obj) -> dict | None:
        runtime = self._runtime(obj)
        if runtime is None or not runtime.source_kind:
            return None
        facts = {
            name: value
            for name, value in (runtime.source or {}).items()
            if name in SOURCE_FACTS and value
        }
        return {"kind": str(runtime.source_kind), **facts}


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


class AgentVersionRestoreResponseSerializer(serializers.Serializer):
    """
    Response serializer for POST /agent-definitions/{id}/versions/{id}/restore/.
    Shape: {"message": "...", "agent": {...}, "version": {...}}
    """

    message = serializers.CharField(read_only=True)
    agent = serializers.DictField(read_only=True)
    version = AgentVersionResponseSerializer(read_only=True)


class AgentVersionChangeResponseSerializer(serializers.Serializer):
    """The version a change made, or the one already standing for that content."""

    version = AgentVersionResponseSerializer(read_only=True)
    created = serializers.BooleanField(read_only=True)
    up_to_date = serializers.BooleanField(read_only=True)


class AgentVersionProductionResponseSerializer(serializers.Serializer):
    version = AgentVersionResponseSerializer(read_only=True)
    moved_environment_ids = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )


class AgentVersionActionResponseSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)


class AgentVersionToolChangeSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    before = serializers.DictField(read_only=True)
    after = serializers.DictField(read_only=True)


class AgentVersionCompatibilityResponseSerializer(serializers.Serializer):
    """How a version fits the world an environment was built from."""

    environment_id = serializers.CharField(read_only=True)
    version_id = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(
        choices=(
            "clean",
            "new_tool",
            "tool_removed_or_changed",
            "new_rule",
            "unmeasured",
        ),
        read_only=True,
    )
    base = serializers.CharField(read_only=True, allow_null=True)
    reason = serializers.CharField(read_only=True, required=False)
    same_version = serializers.BooleanField(read_only=True, required=False)
    tools_added = serializers.ListField(
        child=serializers.CharField(), read_only=True, required=False
    )
    tools_removed = serializers.ListField(
        child=serializers.CharField(), read_only=True, required=False
    )
    tools_changed = AgentVersionToolChangeSerializer(
        many=True, read_only=True, required=False
    )
    services_added = serializers.ListField(
        child=serializers.CharField(), read_only=True, required=False
    )
    rules_added = serializers.ListField(
        child=serializers.CharField(), read_only=True, required=False
    )
    prompt_changed = serializers.BooleanField(read_only=True, required=False)
    model_changed = serializers.BooleanField(read_only=True, required=False)


class AgentBuildPreflightCheckSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(choices=("ok", "blocked"), read_only=True)
    message = serializers.CharField(read_only=True)


class AgentBuildPreflightResponseSerializer(serializers.Serializer):
    """Whether an environment can be built from the agent, and from which version."""

    ok = serializers.BooleanField(read_only=True)
    version_id = serializers.CharField(read_only=True, allow_null=True)
    checks = AgentBuildPreflightCheckSerializer(many=True, read_only=True)


class AgentCodeEntrySerializer(serializers.Serializer):
    path = serializers.CharField(read_only=True)
    size = serializers.IntegerField(read_only=True)
    kind = serializers.ChoiceField(
        choices=("text", "binary", "too_large"), read_only=True
    )


class AgentCodeTreeResponseSerializer(serializers.Serializer):
    """Every file of a version's code, relative to the archive root."""

    subfolder = serializers.CharField(read_only=True, allow_blank=True)
    files = AgentCodeEntrySerializer(many=True, read_only=True)
    truncated = serializers.BooleanField(read_only=True)


class AgentCodeFileResponseSerializer(serializers.Serializer):
    """One file of a version's code; content only for text under the cap."""

    path = serializers.CharField(read_only=True)
    size = serializers.IntegerField(read_only=True)
    kind = serializers.ChoiceField(
        choices=("text", "binary", "too_large"), read_only=True
    )
    language = serializers.CharField(read_only=True)
    content = serializers.CharField(read_only=True, required=False, allow_blank=True)


class AgentCodeChangeSerializer(serializers.Serializer):
    path = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(
        choices=("added", "removed", "modified"), read_only=True
    )


class AgentCodeChangesResponseSerializer(serializers.Serializer):
    """The files that changed between two versions, by content."""

    against = serializers.CharField(read_only=True, allow_null=True)
    files = AgentCodeChangeSerializer(many=True, read_only=True)
    unavailable = serializers.CharField(read_only=True, required=False)
