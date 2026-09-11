"""Runtime/OpenAPI parity for attribute-discovery response values."""

import json
from pathlib import Path

import pytest
from tfc.utils.serializer_fields import JsonValueField

from tracer.serializers.dashboard import DashboardFilterValueOptionSerializer
from tracer.serializers.observation_span import (
    ObservationAttributeListResponseSerializer,
)
from tracer.serializers.span_attributes import (
    SpanAttributeDetailResponseSerializer,
    SpanAttributeKeySerializer,
    SpanAttributeKeysResponseSerializer,
    SpanAttributeTopValueSerializer,
    SpanAttributeValueSerializer,
    SpanAttributeValuesResponseSerializer,
)


def _swagger_definitions():
    path = (
        Path(__file__).resolve().parents[3]
        / "api_contracts"
        / "openapi"
        / "swagger.json"
    )
    with path.open() as schema_file:
        return json.load(schema_file)["definitions"]


TYPE_FIELDS = (
    (SpanAttributeKeySerializer, "SpanAttributeKey"),
    (SpanAttributeValueSerializer, "SpanAttributeValue"),
    (SpanAttributeDetailResponseSerializer, "SpanAttributeDetailResponse"),
)

QUERY_STATUS_SERIALIZERS = (
    SpanAttributeKeysResponseSerializer,
    SpanAttributeValuesResponseSerializer,
    SpanAttributeDetailResponseSerializer,
    ObservationAttributeListResponseSerializer,
)


@pytest.mark.parametrize(("serializer_cls", "definition_name"), TYPE_FIELDS)
def test_span_attribute_type_enum_matches_generated_openapi(
    serializer_cls, definition_name
):
    runtime_choices = list(serializer_cls().fields["type"].choices)
    openapi_choices = _swagger_definitions()[definition_name]["properties"]["type"][
        "enum"
    ]

    expected_choices = ["string", "number", "boolean", "array", "map", "json"]

    assert runtime_choices == expected_choices
    assert openapi_choices == runtime_choices


@pytest.mark.parametrize("serializer_cls", QUERY_STATUS_SERIALIZERS)
def test_attribute_query_status_enum_is_declared_for_openapi(serializer_cls):
    runtime_choices = list(serializer_cls().fields["query_status"].choices)

    expected = ["complete", "sampled", "degraded"]
    if serializer_cls is SpanAttributeDetailResponseSerializer:
        expected = ["complete", "pending", "sampled", "degraded"]
    assert runtime_choices == expected


@pytest.mark.parametrize(
    ("serializer_cls", "definition_name"),
    (
        (DashboardFilterValueOptionSerializer, "DashboardFilterValueOption"),
        (SpanAttributeValueSerializer, "SpanAttributeValue"),
        (SpanAttributeTopValueSerializer, "SpanAttributeTopValue"),
    ),
)
def test_attribute_picker_json_values_match_generated_openapi(
    serializer_cls, definition_name
):
    field = serializer_cls().fields["value"]
    assert isinstance(field, JsonValueField)
    for value in ("Rejected", 7, 1.5, False, None, ["nested"], {"nested": True}):
        assert field.run_validation(value) == value

    value_schema = _swagger_definitions()[definition_name]["properties"]["value"]
    assert value_schema["x-json-value"] is True
    assert value_schema["x-nullable"] is True
