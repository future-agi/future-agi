from rest_framework import serializers

from tracer.utils.filter_operators import (
    FILTER_TYPE_ALLOWED_OPS,
    LIST_FILTER_OPS,
    NO_VALUE_FILTER_OPS,
    RANGE_FILTER_OPS,
)


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

FILTER_LIST_SCHEMA = {
    "type": "array",
    "items": FILTER_ITEM_SCHEMA,
}

FILTER_ITEM_ALLOWED_KEYS = set(FILTER_ITEM_SCHEMA["properties"])
FILTER_CONFIG_ALLOWED_KEYS = set(FILTER_CONFIG_SCHEMA["properties"])
FILTER_ITEM_REQUIRED_KEYS = set(FILTER_ITEM_SCHEMA["required"])
FILTER_CONFIG_REQUIRED_KEYS = set(FILTER_CONFIG_SCHEMA["required"])


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

        missing_keys = sorted(FILTER_ITEM_REQUIRED_KEYS - set(value))
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing filter item keys: {', '.join(missing_keys)}"
            )

        extra_keys = sorted(set(value) - FILTER_ITEM_ALLOWED_KEYS)
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown filter item keys: {', '.join(extra_keys)}"
            )

        config = value.get("filter_config")
        if not isinstance(config, dict):
            raise serializers.ValidationError("Filter config must be an object.")

        missing_config_keys = sorted(FILTER_CONFIG_REQUIRED_KEYS - set(config))
        if missing_config_keys:
            raise serializers.ValidationError(
                f"Missing filter config keys: {', '.join(missing_config_keys)}"
            )

        extra_config_keys = sorted(set(config) - FILTER_CONFIG_ALLOWED_KEYS)
        if extra_config_keys:
            raise serializers.ValidationError(
                f"Unknown filter config keys: {', '.join(extra_config_keys)}"
            )

        filter_type = config.get("filter_type")
        filter_op = config.get("filter_op")
        allowed_ops = FILTER_TYPE_ALLOWED_OPS.get(filter_type)
        if allowed_ops is None:
            raise serializers.ValidationError(
                f"Unsupported filter_type {filter_type!r}."
            )
        if filter_op not in allowed_ops:
            raise serializers.ValidationError(
                f"Unsupported filter_op {filter_op!r} for filter_type {filter_type!r}."
            )

        filter_value = config.get("filter_value")
        if filter_op in RANGE_FILTER_OPS:
            if not isinstance(filter_value, list) or len(filter_value) != 2:
                raise serializers.ValidationError(
                    f"{filter_op!r} requires a two-value filter_value list."
                )
        elif filter_op in LIST_FILTER_OPS:
            if not isinstance(filter_value, list) or not filter_value:
                raise serializers.ValidationError(
                    f"{filter_op!r} requires a non-empty filter_value list."
                )
        elif filter_op not in NO_VALUE_FILTER_OPS and "filter_value" not in config:
            raise serializers.ValidationError(f"{filter_op!r} requires filter_value.")

        return value


class FilterListField(serializers.ListField):
    """List wrapper that carries the exact filter-item OpenAPI shape.

    drf-yasg treats bare JSONField children as open-ended objects even when the
    child has swagger_schema_fields. Defining the array schema on the list field
    keeps the generated contract aligned with the runtime validator.
    """

    child = FilterItemField()

    class Meta:
        swagger_schema_fields = FILTER_LIST_SCHEMA


def filter_list_field(**kwargs):
    return FilterListField(**kwargs)
