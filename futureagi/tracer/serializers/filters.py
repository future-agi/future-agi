import json

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
FILTER_LIST_QUERY_PARAM_SCHEMA = {
    "type": "string",
    "description": "JSON-encoded canonical filter list.",
}

EVAL_TASK_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "nullable": True,
            "description": "Project scope for the evaluation task.",
        },
        "date_range": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 2,
            "description": "Inclusive start/end ISO timestamps.",
        },
        "created_at": {
            "type": "string",
            "description": "Lower-bound ISO timestamp for legacy task filters.",
        },
        "session_id": {
            "type": "string",
            "description": "Trace session id to constrain the task.",
        },
        "observation_type": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ],
            "description": "Observation span type(s), for example llm, tool, or chain.",
        },
        "span_attributes_filters": FILTER_LIST_SCHEMA,
    },
    "additionalProperties": False,
}

FILTER_ITEM_ALLOWED_KEYS = set(FILTER_ITEM_SCHEMA["properties"])
FILTER_CONFIG_ALLOWED_KEYS = set(FILTER_CONFIG_SCHEMA["properties"])
FILTER_ITEM_REQUIRED_KEYS = set(FILTER_ITEM_SCHEMA["required"])
FILTER_CONFIG_REQUIRED_KEYS = set(FILTER_CONFIG_SCHEMA["required"])
EVAL_TASK_FILTER_ALLOWED_KEYS = set(EVAL_TASK_FILTERS_SCHEMA["properties"])


def parse_filter_list_payload(data):
    """Decode the canonical filter-list payload from body or query params."""
    if data in (None, ""):
        return []
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise serializers.ValidationError("Filters must be valid JSON.") from exc
    if data is None:
        return []
    if not isinstance(data, list):
        raise serializers.ValidationError("Filters must be a list.")
    return data


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

    def to_internal_value(self, data):
        return super().to_internal_value(parse_filter_list_payload(data))


class FilterListQueryParamField(serializers.CharField):
    """Query-param version of FilterListField.

    Query strings carry filters as JSON text (`filters=[...]`). The runtime
    validator still parses and checks the canonical filter-list shape, while
    OpenAPI correctly advertises a string parameter instead of an array of
    repeated query params.
    """

    class Meta:
        swagger_schema_fields = FILTER_LIST_QUERY_PARAM_SCHEMA

    def to_internal_value(self, data):
        return FilterListField().run_validation(data)


class EvalTaskFiltersField(serializers.JSONField):
    """Strict serializer for the saved EvalTask filter object.

    Eval tasks store a small wrapper object around canonical filter lists
    because the dispatcher needs task-scoping keys (`project_id`, `date_range`)
    alongside span attribute filters. Keep that wrapper typed and reject unknown
    keys instead of silently dropping them in `parsing_evaltask_filters`.
    """

    class Meta:
        swagger_schema_fields = EVAL_TASK_FILTERS_SCHEMA

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("Eval task filters must be an object.")

        extra_keys = sorted(set(value) - EVAL_TASK_FILTER_ALLOWED_KEYS)
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown eval task filter keys: {', '.join(extra_keys)}"
            )

        if "date_range" in value:
            date_range = value["date_range"]
            if not isinstance(date_range, list) or len(date_range) != 2:
                raise serializers.ValidationError(
                    "date_range must be a two-value list."
                )

        if "observation_type" in value:
            observation_type = value["observation_type"]
            if isinstance(observation_type, str):
                if not observation_type:
                    raise serializers.ValidationError(
                        "observation_type cannot be empty."
                    )
            elif isinstance(observation_type, list):
                if not observation_type or not all(
                    isinstance(item, str) and item for item in observation_type
                ):
                    raise serializers.ValidationError(
                        "observation_type must be a non-empty string or string list."
                    )
            else:
                raise serializers.ValidationError(
                    "observation_type must be a string or string list."
                )

        if "span_attributes_filters" in value:
            value["span_attributes_filters"] = FilterListField().run_validation(
                value["span_attributes_filters"]
            )

        return value


def filter_list_field(**kwargs):
    return FilterListField(**kwargs)


def filter_list_query_param_field(**kwargs):
    return FilterListQueryParamField(**kwargs)


def eval_task_filters_field(**kwargs):
    return EvalTaskFiltersField(**kwargs)
