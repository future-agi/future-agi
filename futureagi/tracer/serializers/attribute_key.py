"""Exact span-attribute keys are opaque UTF-8 data, not normalized text."""

from rest_framework import serializers

from tracer.utils.attribute_suggestion_contract import (
    ATTRIBUTE_KEY_MAX_UTF8_BYTES,
    validate_exact_attribute_key,
)

_KEY_DESCRIPTION = (
    f"Nonempty exact attribute key, at most {ATTRIBUTE_KEY_MAX_UTF8_BYTES} UTF-8 bytes. "
    "Whitespace, controls and case are preserved."
)


class ExactAttributeKeyField(serializers.Field):
    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "minLength": 1,
            "description": _KEY_DESCRIPTION,
        }

    def __init__(self, **kwargs):
        # Query Parameters do not receive Meta.swagger_schema_fields in drf-yasg.
        kwargs.setdefault("help_text", _KEY_DESCRIPTION)
        super().__init__(**kwargs)

    def get_value(self, dictionary):
        # DRF otherwise treats optional QueryDict q="" as an omitted field.
        return dictionary.get(self.field_name, serializers.empty)

    def to_internal_value(self, data):
        try:
            return validate_exact_attribute_key(data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def to_representation(self, value):
        return value
