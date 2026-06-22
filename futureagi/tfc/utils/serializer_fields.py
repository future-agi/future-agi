from rest_framework import serializers


JSON_VALUE_SCHEMA = {
    "x-json-value": True,
    "description": "Any valid JSON value.",
}


class JsonValueField(serializers.JSONField):
    """Arbitrary JSON value field — use ONLY for genuinely open-shape values.

    Emits ``x-json-value: true`` in the OpenAPI schema, detected by the
    ``x-json-value`` branch in ``openapi-contract.js``, which maps to
    ``z.any()``.  Because ``z.any()`` removes all contract knowledge of the
    field shape, this field should only be used when the value is genuinely
    open (e.g. provider-specific config dicts whose keys vary per provider).

    For fields with a known shape use a typed serializer or ``StringOrObjectField``
    for ``string | object`` unions.
    """

    class Meta:
        swagger_schema_fields = JSON_VALUE_SCHEMA


class StringOrObjectField(serializers.JSONField):
    """Field that accepts either a plain string or a JSON object.

    Emits ``x-string-or-object: true`` in the OpenAPI schema, detected by
    the ``x-string-or-object`` branch in ``openapi-contract.js``, which maps
    to ``z.union([z.string(), z.object({}).passthrough()])``.

    Use this for fields like ``response_format`` and ``model`` that are
    legitimately ``string | object`` at the protocol level.
    """

    class Meta:
        swagger_schema_fields = {
            "x-string-or-object": True,
            "description": "String or JSON object.",
        }
