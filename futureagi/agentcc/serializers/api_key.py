from collections.abc import Mapping

from django.utils import timezone
from rest_framework import serializers
from rest_framework.settings import api_settings

from agentcc.models import AgentccAPIKey
from agentcc.validators import reserved_key_metadata, validate_safe_agentcc_name
from tfc.utils.api_errors import ApiErrorCode
from tfc.utils.api_serializers import StrictInputSerializer
from tfc.utils.error_codes import get_error_message

# How much request content the gateway logs for a key.
# The most restrictive of the org, the key and the request wins.
CONTENT_MODES = ("full", "metadata", "none")

# Key settings kept in AgentccAPIKey.metadata, which reaches the gateway in
# the key's synced metadata (a map of strings). Each is set through its own
# field; a raw ``metadata`` dict may not carry them, nor the key's org.
KEY_METADATA_FIELDS = ("log_content",)

# Key limits the gateway does not enforce for a key synced from here: it fills
# a key's rate limits and IP allowlist only from its own config file, and reads
# nothing else of a synced key's metadata but log_content. They are refused,
# as a field or in a raw metadata dict, so no key looks limited when it is not.
# Reads leave these names out of a key's metadata, and the names stay reserved
# so a raw metadata replace neither drops nor sets one.
UNENFORCED_KEY_LIMIT_FIELDS = ("rate_limit_rpm", "rate_limit_tpm", "allowed_ips")
RESERVED_METADATA_KEYS = frozenset(
    {*KEY_METADATA_FIELDS, *UNENFORCED_KEY_LIMIT_FIELDS, "org_id"}
)


def unenforced_key_limits(data):
    """The unenforced key limits ``data`` names, as a field or inside its raw
    ``metadata`` dict, sorted. Any value counts, ``null`` included."""
    if not hasattr(data, "keys"):
        return []
    names = set(data.keys())
    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        names |= set(metadata.keys())
    return sorted(names & set(UNENFORCED_KEY_LIMIT_FIELDS))


class KeyMetadataFieldsMixin(serializers.Serializer):
    """The key settings stored in key metadata, validated as the gateway reads
    them, and the refusal of the key limits it does not enforce.

    ``log_content`` takes no ``null``; ``full`` is its unrestricted value."""

    log_content = serializers.ChoiceField(choices=CONTENT_MODES, required=False)

    def to_internal_value(self, data):
        # Ahead of every other check, so the refusal is the one error a client
        # gets for a limit, and the create serializer, which drops unknown
        # fields, cannot accept one silently.
        refused = unenforced_key_limits(data)
        if refused:
            message = get_error_message("KEY_LIMIT_NOT_ENFORCED").format(
                ", ".join(refused)
            )
            raise serializers.ValidationError(
                {api_settings.NON_FIELD_ERRORS_KEY: [message]},
                code=ApiErrorCode.KEY_LIMIT_NOT_ENFORCED,
            )
        return super().to_internal_value(data)

    def validate_metadata(self, value):
        settings = sorted(set(KEY_METADATA_FIELDS) & set(value))
        if settings:
            raise serializers.ValidationError(
                f"{', '.join(settings)} cannot be set in metadata; "
                "use the key's own fields"
            )
        # The authority names (org_id and the rest of RESERVED_KEY_METADATA)
        # are KeyMetadataValidationMixin's, next in the MRO.
        return super().validate_metadata(value)


class AgentccAPIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentccAPIKey
        fields = [
            "id",
            "project",
            "user",
            "gateway_key_id",
            "key_prefix",
            "name",
            "owner",
            "status",
            "allowed_models",
            "allowed_providers",
            "metadata",
            "last_used_at",
            "expires_at",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "gateway_key_id",
            "key_prefix",
            "status",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        # A read leaves the unenforced key limits out of the metadata it
        # returns.
        data = super().to_representation(instance)
        if isinstance(data.get("metadata"), dict):
            data["metadata"] = {
                key: value
                for key, value in data["metadata"].items()
                if key not in UNENFORCED_KEY_LIMIT_FIELDS
            }
        return data


class KeyMetadataValidationMixin:
    """A key's metadata holds labels only: the names the gateway reads as
    authority (org_id and the rest of RESERVED_KEY_METADATA) are refused,
    except an org_id equal to the key's own org.

    ``context["key_organization_id"]`` is the key's own org (on create, the
    org it is created in). An org_id equal to it is accepted, so a client can
    write back the metadata it read, or reuse it for a new key: responses carry
    it, and ``bound_key_metadata`` sets it anyway.
    """

    def validate_metadata(self, value):
        own_org_id = self.context.get("key_organization_id")
        reserved = [
            key
            for key in reserved_key_metadata(value)
            if not (
                key == "org_id"
                and own_org_id is not None
                and str(value[key]) == str(own_org_id)
            )
        ]
        if reserved:
            raise serializers.ValidationError(
                get_error_message("AGENTCC_KEY_METADATA_RESERVED").format(
                    ", ".join(reserved)
                )
            )
        return value


class AgentccAPIKeyUpdateSerializer(
    KeyMetadataFieldsMixin, KeyMetadataValidationMixin, StrictInputSerializer
):
    name = serializers.CharField(max_length=255, required=False)
    owner = serializers.CharField(max_length=255, required=False, allow_blank=True)
    allowed_models = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    allowed_providers = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    metadata = serializers.DictField(required=False)
    # A model column, synced to the gateway with the key; null clears it.
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("expires_at must be in the future")
        return value


class AgentccAPIKeyCreateSerializer(
    KeyMetadataFieldsMixin, KeyMetadataValidationMixin, serializers.Serializer
):
    project_id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=255)
    owner = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    allowed_models = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    allowed_providers = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    metadata = serializers.DictField(required=False, default=dict)

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
