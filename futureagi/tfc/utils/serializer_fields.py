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

    NOTE on orval: drf-yasg merges ``swagger_schema_fields`` on top of the
    JSONField base schema which always includes ``"type": "object"``.  The
    ``x-json-value`` flag is understood by ``openapi-contract.js`` (runtime
    validation), but orval's code-gen sees ``type: object`` and emits
    ``z.object({}).passthrough()`` — which rejects scalar cell values.
    When this field is used as the child of a DictField for dynamic-column
    table rows, use ``AnyValueDictField`` instead.
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


class AnyValueDictField(serializers.DictField):
    """DictField whose values are any valid JSON (scalar, object, or array).

    ``DictField(child=JsonValueField())`` emits
    ``additionalProperties: {type: object, x-json-value: true}`` — orval
    sees ``type: object`` and narrows the generated TypeScript to
    ``Record<string, object>``, rejecting string/bool/number cell values.

    This field overrides the items schema to ``additionalProperties: {}``
    (JSON Schema "any value") so orval correctly emits ``Record<string, any>``.
    Use this for dynamic-column table rows where cell values are scalars.
    """

    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "additionalProperties": {},
            "x-json-value": True,
            "description": "Row with dynamic columns — cell values are any valid JSON.",
        }
