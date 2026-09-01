from __future__ import annotations

import pytest

from tracer.utils.property_registry import (
    normalize_custom_attribute_source,
    parse_property_registry_id,
    property_value_transport_source,
    validate_property_metric_binding,
    validate_property_source_binding,
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
