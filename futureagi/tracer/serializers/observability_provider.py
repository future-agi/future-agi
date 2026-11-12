from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices

# Providers whose api-key / assistant verification is actually implemented.
VERIFIABLE_PROVIDERS = [
    ProviderChoices.VAPI.value,
    ProviderChoices.RETELL.value,
    ProviderChoices.BLAND.value,
]

# poll_state key the scheduled fetch sets when the provider rejects the key
# (LAST_FETCH_ERROR_KEY in tracer/utils/observability_provider.py, which
# imports this module, so the name is repeated here rather than imported).
LAST_FETCH_ERROR_KEY = "last_fetch_error"


class ObservabilityProviderFetchErrorSerializer(serializers.Serializer):
    """The provider's last refused fetch: HTTP status and when it happened."""

    status_code = serializers.IntegerField()
    at = serializers.DateTimeField()


class ObservabilityProviderSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(
        write_only=True,
        required=False,
        help_text="Name of the project. If it doesn't exist, it will be created.",
    )
    # OBS-SW-110: lets the empty call list say the provider rejected the key.
    # poll_state itself (Retell cursors and backoff) is never exposed.
    last_fetch_error = serializers.SerializerMethodField()

    class Meta:
        model = ObservabilityProvider
        fields = [
            "id",
            "project",
            "project_name",
            "provider",
            "enabled",
            "organization",
            "workspace",
            "metadata",
            "created_at",
            "updated_at",
            "last_fetch_error",
        ]
        read_only_fields = ["organization", "workspace", "project"]

    @swagger_serializer_method(
        serializer_or_field=ObservabilityProviderFetchErrorSerializer(allow_null=True)
    )
    def get_last_fetch_error(self, obj) -> dict | None:
        state = getattr(obj, "poll_state", None)
        error = state.get(LAST_FETCH_ERROR_KEY) if isinstance(state, dict) else None
        if not isinstance(error, dict):
            return None
        return {"status_code": error.get("status_code"), "at": error.get("at")}

    def create(self, validated_data):
        """
        Custom create method to handle the `project_name` field.
        """
        validated_data.pop("project_name", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """
        Custom update method to prevent changing the project.
        """
        if "project_name" in self.initial_data or "project" in self.initial_data:
            raise serializers.ValidationError(
                {"project": "Project cannot be changed after creation."}
            )
        return super().update(instance, validated_data)


class VerifyApiKeyRequestSerializer(serializers.Serializer):
    """verify_api_key body. provider is constrained to the providers the verify
    logic actually supports, so the contract advertises only those (not the full
    enum) and an unsupported provider is rejected at validation."""

    provider = serializers.ChoiceField(choices=VERIFIABLE_PROVIDERS)
    api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    agent_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class VerifyAssistantIdRequestSerializer(serializers.Serializer):
    """verify_assistant_id body; provider constrained to the verifiable set."""

    provider = serializers.ChoiceField(choices=VERIFIABLE_PROVIDERS)
    assistant_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    agent_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class VerifyResponseSerializer(serializers.Serializer):
    """verify_* success envelope: a status flag and a human-readable message."""

    status = serializers.BooleanField(default=True)
    result = serializers.CharField()
