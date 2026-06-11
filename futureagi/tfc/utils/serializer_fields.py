from rest_framework import serializers


JSON_VALUE_SCHEMA = {
    "x-json-value": True,
    "description": "Any valid JSON value.",
}


class JsonValueField(serializers.JSONField):
    """Arbitrary JSON value field for response data with mixed JSON shapes."""

    class Meta:
        swagger_schema_fields = JSON_VALUE_SCHEMA
