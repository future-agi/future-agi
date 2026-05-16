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
    "additionalProperties": True,
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
    "additionalProperties": True,
}


class FilterItemField(serializers.JSONField):
    """JSON field with explicit OpenAPI shape for a single filter item.

    Runtime stays intentionally permissive because saved views and legacy UI
    surfaces may carry additional keys; stricter operator validation is layered
    in endpoint serializers and query builders.
    """

    class Meta:
        swagger_schema_fields = FILTER_ITEM_SCHEMA


def filter_list_field(**kwargs):
    return serializers.ListField(child=FilterItemField(), **kwargs)
