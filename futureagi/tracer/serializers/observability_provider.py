from rest_framework import serializers

from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices

# Providers whose api-key / assistant verification is actually implemented.
VERIFIABLE_PROVIDERS = [ProviderChoices.VAPI.value, ProviderChoices.RETELL.value]


class ObservabilityProviderSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(
        write_only=True,
        required=False,
        help_text="Name of the project. If it doesn't exist, it will be created.",
    )

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
        ]
        read_only_fields = ["organization", "workspace", "project"]

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
