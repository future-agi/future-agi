"""Unit tests for OpenInferenceAdapter."""

import json

import pytest

from tracer.utils.adapters.openinference import OpenInferenceAdapter


@pytest.fixture
def adapter():
    return OpenInferenceAdapter()


@pytest.mark.unit
class TestOpenInferenceDetect:
    def test_detect_with_span_kind(self, adapter):
        assert adapter.detect({"openinference.span.kind": "LLM"}) is True

    def test_detect_without_marker(self, adapter):
        assert adapter.detect({"fi.span.kind": "LLM"}) is False

    def test_detect_empty(self, adapter):
        assert adapter.detect({}) is False


@pytest.mark.unit
class TestOpenInferenceNormalize:
    def test_maps_span_kind(self, adapter):
        attrs = {"openinference.span.kind": "LLM"}
        result = adapter.normalize(attrs)
        assert result["fi.span.kind"] == "LLM"

    def test_preserves_existing_fi_span_kind(self, adapter):
        attrs = {"openinference.span.kind": "CHAIN", "fi.span.kind": "LLM"}
        result = adapter.normalize(attrs)
        assert result["fi.span.kind"] == "LLM"  # Not overwritten

    def test_strips_openinference_keys(self, adapter):
        attrs = {
            "openinference.span.kind": "LLM",
            "openinference.extra.key": "value",
            "llm.model_name": "gpt-4",
        }
        result = adapter.normalize(attrs)
        assert not any(k.startswith("openinference.") for k in result)
        assert result["llm.model_name"] == "gpt-4"

    def test_preserves_llm_attributes(self, adapter, openinference_llm_attrs):
        result = adapter.normalize(openinference_llm_attrs)
        assert result["llm.model_name"] == "gpt-4o-mini"
        assert result["llm.provider"] == "openai"
        assert result["llm.token_count.prompt"] == 22
        assert result["llm.token_count.completion"] == 4
        assert result["llm.token_count.total"] == 26

    def test_preserves_io_values(self, adapter, openinference_llm_attrs):
        result = adapter.normalize(openinference_llm_attrs)
        assert "input.value" in result
        assert "output.value" in result
        assert result["input.mime_type"] == "application/json"

    def test_preserves_message_attributes(self, adapter, openinference_llm_attrs):
        result = adapter.normalize(openinference_llm_attrs)
        assert result["llm.input_messages.0.message.role"] == "system"
        assert result["llm.output_messages.0.message.role"] == "assistant"

    def test_sets_trace_source(self, adapter):
        attrs = {"openinference.span.kind": "LLM"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.trace.source"] == "openinference"

    def test_retriever_span(self, adapter, openinference_retriever_attrs):
        result = adapter.normalize(openinference_retriever_attrs)
        assert result["fi.span.kind"] == "RETRIEVER"
        assert "input.value" in result

    def test_chain_span(self, adapter):
        attrs = {"openinference.span.kind": "CHAIN"}
        result = adapter.normalize(attrs)
        assert result["fi.span.kind"] == "CHAIN"

    def test_embedding_span(self, adapter):
        attrs = {"openinference.span.kind": "EMBEDDING"}
        result = adapter.normalize(attrs)
        assert result["fi.span.kind"] == "EMBEDDING"

    def test_agent_span(self, adapter):
        attrs = {"openinference.span.kind": "AGENT"}
        result = adapter.normalize(attrs)
        assert result["fi.span.kind"] == "AGENT"

    def test_source_name(self, adapter):
        assert adapter.source_name == "openinference"
