from __future__ import annotations

import pytest

from tracer.utils.property_registry import (
    normalize_custom_attribute_source,
    parse_property_registry_id,
    property_value_transport_source,
    validate_property_filter_binding,
    validate_property_metric_binding,
    validate_property_source_binding,
)


@pytest.mark.parametrize("property_name", ["name", "trace_name"])
def test_trace_name_identity_accepts_native_root_name_filter(property_name):
    from tracer.serializers.trace import TraceObserveListQuerySerializer

    property_id = f"system_attribute:traces:{property_name}"
    decoded = validate_property_filter_binding(
        property_id, column_id="name", column_type="SYSTEM_METRIC", source="traces"
    )
    assert decoded["property_id"] == "system_attribute:traces:trace_name"
    serializer = TraceObserveListQuerySerializer(data={
        "filters": [{
            "column_id": "name", "property_id": property_id,
            "filter_config": {
                "col_type": "SYSTEM_METRIC", "filter_type": "text",
                "filter_op": "in", "filter_value": ["example-trace"],
            },
        }],
        "cursor_mode": True, "page_size": 25,
    })
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"][0]["column_id"] == "name"


def test_trace_name_alias_does_not_rebind_raw_attributes_or_span_names():
    assert parse_property_registry_id("custom_attribute:name")["metric_name"] == "name"
    assert parse_property_registry_id("system_attribute:spans:name")["metric_name"] == "name"
    with pytest.raises(ValueError, match="does not match column_id"):
        validate_property_filter_binding(
            "custom_attribute:name", column_id="trace_name", column_type="SPAN_ATTRIBUTE"
        )


def test_legacy_system_aliases_resolve_to_one_catalog_identity() -> None:
    decoded = parse_property_registry_id("system_attribute:traces:session_id")

    assert decoded["property_id"] == "system_attribute:traces:session"
    assert decoded["metric_name"] == "session"
    # A saved graph may still name the physical column. The catalog identity is
    # canonical while the native adapter remains backwards-compatible.
    validate_property_metric_binding(
        "system_attribute:traces:session_id",
        metric_name="session_id",
        metric_type="system_metric",
        source="traces",
    )


def test_prompt_catalog_namespace_uses_trace_transport() -> None:
    decoded = parse_property_registry_id("system_attribute:prompts:avg_latency")

    assert validate_property_source_binding(decoded, "traces") is decoded
    assert validate_property_source_binding(decoded, "prompts") is decoded
    assert property_value_transport_source("prompts") == "traces"


@pytest.mark.parametrize(
    "source", ["traces", "spans", "voice_calls", "voiceCalls", "prompts"]
)
def test_custom_attribute_aliases_share_trace_transport(source: str) -> None:
    decoded = parse_property_registry_id("custom_attribute:customer.plan")

    assert normalize_custom_attribute_source(source) == "traces"
    assert validate_property_source_binding(decoded, source) is decoded


@pytest.mark.parametrize(
    "source",
    ["sessions", "users", "datasets", "dataset_column", "simulation", "all", "both"],
)
def test_custom_attribute_rejects_unsupported_sources(source: str) -> None:
    decoded = parse_property_registry_id("custom_attribute:customer.plan")

    with pytest.raises(ValueError, match="custom_attribute is not compatible"):
        normalize_custom_attribute_source(source)
    with pytest.raises(ValueError, match="custom_attribute is not compatible"):
        validate_property_source_binding(decoded, source)
