from rest_framework import serializers

from agentcc.models import AgentccAPIKey
from agentcc.validators import validate_safe_agentcc_name
from tfc.utils.api_serializers import StrictInputSerializer


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


class AgentccAPIKeyUpdateSerializer(StrictInputSerializer):
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
            raise serializers.ValidationError(str(exc))


class AgentccAPIKeyCreateSerializer(serializers.Serializer):
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
            raise serializers.ValidationError(str(exc))
