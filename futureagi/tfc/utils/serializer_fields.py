from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
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

    Emits ``x-string-or-object: true`` in the OpenAPI schema. Two consumers:

    - The runtime contract mapper (``openapi-contract.js``) reads the flag
      and produces ``z.union([z.string(), z.object().passthrough()])``.
    - Orval (the static TS generator) ignores custom extensions, so the
      post-processor in ``generate-openapi-client.mjs`` rewrites the
      object-only TS alias / zod object into a proper union in both
      ``api.schemas.ts`` and ``api.zod.ts`` after orval runs.

    A native ``oneOf`` would be cleaner but drf-yasg emits Swagger 2.0
    which does not support ``oneOf``. Tracked for the OpenAPI 3.0
    migration (TH-6029); until then the custom extension + post-processor
    is the working pattern.

    Use this for fields like ``response_format`` and ``model`` that are
    legitimately ``string | object`` at the protocol level.
    """

    def to_internal_value(self, data):
        # Runtime guard — the generated contract describes string-or-object
        # but the field inherits from ``JSONField`` whose base
        # ``to_internal_value`` would otherwise accept arrays, numbers,
        # booleans and ``null``. Without this override, a request that
        # bypasses the FE contract validator (SDK call, curl, internal
        # caller) would persist ``model: []`` happily.
        if isinstance(data, (str, dict)):
            return data
        raise serializers.ValidationError(
            "Expected a string or a JSON object."
        )

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


class StringOrArrayField(serializers.JSONField):
    """Field that accepts either a plain string or a JSON array.

    Use this for ``messages[].content`` which is either a plain text string
    or an array of content-part objects (OpenAI multi-part format).

    Emits ``x-string-or-array: true`` detected by ``openapi-contract.js``
    which maps it to ``z.union([z.string(), z.array(z.unknown())])``.
    """

    def to_internal_value(self, data):
        # Runtime guard — parallel to ``StringOrObjectField`` above. The
        # generated contract describes string-or-array but the field
        # inherits from ``JSONField`` whose base ``to_internal_value`` would
        # otherwise accept dicts, numbers, booleans and ``null``. Without
        # this override an SDK / curl / internal caller that bypasses the
        # FE contract validator would persist ``messages[].content: 42``
        # or ``content: {}`` happily.
        if isinstance(data, (str, list)):
            return data
        raise serializers.ValidationError(
            "Expected a string or a JSON array."
        )

    class Meta:
        swagger_schema_fields = {
            "x-string-or-array": True,
            "description": "Plain text string or array of content-part objects.",
        }


class StrictAwareDateTimeField(serializers.DateTimeField):
    """DateTimeField that rejects naive datetimes before DRF localizes them."""

    def to_internal_value(self, value):
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed is not None and timezone.is_naive(parsed):
                raise serializers.ValidationError("datetime must include a timezone")
        elif isinstance(value, datetime) and timezone.is_naive(value):
            raise serializers.ValidationError("datetime must include a timezone")
        return super().to_internal_value(value)
