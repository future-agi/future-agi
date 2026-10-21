from rest_framework import serializers

from simulate.models.agent_definition import (
    AgentDefinition,
    AgentDefinitionAuthenticationChoices,
    AgentTypeChoices,
)
from simulate.temporal.constants import DEFAULT_ORG_LIMIT


class AgentVersionCreateRequestSerializer(serializers.Serializer):
    """
    Request serializer for POST /agent-definitions/{id}/versions/create/.
    Validates both version creation fields and agent update fields.
    """

    # Agent update fields (all optional — only changed fields needed)
    agent_name = serializers.CharField(max_length=255, required=False)
    agent_type = serializers.ChoiceField(
        choices=AgentTypeChoices.choices, required=False
    )
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    provider = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    assistant_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    authentication_method = serializers.ChoiceField(
        choices=AgentDefinitionAuthenticationChoices.choices,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    language = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    languages = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
    )
    contact_number = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    inbound = serializers.BooleanField(required=False)
    target_speaks_first = serializers.BooleanField(required=False, allow_null=True)
    knowledge_base = serializers.UUIDField(required=False, allow_null=True)
    model = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    model_details = serializers.JSONField(required=False, allow_null=True)

    # LiveKit fields — routed to ProviderCredentials by the view, not to
    # AgentDefinition model columns.
    livekit_url = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )
    livekit_api_key = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    livekit_api_secret = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )
    livekit_agent_name = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    livekit_config_json = serializers.JSONField(required=False, allow_null=True)
    livekit_max_concurrency = serializers.IntegerField(
        required=False, min_value=1, max_value=DEFAULT_ORG_LIMIT
    )

    # Version-specific fields
    commit_message = serializers.CharField(required=False, allow_blank=True, default="")
    observability_enabled = serializers.BooleanField(required=False, default=False)

    # -- Field-level validators --

    def validate_agent_name(self, value):
        """Ensure agent_name is not empty or whitespace-only."""
        if value is not None and not value.strip():
            raise serializers.ValidationError("Agent name cannot be empty")
        return value

    def validate_language(self, value):
        """Ensure language is a valid choice."""
        if value:
            valid_languages = [
                choice[0] for choice in AgentDefinition.LanguageChoices.choices
            ]
            if value not in valid_languages:
                raise serializers.ValidationError(
                    f"Invalid language. Must be one of: {', '.join(valid_languages)}"
                )
        return value

    def validate_languages(self, value):
        """Ensure languages array items are valid."""
        if value:
            valid_languages = [
                choice[0] for choice in AgentDefinition.LanguageChoices.choices
            ]
            for lang in value:
                if lang not in valid_languages:
                    raise serializers.ValidationError(
                        f"Invalid language '{lang}'. Must be one of: "
                        f"{', '.join(valid_languages)}"
                    )
        return value
