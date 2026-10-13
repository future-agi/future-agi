from rest_framework import serializers

from agent_playground.models.choices import RESERVED_NAME_RE, PortDirection
from agent_playground.models.port import Port
from tfc.utils.serializer_fields import JsonValueField


class PortCreateSerializer(serializers.Serializer):
    """Port data inside CreateNodeSerializer (FE-generated ID)."""

    id = serializers.UUIDField(required=True, help_text="FE-generated UUID")
    key = serializers.CharField(required=True, max_length=100)
    display_name = serializers.CharField(required=True, max_length=100)
    direction = serializers.ChoiceField(required=True, choices=PortDirection.choices)
    data_schema = serializers.JSONField(required=False, default=dict)
    ref_port_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if attrs.get("direction") == PortDirection.OUTPUT and RESERVED_NAME_RE.search(
            attrs.get("display_name", "")
        ):
            raise serializers.ValidationError(
                {
                    "display_name": "Output port display_name cannot contain "
                    "reserved characters: . [ ] { }"
                }
            )
        return attrs


class UpdatePortSerializer(serializers.Serializer):
    """Serializer for PATCH /ports/{port_id}/."""

    display_name = serializers.CharField(required=True, max_length=100)


class PortReadSerializer(serializers.ModelSerializer):
    """Serializer for reading port data in responses."""

    ref_port_id = serializers.UUIDField(
        source="ref_port.id", read_only=True, default=None, allow_null=True
    )
    default_value = JsonValueField(read_only=True, allow_null=True)

    class Meta:
        model = Port
        fields = [
            "id",
            "key",
            "display_name",
            "direction",
            "data_schema",
            "required",
            "default_value",
            "metadata",
            "ref_port_id",
        ]
        read_only_fields = fields
        swagger_schema_fields = {"required": fields}


class PortWriteSerializer(serializers.Serializer):
    """Serializer for writing port data in requests."""

    id = serializers.UUIDField(
        required=True, help_text="Frontend-generated UUID for the port"
    )
    key = serializers.CharField(required=True, max_length=100)
    display_name = serializers.CharField(required=True, max_length=100)
    direction = serializers.ChoiceField(required=True, choices=PortDirection.choices)
    data_schema = serializers.JSONField(required=False, default=dict)
    required = serializers.BooleanField(required=False, default=True)
    default_value = serializers.JSONField(required=False, allow_null=True, default=None)
    metadata = serializers.JSONField(required=False, default=dict)
    ref_port_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if attrs.get("direction") == PortDirection.OUTPUT and RESERVED_NAME_RE.search(
            attrs.get("display_name", "")
        ):
            raise serializers.ValidationError(
                {
                    "display_name": "Output port display_name cannot contain "
                    "reserved characters: . [ ] { }"
                }
            )
        return attrs
