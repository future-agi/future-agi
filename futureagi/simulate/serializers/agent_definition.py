import re
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.validators import URLValidator
from django.db import transaction
from rest_framework import serializers

from model_hub.models.develop_dataset import KnowledgeBaseFile
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import AgentTypeChoices
from simulate.temporal.constants import DEFAULT_ORG_LIMIT
from tracer.models.observability_provider import ProviderChoices

MASKED_VALUE = "********"

# LiveKit URLs are valid in either WebSocket form (wss://, ws://) or HTTP
# form (https://, http://). The frontend stores whatever the user typed
# and the backend converts the scheme at use-time in three places:
# ValidateLiveKitCredentialsView, simulate.services.livekit.config, and
# LiveKitBridgeConnector._http_url. So the validator just needs to accept
# all four schemes.
_LIVEKIT_URL_VALIDATOR = URLValidator(schemes=["http", "https", "ws", "wss"])


def _is_masked(value: str) -> bool:
    """Detect whether ``value`` is one of the mask strings the backend returns
    on read, so we can avoid re-encrypting a masked display string as if it
    were a real credential.

    Must match every output of:
    - :func:`agentcc.services.credential_manager.mask_key` which returns
      ``""`` for empty, ``"****"`` for keys of length <= 8, and
      ``"<first4>...<last4>"`` (11 chars, ``...`` at index 4) for longer keys.
    - :meth:`ProviderCredentials.get_masked_api_secret` which returns the
      constant ``"********"`` (``MASKED_VALUE``) when a secret is set.
    """
    if not value:
        return False
    if value == MASKED_VALUE:  # secret mask
        return True
    if value == "****":  # short-key mask
        return True
    # Long-key mask: exactly 11 chars, `...` at index 4
    if len(value) == 11 and value[4:7] == "...":
        return True
    return False


@dataclass
class ProviderCredentialsInput:
    """Typed payload for :meth:`AgentDefinitionSerializer._sync_provider_credentials`.

    Captures every field that can land in a ``ProviderCredentials`` row,
    regardless of provider. ``provider`` is the discriminator:

    - ``livekit`` / ``livekit_bridge`` → uses the six ``livekit_*`` fields.
    - ``retell`` / default (``vapi``) → uses ``api_key`` + ``assistant_id``.

    Fields are ``Optional`` because DRF may or may not include them in
    ``validated_data`` depending on the request payload. Missing values are
    treated as "don't touch" by the sync logic (secrets are never cleared
    by a missing key).
    """

    provider: str
    api_key: str | None = None
    assistant_id: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_agent_name: str | None = None
    livekit_config_json: dict[str, Any] | None = None
    livekit_max_concurrency: int | None = None
    provider_was_provided: bool = False


def _extract_credentials_input(
    validated_data: dict, fallback_provider: str
) -> ProviderCredentialsInput:
    """Pop the write-only livekit_* fields out of ``validated_data`` and
    return a :class:`ProviderCredentialsInput` dataclass.

    Call this **before** ``super().create()``/``update()`` — the livekit
    fields must be removed from ``validated_data`` so ``ModelSerializer``
    doesn't try to write them to non-existent columns on
    ``AgentDefinition``. ``api_key`` and ``assistant_id`` stay in place
    (they're real model columns) and are copied into the dataclass by
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
        """Read credentials from ProviderCredentials, fall back to AgentDefinition.

        Serialized ``api_key`` / ``assistant_id`` describe the VAPI/Retell
        credentials and are populated from ``ProviderCredentials`` only when
        the agent's provider actually uses them. For LiveKit agents the
        credentials live under the ``livekit_*`` keys instead, so the
        generic fields are left blank to avoid leaking the masked LiveKit
        key into a VAPI-shaped field.
        """
        data = super().to_representation(instance)
        try:
            creds = instance.credentials
        except AgentDefinition.credentials.RelatedObjectDoesNotExist:
            creds = None

        if creds:
            if creds.provider_type == "livekit":
                data["livekit_url"] = creds.server_url
                data["livekit_api_key"] = creds.get_masked_api_key()
                data["livekit_agent_name"] = creds.agent_name
                data["livekit_config_json"] = creds.config_json or {}
                data["livekit_max_concurrency"] = (
                    creds.max_concurrency or settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
                )
                # Generic VAPI/Retell fields are not meaningful for
                # LiveKit — clear them rather than leaking a masked
                # LiveKit key through ``api_key``.
                data["api_key"] = ""
                data["assistant_id"] = ""
            else:
                data["api_key"] = creds.get_masked_api_key()
                data["assistant_id"] = creds.assistant_id or data.get(
                    "assistant_id", ""
                )
        # Never expose raw secrets (write_only already strips them, but
        # be defensive in case a subclass adds them back).
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
        # Drop masked api_key on create (user never legitimately sends a
        # masked value when creating fresh).
        if _is_masked(validated_data.get("api_key") or ""):
            validated_data.pop("api_key", None)

        instance = super().create(validated_data)

        # Provider was unknown until super().create() ran if the client
        # omitted it (unusual). Backfill from the instance.
        creds_input.provider = instance.provider or creds_input.provider
        AgentDefinitionSerializer._sync_provider_credentials(instance, creds_input)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        # Serialize concurrent writes on the same agent. Without this,
        # two simultaneous PUTs can both read "creds exists", both mutate,
        # and last-writer-wins races leave the row in an inconsistent
        # state (e.g. provider_type from one writer, api_key from the
        # other). select_for_update blocks inside @transaction.atomic.
        instance = AgentDefinition.objects.select_for_update(of=("self",)).get(
            pk=instance.pk
        )

        creds_input = _extract_credentials_input(
            validated_data, fallback_provider=instance.provider or ""
        )

        # Preserve the existing api_key when the client round-trips a
        # masked display value (``****`` / ``abcd...wxyz`` / ``********``).
        new_api_key = validated_data.get("api_key")
        if new_api_key is not None and _is_masked(new_api_key):
            validated_data.pop("api_key")

        instance = super().update(instance, validated_data)

        # If the client sent a new provider, ``super().update`` just
        # applied it — resync ``creds_input.provider`` from the fresh
        # instance so ``_sync_provider_credentials`` routes to the
        # correct branch.
        creds_input.provider = instance.provider or creds_input.provider
        AgentDefinitionSerializer._sync_provider_credentials(instance, creds_input)
        return instance

    @staticmethod
    def _sync_provider_credentials(instance, data: ProviderCredentialsInput):
        """Write the agent's credentials to the ``ProviderCredentials`` table.

        ``data.api_key`` / ``data.assistant_id`` come from the main
        ``validated_data`` (they map to real model columns for VAPI/Retell,
        kept for backward compat). The ``data.livekit_*`` fields are the
        write-only fields popped out before the base ``ModelSerializer``
        save and routed exclusively to ``ProviderCredentials``.
        """
        from simulate.models.agent_definition import ProviderCredentials

        credential_field_was_provided = data.provider_was_provided or any(
            value is not None
            for value in [
                data.api_key,
                data.assistant_id,
                data.livekit_url,
                data.livekit_api_key,
                data.livekit_api_secret,
                data.livekit_agent_name,
                data.livekit_config_json,
                data.livekit_max_concurrency,
            ]
        )
        if not credential_field_was_provided:
            return

        provider = (data.provider or "").strip()

        if provider in ("livekit", "livekit_bridge"):
            provider_type = ProviderCredentials.ProviderType.LIVEKIT
            api_key = (data.livekit_api_key or "").strip()
            api_secret = (data.livekit_api_secret or "").strip()
            assistant_id = ""
            server_url = (data.livekit_url or "").strip()
            agent_name = (data.livekit_agent_name or "").strip()
            config_json = data.livekit_config_json
            max_concurrency = data.livekit_max_concurrency
        elif provider == "retell":
            provider_type = ProviderCredentials.ProviderType.RETELL
            api_key = (data.api_key or "").strip()
            api_secret = ""
            assistant_id = (data.assistant_id or "").strip()
            server_url = ""
            agent_name = ""
            config_json = None
            max_concurrency = None
        else:
            provider_type = ProviderCredentials.ProviderType.VAPI
            api_key = (data.api_key or "").strip()
            api_secret = ""
            assistant_id = (data.assistant_id or "").strip()
            server_url = ""
            agent_name = ""
            config_json = None
            max_concurrency = None

        creds = ProviderCredentials.objects.filter(agent_definition=instance).first()
        if creds is None:
            creds = ProviderCredentials(
                agent_definition=instance,
                provider_type=provider_type,
            )
        # When the agent's provider changes (e.g. vapi → livekit), wipe
        # stale per-provider fields on the existing creds row so old
        # values don't leak through. When the provider is unchanged,
        # preserve existing values and only overwrite fields actually
        # present in the payload.
        provider_changed = creds.provider_type != provider_type
        creds.provider_type = provider_type

        # Secrets: overwrite on non-empty, non-masked input, OR clear
        # when the provider just changed (the old secret belongs to a
        # different provider).
        if api_key and not _is_masked(api_key):
            creds.api_key = api_key  # save() encrypts
        elif provider_changed:
            creds.api_key = ""
        if api_secret and not _is_masked(api_secret):
            creds.api_secret = api_secret  # save() encrypts
        elif provider_changed:
            creds.api_secret = ""

        # Non-secret fields. On a provider change the branch-computed
        # values (which include explicit `""` for non-applicable fields)
        # are written unconditionally so stale data is wiped. On a
        # same-provider update, only write when the field was present
        # in the payload (preserves existing values).
        if provider_changed:
            creds.assistant_id = assistant_id
            creds.server_url = server_url
            creds.agent_name = agent_name
            creds.config_json = config_json if config_json is not None else {}
            creds.max_concurrency = (
                int(max_concurrency)
                if max_concurrency is not None
                else settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
            )
        else:
            if assistant_id:
                creds.assistant_id = assistant_id
            if server_url:
                creds.server_url = server_url
            if agent_name:
                creds.agent_name = agent_name
            if config_json is not None:
                creds.config_json = config_json
            if max_concurrency is not None:
                creds.max_concurrency = int(max_concurrency)
        creds.save()


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
        # Use annotated field if available (from view's Subquery optimization)
        if hasattr(obj, "_latest_version"):
            return obj._latest_version
        # Fallback for non-optimized querysets
        latest_version = (
            AgentVersion.objects.filter(agent_definition=obj)
            .order_by("-version_number")
            .values_list("version_number", flat=True)
            .first()
        )
        return latest_version

    def get_latest_version_id(self, obj):
        """Get the latest version id for the agent"""
        # Use annotated field if available (from view's Subquery optimization)
        if hasattr(obj, "_latest_version_id"):
            return obj._latest_version_id
        # Fallback for non-optimized querysets
        latest_version = (
            AgentVersion.objects.filter(agent_definition=obj)
            .order_by("-version_number")
            .values_list("id", flat=True)
            .first()
        )
        return latest_version


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
        AgentDefinitionSerializer._sync_provider_credentials(instance, creds_input)
        return instance

    def to_representation(self, instance):
        """Ensure snapshot is JSON-serializable (convert UUIDs to strings)."""
        data = super().to_representation(instance)
        if data.get("knowledge_base") is not None:
            data["knowledge_base"] = str(data["knowledge_base"])
        return data
