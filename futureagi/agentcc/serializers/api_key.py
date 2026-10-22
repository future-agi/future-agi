from rest_framework import serializers

from agentcc.models import AgentccAPIKey
from agentcc.validators import reserved_key_metadata, validate_safe_agentcc_name
from tfc.utils.api_serializers import StrictInputSerializer
from tfc.utils.error_codes import get_error_message


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


class KeyMetadataValidationMixin:
    """A tenant's key metadata holds labels, never what the gateway reads as
    authority: an org_id other than the key's own org is refused.

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


class AgentccAPIKeyUpdateSerializer(KeyMetadataValidationMixin, StrictInputSerializer):
    name = serializers.CharField(max_length=255, required=False)
    owner = serializers.CharField(max_length=255, required=False, allow_blank=True)
    allowed_models = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    allowed_providers = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    metadata = serializers.DictField(required=False)

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class AgentccAPIKeyCreateSerializer(KeyMetadataValidationMixin, serializers.Serializer):
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
