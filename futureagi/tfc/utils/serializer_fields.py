from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers


JSON_VALUE_SCHEMA = {
    "x-json-value": True,
    "description": "Any valid JSON value.",
}


class JsonValueField(serializers.JSONField):
    """Arbitrary JSON value field for response data with mixed JSON shapes."""

    class Meta:
        swagger_schema_fields = JSON_VALUE_SCHEMA


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
