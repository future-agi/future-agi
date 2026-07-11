import re

from rest_framework import serializers

from simulate.models.agent_definition import (
    AgentDefinition,
    AgentDefinitionAuthenticationChoices,
    AgentTypeChoices,
)
from simulate.temporal.constants import DEFAULT_ORG_LIMIT
from tracer.models.observability_provider import ProviderChoices


class AgentDefinitionCreateRequestSerializer(serializers.Serializer):
    """
    Request serializer for POST /agent-definitions/create/.
    Validates all incoming fields for agent definition creation.
    """

    # Required fields
    agent_name = serializers.CharField(max_length=255, required=True)
    agent_type = serializers.ChoiceField(
        choices=AgentTypeChoices.choices,
        required=True,
        help_text="The type of agent. One of: voice, text.",
    )
    commit_message = serializers.CharField(required=True)
    inbound = serializers.BooleanField(required=False, default=True)

    # Optional fields
    description = serializers.CharField(required=False, allow_blank=True, default="")
    provider = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    api_key = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    assistant_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    authentication_method = serializers.ChoiceField(
        choices=AgentDefinitionAuthenticationChoices.choices,
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
    )
    language = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    languages = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
        default=None,
    )
    contact_number = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    knowledge_base = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )

    def to_internal_value(self, data):
        if isinstance(data, dict) and data.get("knowledge_base") == "":
            data = data.copy()
            data["knowledge_base"] = None
        return super().to_internal_value(data)

    observability_enabled = serializers.BooleanField(required=False, default=False)
    model = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    model_details = serializers.JSONField(required=False, allow_null=True, default=None)
    websocket_url = serializers.URLField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    websocket_headers = serializers.JSONField(
        required=False, allow_null=True, default=None
    )
    replay_session_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )

    # LiveKit fields — CharField + custom validator (not URLField) so
    # ws:// and wss:// schemes pass validation.
    livekit_url = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
        max_length=500,
    )
    livekit_api_key = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    livekit_api_secret = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    livekit_agent_name = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    livekit_config_json = serializers.JSONField(
        required=False, allow_null=True, default=None
    )
    livekit_max_concurrency = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )

    # -- Field-level validators --

    def validate_agent_name(self, value):
        """Ensure agent_name is not empty or whitespace-only."""
        if not value or not value.strip():
            raise serializers.ValidationError("Agent name is required")
        return value

    def validate_commit_message(self, value):
        """Ensure commit_message is not empty or whitespace-only."""
        if not value or not value.strip():
            raise serializers.ValidationError("Commit message is required")
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
        """Ensure languages array has at least one item and all are valid."""
        if value:
            if len(value) == 0:
                raise serializers.ValidationError("At least one language is required")
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

    def validate_websocket_headers(self, value):
        """Ensure websocket_headers is a valid dictionary."""
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError("websocket_headers must be a dictionary")
        return value

    # -- Cross-field validation --

    def validate(self, attrs):
        """Object-level validations that depend on multiple fields."""
        agent_type = attrs.get("agent_type")
        contact_number = attrs.get("contact_number")
        inbound = attrs.get("inbound", True)
        provider = attrs.get("provider")
        api_key = attrs.get("api_key")
        assistant_id = attrs.get("assistant_id")
        observability_enabled = attrs.get("observability_enabled", False)

        # Voice agents require provider
        if agent_type == AgentTypeChoices.VOICE:
            if not provider or not provider.strip():
                raise serializers.ValidationError(
                    {"provider": "Please select a provider"}
                )

            is_livekit = provider in ("livekit", "livekit_bridge")

            if is_livekit:
                # Validate livekit_max_concurrency cap
                max_conc = attrs.get("livekit_max_concurrency")
                if max_conc is not None and max_conc > DEFAULT_ORG_LIMIT:
                    raise serializers.ValidationError(
                        {
                            "livekit_max_concurrency": (
                                f"Cannot exceed the organization limit of {DEFAULT_ORG_LIMIT}"
                            )
                        }
                    )
            else:
                # Contact number is optional when api_key + assistant_id are
                # provided (web bridge will be used instead of SIP/phone).
                has_web_bridge_creds = bool(
                    api_key
                    and api_key.strip()
                    and assistant_id
                    and assistant_id.strip()
                )
                if (
                    not contact_number or not contact_number.strip()
                ) and not has_web_bridge_creds:
                    raise serializers.ValidationError(
                        {
                            "contact_number": (
                                "Contact number is required "
                                "(or provide API Key and Assistant ID for web bridge)"
                            )
                        }
                    )

                # Validate contact number format when provided.
                # Strip spaces so human-readable formats like "+1 5598887142"
                # are accepted (TH-5373).
                if contact_number and contact_number.strip():
                    cleaned = contact_number.lstrip("+").replace(" ", "")
                    if not re.match(r"^\d+$", cleaned):
                        raise serializers.ValidationError(
                            {
                                "contact_number": (
                                    "Contact number must contain only digits"
                                )
                            }
                        )
                    if len(cleaned) < 10 or len(cleaned) > 12:
                        raise serializers.ValidationError(
                            {
                                "contact_number": (
                                    "Contact number must be between 10 and 12 digits"
                                )
                            }
                        )

                # Authentication method requirements
                should_require_auth = provider != "others" and (
                    observability_enabled or not inbound
                )
                if should_require_auth:
                    authentication_method = attrs.get("authentication_method")
                    if not authentication_method or not authentication_method.strip():
                        raise serializers.ValidationError(
                            {
                                "authentication_method": (
                                    "Authentication method is required"
                                )
                            }
                        )
                    if authentication_method != "api_key":
                        raise serializers.ValidationError(
                            {"authentication_method": "Invalid authentication method"}
                        )

                # Outbound voice calls require api_key and assistant_id
                if not inbound:
                    if not api_key:
                        raise serializers.ValidationError(
                            {"api_key": "API key is required for outbound calls"}
                        )
                    if not assistant_id:
                        raise serializers.ValidationError(
                            {
                                "assistant_id": (
                                    "Assistant ID is required for outbound calls"
                                )
                            }
                        )

        # Observability enabled requires api_key and assistant_id
        # (only for non-"others" and non-livekit providers)
        if (
            observability_enabled
            and provider not in ("others", "livekit", "livekit_bridge")
            and inbound
        ):
            if not api_key:
                raise serializers.ValidationError(
                    {"api_key": "API key is required when observability is enabled"}
                )
            if not assistant_id:
                raise serializers.ValidationError(
                    {
                        "assistant_id": (
                            "Assistant ID is required when observability is enabled"
                        )
                    }
                )

        return attrs


class AgentDefinitionEditRequestSerializer(serializers.Serializer):
    """
    Request serializer for PUT /agent-definitions/{id}/edit/.
    All fields are optional to support partial updates.
    """

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
    knowledge_base = serializers.UUIDField(required=False, allow_null=True)
    model = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    model_details = serializers.JSONField(required=False, allow_null=True)
    websocket_url = serializers.URLField(
        required=False, allow_blank=True, allow_null=True
    )
    websocket_headers = serializers.JSONField(required=False, allow_null=True)

    # LiveKit fields — CharField (not URLField) so wss:// passes.
    livekit_url = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=500,
    )
    livekit_api_key = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    livekit_api_secret = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    livekit_agent_name = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    livekit_config_json = serializers.JSONField(required=False, allow_null=True)
    livekit_max_concurrency = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )

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

    def validate_websocket_headers(self, value):
        """Ensure websocket_headers is a valid dictionary."""
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError("websocket_headers must be a dictionary")
        return value

    def validate_livekit_max_concurrency(self, value):
        """Ensure livekit_max_concurrency does not exceed the org limit."""
        if value is not None and value > DEFAULT_ORG_LIMIT:
            raise serializers.ValidationError(
                f"Cannot exceed the organization limit of {DEFAULT_ORG_LIMIT}"
            )
        return value


class AgentDefinitionBulkDeleteRequestSerializer(serializers.Serializer):
    """
    Request serializer for DELETE /agent-definitions/ (bulk delete).
    Validates the agent_ids list.
    """

    agent_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
        min_length=1,
        help_text="List of agent definition UUIDs to delete.",
    )


class AgentDefinitionFilterSerializer(serializers.Serializer):
    """
    Serializer for validating GET /agent-definitions/ query parameters.
    """

    search = serializers.CharField(required=False, allow_blank=True, default="")
    agent_type = serializers.ChoiceField(
        choices=AgentTypeChoices.choices,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1)


class FetchAssistantRequestSerializer(serializers.Serializer):
    """
    Request serializer for POST fetch_assistant_from_provider.
    Validates api_key, assistant_id, and provider.
    """

    assistant_id = serializers.CharField(required=True)
    api_key = serializers.CharField(required=True)
    agent_id = serializers.UUIDField(required=False, allow_null=True)
    provider = serializers.ChoiceField(
        choices=[
            ProviderChoices.VAPI,
            ProviderChoices.RETELL,
            ProviderChoices.ELEVEN_LABS,
            ProviderChoices.OTHERS,
        ],
        default=ProviderChoices.VAPI,
        help_text="Voice provider. One of: vapi, retell, eleven_labs, others.",
    )
