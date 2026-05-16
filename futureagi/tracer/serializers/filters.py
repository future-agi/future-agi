from rest_framework import serializers


FILTER_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filter_type": {
            "type": "string",
            "description": "Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array.",
        },
        "filter_op": {
            "type": "string",
            "description": "Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null.",
        },
        "filter_value": {
            "description": "Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type.",
        },
        "col_type": {
            "type": "string",
            "description": "Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL.",
        },
    },
    "required": ["filter_type", "filter_op"],
    "additionalProperties": False,
}


FILTER_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "column_id": {
            "type": "string",
            "description": "Column or attribute id to filter on.",
        },
        "display_name": {
            "type": "string",
            "description": "Optional UI label for chips and saved views.",
        },
        "filter_config": FILTER_CONFIG_SCHEMA,
    },
    "required": ["column_id", "filter_config"],
    "additionalProperties": False,
}

FILTER_ITEM_ALLOWED_KEYS = set(FILTER_ITEM_SCHEMA["properties"])
FILTER_CONFIG_ALLOWED_KEYS = set(FILTER_CONFIG_SCHEMA["properties"])


class FilterItemField(serializers.JSONField):
    """JSON field with explicit OpenAPI shape for a single filter item.

    Runtime validation is intentionally strict, matching the generated API
    schema pattern used by mature schema-first systems: callers send the
    canonical snake_case filter contract or get a validation error.
    """

    class Meta:
        swagger_schema_fields = FILTER_ITEM_SCHEMA

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("Filter item must be an object.")

        extra_keys = sorted(set(value) - FILTER_ITEM_ALLOWED_KEYS)
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown filter item keys: {', '.join(extra_keys)}"
            )

        config = value.get("filter_config")
        if not isinstance(config, dict):
            raise serializers.ValidationError("Filter config must be an object.")

        extra_config_keys = sorted(set(config) - FILTER_CONFIG_ALLOWED_KEYS)
        if extra_config_keys:
            raise serializers.ValidationError(
                f"Unknown filter config keys: {', '.join(extra_config_keys)}"
            )

        return value


def filter_list_field(**kwargs):
    return serializers.ListField(child=FilterItemField(), **kwargs)
