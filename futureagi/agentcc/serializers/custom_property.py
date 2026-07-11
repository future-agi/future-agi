from rest_framework import serializers

from agentcc.models.custom_property import AgentccCustomPropertySchema
from agentcc.validators import validate_safe_agentcc_name


class AgentccCustomPropertySchemaSerializer(serializers.ModelSerializer):
    """Defines a custom metadata property that gateway request logs can carry,
    with a type and optional validation (enum allowed_values, required flag,
    default). Use it to declare structured tags you want to attach to and
    validate on requests. Listed/read via list_agentcc_custom_property_schemas /
    get_agentcc_custom_property_schema."""

    class Meta:
        model = AgentccCustomPropertySchema
        fields = [
            "id",
            "organization",
            "project",
            "name",
            "description",
            "property_type",
            "required",
            "allowed_values",
            "default_value",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "project": {
                "help_text": "UUID of the AgentCC project this schema is scoped to (optional)."
            },
            "name": {"help_text": "Unique (per org) property name."},
            "description": {"help_text": "Optional description of the property."},
            "property_type": {
                "help_text": "Value type: string, number, boolean, or enum."
            },
            "required": {
                "help_text": "Whether this property must be present on requests."
            },
            "allowed_values": {
                "help_text": "JSON array of permitted values; required when property_type is enum."
            },
            "default_value": {
                "help_text": "Default value applied when the property is absent; must match property_type."
            },
        }

    def validate_allowed_values(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("allowed_values must be a JSON array")
        return value

    def validate(self, attrs):
        property_type = attrs.get(
            "property_type",
            getattr(
                self.instance,
                "property_type",
                AgentccCustomPropertySchema.TYPE_STRING,
            ),
        )
        allowed_values = attrs.get(
            "allowed_values",
            getattr(self.instance, "allowed_values", []),
        )
        default_value = attrs.get(
            "default_value",
            getattr(self.instance, "default_value", None),
        )

        if not isinstance(allowed_values, list):
            raise serializers.ValidationError(
                {"allowed_values": "allowed_values must be a JSON array"}
            )

        if property_type == AgentccCustomPropertySchema.TYPE_ENUM:
            if not allowed_values:
                raise serializers.ValidationError(
                    {
                        "allowed_values": (
                            "Enum properties require at least one allowed value"
                        )
                    }
                )
            for index, value in enumerate(allowed_values):
                if value is None or isinstance(value, (dict, list)):
                    raise serializers.ValidationError(
                        {
                            "allowed_values": (
                                f"Allowed value at index {index} must be a string, number, or boolean"
                            )
                        }
                    )

        if default_value is not None:
            error = self._default_value_error(
                property_type=property_type,
                allowed_values=allowed_values,
                default_value=default_value,
            )
            if error:
                raise serializers.ValidationError({"default_value": error})

        return attrs

    def _default_value_error(self, *, property_type, allowed_values, default_value):
        if property_type == AgentccCustomPropertySchema.TYPE_STRING:
            if not isinstance(default_value, str):
                return "Default value must be a string"
        elif property_type == AgentccCustomPropertySchema.TYPE_NUMBER:
            if isinstance(default_value, bool) or not isinstance(
                default_value, (int, float)
            ):
                return "Default value must be a number"
        elif property_type == AgentccCustomPropertySchema.TYPE_BOOLEAN:
            if not isinstance(default_value, bool):
                return "Default value must be a boolean"
        elif property_type == AgentccCustomPropertySchema.TYPE_ENUM:
            if default_value not in allowed_values:
                return "Default value must be one of the allowed values"
        return ""

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
