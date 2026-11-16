from rest_framework import serializers

from integrations.models import IntegrationConnection, IntegrationPlatform
from integrations.services.credentials import CredentialManager


class ValidateCredentialsSerializer(serializers.Serializer):
    """Serializer for the credential validation endpoint.

    Supports two credential modes:
    - Legacy: public_key + secret_key (backwards compat)
    - Generic: credentials JSON dict
    """

    platform = serializers.ChoiceField(choices=IntegrationPlatform.choices)
    host_url = serializers.URLField(max_length=500, required=False, default="")
    public_key = serializers.CharField(max_length=500, required=False, default="")
    secret_key = serializers.CharField(max_length=500, required=False, default="")
    ca_certificate = serializers.CharField(required=False, allow_blank=True, default="")
    credentials = serializers.JSONField(required=False, default=dict)

    def validate_platform(self, value):
        if value == IntegrationPlatform.SLACK:
            raise serializers.ValidationError("Connect Slack through OAuth.")
        return value


class IntegrationConnectionCreateSerializer(serializers.Serializer):
    """Serializer for creating a new integration connection.

    Supports two credential modes:
    - Legacy: public_key + secret_key (backwards compat)
    - Generic: credentials JSON dict
    """

    platform = serializers.ChoiceField(choices=IntegrationPlatform.choices)
    host_url = serializers.URLField(max_length=500, required=False, default="")
    public_key = serializers.CharField(
        max_length=500, write_only=True, required=False, default=""
    )
    secret_key = serializers.CharField(
        max_length=500, write_only=True, required=False, default=""
    )
    ca_certificate = serializers.CharField(required=False, allow_blank=True, default="")
    credentials = serializers.JSONField(required=False, default=dict)

    # Project mapping
    project_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Existing FutureAGI project ID. If null, a new project is created.",
    )
    new_project_name = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Name for the new project (used when project_id is null).",
    )

    # Sync settings
    backfill_option = serializers.ChoiceField(
        choices=[("all", "All"), ("from_date", "From Date"), ("new_only", "New Only")],
        default="new_only",
    )
    backfill_from_date = serializers.DateTimeField(required=False, allow_null=True)
    backfill_to_date = serializers.DateTimeField(required=False, allow_null=True)
    sync_interval_seconds = serializers.IntegerField(
        default=300, min_value=60, max_value=1800
    )

    # Display name
    display_name = serializers.CharField(required=False, allow_blank=True, default="")

    # External project name (Langfuse project or sub-platform identifier)
    external_project_name = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

    # Platform-specific export configuration
    export_config = serializers.JSONField(required=False, default=dict)

    def validate_platform(self, value):
        if value == IntegrationPlatform.SLACK:
            raise serializers.ValidationError("Connect Slack through OAuth.")
        return value


class IntegrationConnectionUpdateSerializer(serializers.Serializer):
    """Serializer for updating an existing integration connection."""

    display_name = serializers.CharField(max_length=255, required=False)
    public_key = serializers.CharField(max_length=500, required=False, write_only=True)
    secret_key = serializers.CharField(max_length=500, required=False, write_only=True)
    host_url = serializers.URLField(max_length=500, required=False)
    ca_certificate = serializers.CharField(required=False, allow_blank=True)
    sync_interval_seconds = serializers.IntegerField(
        required=False, min_value=60, max_value=3600
    )


class IntegrationConnectionListSerializer(serializers.ModelSerializer):
    """Serializer for listing connections (summary view)."""

    class Meta:
        model = IntegrationConnection
        fields = [
            "id",
            "platform",
            "display_name",
            "host_url",
            "status",
            "status_message",
            "external_project_name",
            "last_synced_at",
            "total_traces_synced",
            "total_spans_synced",
            "total_scores_synced",
            "backfill_completed",
            "backfill_progress",
            "sync_interval_seconds",
            "created_at",
        ]


class IntegrationConnectionDetailSerializer(serializers.ModelSerializer):
    """Serializer for connection detail (includes masked credentials)."""

    public_key_display = serializers.SerializerMethodField()
    secret_key_display = serializers.SerializerMethodField()
    project_name = serializers.SerializerMethodField()

    class Meta:
        model = IntegrationConnection
        fields = [
            "id",
            "platform",
            "display_name",
            "host_url",
            "status",
            "status_message",
            "external_project_name",
            "project",
            "project_name",
            "public_key_display",
            "secret_key_display",
            "last_synced_at",
            "sync_cursor",
            "sync_interval_seconds",
            "last_error_notified_at",
            "backfill_from",
            "backfill_completed",
            "backfill_progress",
            "total_traces_synced",
            "total_spans_synced",
            "total_scores_synced",
            "created_at",
            "updated_at",
            "created_by",
        ]

    def get_public_key_display(self, obj):
        try:
            creds = self._get_decrypted_credentials(obj)
            return CredentialManager.mask_key(creds.get("public_key", ""))
        except Exception:
            return "****"

    def get_secret_key_display(self, obj):
        try:
            creds = self._get_decrypted_credentials(obj)
            return CredentialManager.mask_key(creds.get("secret_key", ""))
        except Exception:
            return "****"

    def get_project_name(self, obj):
        if obj.project:
            return obj.project.name
        return None

    def _get_decrypted_credentials(self, obj):
        """Decrypt credentials once and cache on the instance."""
        cache_attr = "_decrypted_credentials"
        if not hasattr(obj, cache_attr):
            setattr(
                obj,
                cache_attr,
                CredentialManager.decrypt(bytes(obj.encrypted_credentials)),
            )
        return getattr(obj, cache_attr)
