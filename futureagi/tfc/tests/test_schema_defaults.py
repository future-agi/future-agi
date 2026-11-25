import pytest
from rest_framework import serializers

from tfc.tests.schema_default_helpers import field_schema
from tracer.serializers.filters import JsonObjectQueryParamField
from tracer.serializers.trace import JSONOrCommaSeparatedStringListField


@pytest.mark.parametrize("query", [True, False])
def test_encoded_fields_without_defaults_stay_without_defaults(query):
    assert "default" not in field_schema(
        JSONOrCommaSeparatedStringListField(required=False), query=query
    )


def test_nonempty_defaults_are_not_replaced():
    schema = field_schema(JsonObjectQueryParamField(default={"name": "value"}))
    assert schema["default"] == {"name": "value"}


def test_unmarked_invalid_defaults_remain_visible():
    class UnmarkedField(serializers.Field):
        def to_representation(self, value):
            return value

    schema = field_schema(UnmarkedField(default=list))
    assert schema["type"] == "string"
    assert schema["default"] == []
