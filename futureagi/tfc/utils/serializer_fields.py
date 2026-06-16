from rest_framework import serializers


JSON_VALUE_SCHEMA = {
    "x-json-value": True,
    "description": "Any valid JSON value.",
}


class JsonValueField(serializers.JSONField):
    """Arbitrary JSON value field that accepts any JSON — string, object, array, etc.

    drf-yasg infers ``type: object`` for plain JSONField, causing orval to
    emit ``zod.object({}).passthrough()`` which rejects legitimate string
    values (e.g. ``response_format: "text"``).  The ``x-json-value`` flag is
    detected by the contract validator (openapi-contract.js:181) and replaced
    with ``z.any()`` before the type switch runs, so both strings and objects
    are accepted.
    """

    class Meta:
        swagger_schema_fields = JSON_VALUE_SCHEMA
