"""Unit tests for the public normalize_span_attributes() function."""

import json

import pytest

from tracer.utils.adapters.base import (
    _propagate_to_span_data,
    normalize_span_attributes,
)


@pytest.mark.unit
class TestNormalizeSpanAttributes:
    def test_batch_mixed_formats(self):
        """A batch with Langfuse, OpenLLMetry, and FI-native spans normalizes each correctly."""
        otel_data_list = [
            {
                "attributes": {
                    "langfuse.observation.type": "generation",
                    "langfuse.observation.model.name": "gpt-4o",
                }
            },
            {
                "attributes": {
                    "gen_ai.system": "openai",
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": "gpt-4o",
                }
            },
            {
                "attributes": {
                    "fi.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                }
            },
        ]
        normalize_span_attributes(otel_data_list)

        # Langfuse
        assert otel_data_list[0]["attributes"]["gen_ai.trace.source"] == "langfuse"
        assert otel_data_list[0]["attributes"]["gen_ai.span.kind"] == "LLM"
        assert not any(
            k.startswith("langfuse.") for k in otel_data_list[0]["attributes"]
        )

        # OTEL GenAI (gen_ai.* without traceloop.* now goes to otel_genai adapter)
        assert otel_data_list[1]["attributes"]["gen_ai.trace.source"] == "traceai"
        assert otel_data_list[1]["attributes"]["gen_ai.span.kind"] == "LLM"
        # otel_genai adapter preserves gen_ai.* keys (adds llm.* aliases)

        # FI Native
        assert otel_data_list[2]["attributes"]["gen_ai.trace.source"] == "traceai"
        assert otel_data_list[2]["attributes"]["fi.span.kind"] == "LLM"

    def test_empty_list(self):
        normalize_span_attributes([])  # Should not raise

    def test_span_without_attributes(self):
        otel_data_list = [{"name": "test-span"}]
        normalize_span_attributes(otel_data_list)
        assert "attributes" not in otel_data_list[0]

    def test_span_with_empty_attributes(self):
        otel_data_list = [{"attributes": {}}]
        normalize_span_attributes(otel_data_list)
        assert otel_data_list[0]["attributes"] == {}

    def test_unknown_format_passthrough(self):
        attrs = {"custom.key": "val", "another": 42}
        otel_data_list = [{"attributes": attrs.copy()}]
        normalize_span_attributes(otel_data_list)
        # No adapter matched, attributes unchanged
        assert otel_data_list[0]["attributes"]["custom.key"] == "val"
        assert "gen_ai.trace.source" not in otel_data_list[0]["attributes"]

    def test_in_place_mutation(self):
        """Verify the attributes dict is modified in-place."""
        attrs = {"fi.span.kind": "LLM", "llm.model_name": "gpt-4"}
        otel_data_list = [{"attributes": attrs}]
        normalize_span_attributes(otel_data_list)
        assert attrs["gen_ai.trace.source"] == "traceai"  # Same object was mutated


@pytest.mark.unit
class TestPropagateToSpanData:
    def test_metadata_propagated(self):
        span_data = {"attributes": {"metadata": {"key": "val"}}}
        _propagate_to_span_data(span_data)
        assert span_data["metadata"] == {"key": "val"}

    def test_does_not_overwrite_existing_metadata(self):
        span_data = {
            "metadata": {"existing": True},
            "attributes": {"metadata": {"from_attrs": True}},
        }
        _propagate_to_span_data(span_data)
        assert span_data["metadata"] == {"existing": True}

    def test_no_metadata_in_attributes(self):
        span_data = {"attributes": {"llm.model": "gpt-4"}}
        _propagate_to_span_data(span_data)
        assert "metadata" not in span_data
