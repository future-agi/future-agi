import re

from django.conf import settings
from django.core.validators import URLValidator
from django.db import transaction
from rest_framework import serializers

from model_hub.models.develop_dataset import KnowledgeBaseFile
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import AgentTypeChoices
from simulate.services.agent_definition import is_masked, sync_provider_credentials
from simulate.services.types.agent_definition import ProviderCredentialsInput
from simulate.temporal.constants import DEFAULT_ORG_LIMIT
from tracer.models.observability_provider import ProviderChoices

# LiveKit URLs are valid in either WebSocket form (wss://, ws://) or HTTP
# form (https://, http://). The frontend stores whatever the user typed
# and the backend converts the scheme at use-time in three places:
# ValidateLiveKitCredentialsView, simulate.services.livekit.config, and
# LiveKitBridgeConnector._http_url. So the validator just needs to accept
# all four schemes.
_LIVEKIT_URL_VALIDATOR = URLValidator(schemes=["http", "https", "ws", "wss"])


def _extract_credentials_input(
    validated_data: dict, fallback_provider: str
) -> ProviderCredentialsInput:
    """Pop the write-only livekit_* fields out of ``validated_data`` and
    return a :class:`ProviderCredentialsInput`.

    Call this **before** ``super().create()``/``update()`` — the livekit
    fields must be removed from ``validated_data`` so ``ModelSerializer``
    doesn't try to write them to non-existent columns on
    ``AgentDefinition``. ``api_key`` and ``assistant_id`` stay in place
    (they're real model columns) and are copied into the input model by
    read-only lookup.
    """
    return ProviderCredentialsInput(
        provider=validated_data.get("provider") or fallback_provider,
        api_key=validated_data.get("api_key"),
        assistant_id=validated_data.get("assistant_id"),
        livekit_url=validated_data.pop("livekit_url", None),
        livekit_api_key=validated_data.pop("livekit_api_key", None),
        livekit_api_secret=validated_data.pop("livekit_api_secret", None),
        livekit_agent_name=validated_data.pop("livekit_agent_name", None),
        livekit_config_json=validated_data.pop("livekit_config_json", None),
        livekit_max_concurrency=validated_data.pop("livekit_max_concurrency", None),
        provider_was_provided="provider" in validated_data,
    )


class AgentDefinitionOperationSerializer(serializers.Serializer):
    """Serializer for operations on agent definition apart from CRUD"""

    assistant_id = serializers.CharField()
    api_key = serializers.CharField()
    provider = serializers.ChoiceField(
        choices=[
            ProviderChoices.VAPI,
            ProviderChoices.RETELL,
            ProviderChoices.ELEVEN_LABS,
            ProviderChoices.BLAND,
            ProviderChoices.OTHERS,
        ],
        default=ProviderChoices.VAPI,
    )
    name = serializers.CharField(required=False, allow_null=True)
    prompt = serializers.CharField(required=False, allow_null=True)
    commit_message = serializers.CharField(required=False, allow_null=True)


class AgentDefinitionSerializer(serializers.ModelSerializer):
    """Serializer for AgentDefinition model"""

    # LiveKit fields are write-only — validated by DRF but routed to the
    # ProviderCredentials table by create()/update() instead of being
    # written to AgentDefinition columns (they don't exist on the model).
    # `livekit_url` is a CharField (not URLField) because URLField rejects
    # ws:// and wss:// schemes — LiveKit Cloud surfaces wss:// URLs and
    # users naturally paste them in. We accept all four schemes via a
    # custom URLValidator and the backend converts at use-time.
    livekit_url = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=500,
        validators=[_LIVEKIT_URL_VALIDATOR],
    )
    livekit_api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255
    )
    livekit_api_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )

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
            "livekit_api_secret",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "organization",
            "workspace",
            "observability_provider",
        ]

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown_fields = sorted(set(data) - set(self.fields))
            if unknown_fields:
                raise serializers.ValidationError(
                    {field: ["Unknown field."] for field in unknown_fields}
                )
        return super().to_internal_value(data)

    def to_representation(self, instance):
        """Read credentials from the latest version's ProviderCredentials.

        Serialized ``api_key`` / ``assistant_id`` describe the VAPI/Retell
        credentials and are populated from ``ProviderCredentials`` only when
        the agent's provider actually uses them. For LiveKit agents the
        credentials live under the ``livekit_*`` keys instead, so the
        generic fields are left blank to avoid leaking the masked LiveKit
        key into a VAPI-shaped field.
        """
        data = super().to_representation(instance)
        version = instance.active_version or instance.latest_version
        creds = None
        if version:
            try:
                creds = version.credentials
            except AgentVersion.credentials.RelatedObjectDoesNotExist:
                pass

        if creds:
            if creds.provider_type == "livekit":
                data["livekit_url"] = creds.server_url
                data["livekit_api_key"] = creds.get_masked_api_key()
                data["livekit_agent_name"] = creds.agent_name
                data["livekit_config_json"] = creds.config_json or {}
                data["livekit_max_concurrency"] = (
                    creds.max_concurrency or settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
                )
                data["api_key"] = ""
                data["assistant_id"] = ""
            else:
                data["api_key"] = creds.get_masked_api_key()
                data["assistant_id"] = creds.assistant_id or data.get(
                    "assistant_id", ""
                )
        data.pop("livekit_api_secret", None)
        return data

    def validate_agent_name(self, value):
        """Ensure agent_name is not empty or whitespace-only"""
        if not value or not value.strip():
            raise serializers.ValidationError("Agent name is required")
        return value

    def validate_language(self, value):
        """Ensure language is a valid choice"""
        valid_languages = [
            choice[0] for choice in AgentDefinition.LanguageChoices.choices
        ]
        if value not in valid_languages:
            raise serializers.ValidationError(
                f"Invalid language. Must be one of: {', '.join(valid_languages)}"
            )
        return value

    def validate_languages(self, value):
        """Ensure languages array has at least one item and all are valid"""
        if not value or len(value) == 0:
            raise serializers.ValidationError("At least one language is required")
        valid_languages = [
            choice[0] for choice in AgentDefinition.LanguageChoices.choices
        ]
        for lang in value:
            if lang not in valid_languages:
                raise serializers.ValidationError(
                    f"Invalid language '{lang}'. Must be one of: {', '.join(valid_languages)}"
                )
        return value

    def validate_websocket_headers(self, value):
        """Ensure websocket_headers is a valid dictionary"""
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError("websocket_headers must be a dictionary")
        return value

    def validate_inbound(self, value):
        if value:  # inbound True → no extra checks
            return value

        # outbound: require api_key and assistant_id from incoming data or existing instance
        api_key = (self.initial_data or {}).get("api_key") or getattr(
            self.instance, "api_key", None
        )
        assistant_id = (self.initial_data or {}).get("assistant_id") or getattr(
            self.instance, "assistant_id", None
        )

        if not api_key:
            raise serializers.ValidationError("API key is required for outbound calls")
        if not assistant_id:
            raise serializers.ValidationError(
                "Assistant ID is required for outbound calls"
            )
        return value

    def validate(self, attrs):
        """Object-level validations that depend on multiple fields"""
        # Use incoming data with fallback to existing instance for partial updates
        agent_type = attrs.get("agent_type", getattr(self.instance, "agent_type", None))
        contact_number = attrs.get(
            "contact_number", getattr(self.instance, "contact_number", None)
        )
        inbound = attrs.get("inbound", getattr(self.instance, "inbound", True))
        provider = attrs.get("provider", getattr(self.instance, "provider", None))
        # Determine observability_enabled in a way that works for both create
        # (client-supplied boolean) and update (derive from existing provider).
        observability_enabled_raw = (self.initial_data or {}).get(
            "observability_enabled", None
        )
        if observability_enabled_raw is not None:
            observability_enabled_effective = bool(observability_enabled_raw)
        else:
            obs_provider = getattr(self.instance, "observability_provider", None)
            observability_enabled_effective = bool(
                getattr(obs_provider, "enabled", False)
            )

        # Voice agents: match UI requirements for creation
        if agent_type == AgentTypeChoices.VOICE:
            if not provider or not provider.strip():
                raise serializers.ValidationError(
                    {"provider": "Please select a provider"}
                )

            # LiveKit provider uses direct WebRTC — no phone number or
            # API key/assistant ID needed.
            is_livekit = provider in ("livekit", "livekit_bridge")

            if is_livekit:
                max_conc = attrs.get(
                    "livekit_max_concurrency",
                    getattr(
                        self.instance,
                        "livekit_max_concurrency",
                        settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY,
                    ),
                )
                if max_conc is not None:
                    if max_conc < 1:
                        raise serializers.ValidationError(
                            {"livekit_max_concurrency": "Must be at least 1"}
                        )
                    # Cap at org-level limit
                    if max_conc > DEFAULT_ORG_LIMIT:
                        raise serializers.ValidationError(
                            {
                                "livekit_max_concurrency": f"Cannot exceed the organization limit of {DEFAULT_ORG_LIMIT}"
                            }
                        )

            if not is_livekit:
                # Contact number is optional when API key + assistant ID are
                # provided (web bridge will be used instead of SIP/phone).
                api_key = attrs.get("api_key", getattr(self.instance, "api_key", None))
                assistant_id = attrs.get(
                    "assistant_id", getattr(self.instance, "assistant_id", None)
                )
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
                            "contact_number": "Contact number is required (or provide API Key and Assistant ID for web bridge)"
                        }
                    )

            if not is_livekit:
                # If contact_number is provided, enforce format/length.
                if contact_number and contact_number.strip():
                    cleaned = contact_number.lstrip("+")
                    if not re.match(r"^\d+$", cleaned):
                        raise serializers.ValidationError(
                            {
                                "contact_number": "Contact number must contain only digits"
                            }
                        )
                    if len(cleaned) < 10 or len(cleaned) > 12:
                        raise serializers.ValidationError(
                            {
                                "contact_number": "Contact number must be between 10 and 12 digits"
                            }
                        )

                # When provider is not "others", UI requires authentication_method in two cases:
                # - observability_enabled=true (inbound voice)
                # - inbound=false (outbound voice)
                should_require_auth = provider != "others" and (
                    observability_enabled_effective or not inbound
                )
                if should_require_auth:
                    authentication_method = attrs.get(
                        "authentication_method",
                        getattr(self.instance, "authentication_method", None),
                    )
                    if not authentication_method or not authentication_method.strip():
                        raise serializers.ValidationError(
                            {
                                "authentication_method": "Authentication method is required"
                            }
                        )
                    if authentication_method != "api_key":
                        raise serializers.ValidationError(
                            {"authentication_method": "Invalid authentication method"}
                        )

                # Outbound voice calls require api_key and assistant_id
                if not inbound:
                    api_key = attrs.get(
                        "api_key", getattr(self.instance, "api_key", None)
                    )
                    assistant_id = attrs.get(
                        "assistant_id",
                        getattr(self.instance, "assistant_id", None),
                    )
                    if not api_key:
                        raise serializers.ValidationError(
                            {"api_key": "API key is required for outbound calls"}
                        )
                    if not assistant_id:
                        raise serializers.ValidationError(
                            {
                                "assistant_id": "Assistant ID is required for outbound calls"
                            }
                        )

        # Observability enabled requires api_key and assistant_id
        # (only for non-"others" and non-"livekit" providers)
        if (
            observability_enabled_effective
            and provider not in ("others", "livekit", "livekit_bridge")
            and inbound
        ):
            api_key = attrs.get("api_key", getattr(self.instance, "api_key", None))
            assistant_id = attrs.get(
                "assistant_id", getattr(self.instance, "assistant_id", None)
            )
            if not api_key:
                raise serializers.ValidationError(
                    {"api_key": "API key is required when observability is enabled"}
                )
            if not assistant_id:
                raise serializers.ValidationError(
                    {
                        "assistant_id": "Assistant ID is required when observability is enabled"
                    }
                )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        creds_input = _extract_credentials_input(validated_data, fallback_provider="")
        if is_masked(validated_data.get("api_key") or ""):
            validated_data.pop("api_key", None)

        instance = super().create(validated_data)

        creds_input.provider = instance.provider or creds_input.provider
        version = instance.latest_version
        if not version:
            version = instance.create_version(
                description="",
                commit_message="Initial version",
                status="draft",
            )
        sync_provider_credentials(version, creds_input)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = AgentDefinition.objects.select_for_update(of=("self",)).get(
            pk=instance.pk
        )

        creds_input = _extract_credentials_input(
            validated_data, fallback_provider=instance.provider or ""
        )

        new_api_key = validated_data.get("api_key")
        if new_api_key is not None and is_masked(new_api_key):
            validated_data.pop("api_key")

        instance = super().update(instance, validated_data)

        creds_input.provider = instance.provider or creds_input.provider
        version = instance.active_version or instance.latest_version
        if version:
            sync_provider_credentials(version, creds_input)
        return instance


class AgentDefinitionListSerializer(serializers.ModelSerializer):
    """Serializer for listing AgentDefinitions with latest version"""

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
        """Get the latest version number for the agent"""
        if hasattr(obj, "_latest_version"):
            return obj._latest_version
        version = obj.latest_version
        return version.version_number if version else None

    def get_latest_version_id(self, obj):
        """Get the latest version id for the agent"""
        if hasattr(obj, "_latest_version_id"):
            return obj._latest_version_id
        version = obj.latest_version
        return version.id if version else None


class AgentDefinitionUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating AgentDefinition model from version creation"""

    knowledge_base = serializers.PrimaryKeyRelatedField(
        queryset=KnowledgeBaseFile.objects.none(),
        required=False,
        allow_null=True,
        default=None,
        many=False,
    )

    # LiveKit fields are write-only (see AgentDefinitionSerializer for the
    # same pattern). Routed to ProviderCredentials via
    # ``AgentDefinitionSerializer._sync_provider_credentials`` from
    # ``update()`` below. CharField + custom validator (not URLField) so
    # ws:// and wss:// schemes pass validation.
    livekit_url = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=500,
        validators=[_LIVEKIT_URL_VALIDATOR],
    )
    livekit_api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255
    )
    livekit_api_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    livekit_agent_name = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255
    )
    livekit_config_json = serializers.JSONField(
        write_only=True, required=False, allow_null=True
    )
    livekit_max_concurrency = serializers.IntegerField(
        write_only=True, required=False, min_value=1, max_value=DEFAULT_ORG_LIMIT
    )

    class Meta:
        model = AgentDefinition
        fields = [
            "agent_name",
            "language",
            "languages",
            "authentication_method",
            "description",
            "contact_number",
            "provider",
            "api_key",
            "knowledge_base",
            "agent_type",
            "assistant_id",
            "inbound",
            "model",
            "model_details",
            "livekit_url",
            "livekit_api_key",
            "livekit_api_secret",
            "livekit_agent_name",
            "livekit_config_json",
            "livekit_max_concurrency",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = None
        instance = self.instance
        request = self.context.get("request")
        if (
            isinstance(instance, AgentDefinition)
            and getattr(instance, "organization", None)
            and getattr(instance, "workspace", None)
        ):
            organization = instance.organization
        if organization is None:
            if request is not None and hasattr(request.user, "organization"):
                organization = (
                    getattr(request, "organization", None) or request.user.organization
                )
        else:
            self.fields["knowledge_base"].queryset = KnowledgeBaseFile.objects.filter(
                organization=organization
            )

    def update(self, instance, validated_data):
        # Serialize concurrent writes on this agent (see
        # AgentDefinitionSerializer.update for the same pattern).
        instance = AgentDefinition.objects.select_for_update(of=("self",)).get(
            pk=instance.pk
        )

        creds_input = _extract_credentials_input(
            validated_data, fallback_provider=instance.provider or ""
        )

        instance.agent_name = validated_data.get("agent_name", instance.agent_name)
        instance.language = validated_data.get("language", instance.language)
        instance.languages = validated_data.get("languages", instance.languages)
        instance.description = validated_data.get("description", instance.description)
        instance.contact_number = validated_data.get(
            "contact_number", instance.contact_number
        )
        instance.authentication_method = validated_data.get(
            "authentication_method", instance.authentication_method
        )
        instance.provider = validated_data.get("provider", instance.provider)
        instance.assistant_id = validated_data.get(
            "assistant_id", instance.assistant_id
        )
        instance.inbound = validated_data.get("inbound", instance.inbound)
        instance.agent_type = validated_data.get("agent_type", instance.agent_type)
        instance.api_key = validated_data.get("api_key", instance.api_key)
        instance.model = validated_data.get("model", instance.model)
        instance.model_details = validated_data.get(
            "model_details", instance.model_details
        )
        if "knowledge_base" in validated_data:
            # allow clearing when explicitly passed as null
            instance.knowledge_base = validated_data.get("knowledge_base")
        instance.save()

        creds_input.provider = instance.provider or creds_input.provider
        version = instance.active_version or instance.latest_version
        if version:
            sync_provider_credentials(version, creds_input)
        return instance

    def to_representation(self, instance):
        """Ensure snapshot is JSON-serializable (convert UUIDs to strings)."""
        data = super().to_representation(instance)
        if data.get("knowledge_base") is not None:
            data["knowledge_base"] = str(data["knowledge_base"])
        return data
