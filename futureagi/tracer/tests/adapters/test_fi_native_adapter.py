"""Unit tests for FiNativeAdapter."""

import json

import pytest

from tracer.utils.adapters.fi_native import FiNativeAdapter


@pytest.fixture
def adapter():
    return FiNativeAdapter()


@pytest.mark.unit
class TestFiNativeDetect:
    def test_detect_with_fi_span_kind(self, adapter):
        assert adapter.detect({"fi.span.kind": "LLM"}) is True

    def test_detect_without_fi_span_kind(self, adapter):
        assert adapter.detect({"llm.model_name": "gpt-4"}) is False

    def test_detect_empty(self, adapter):
        assert adapter.detect({}) is False


@pytest.mark.unit
class TestFiNativeNormalize:
    def test_passthrough_preserves_all_attributes(self, adapter, fi_native_llm_attrs):
        original_keys = set(fi_native_llm_attrs.keys())
        result = adapter.normalize(fi_native_llm_attrs)
        # All original keys preserved
        for key in original_keys:
            assert key in result

    def test_sets_trace_source(self, adapter, fi_native_llm_attrs):
        result = adapter.normalize(fi_native_llm_attrs)
        assert result["gen_ai.trace.source"] == "traceai"

    def test_preserves_llm_attributes(self, adapter, fi_native_llm_attrs):
        result = adapter.normalize(fi_native_llm_attrs)
        assert result["llm.model_name"] == "gpt-4o-mini"
        assert result["llm.token_count.prompt"] == 22
        assert result["llm.token_count.completion"] == 4
        assert result["llm.token_count.total"] == 26

    def test_preserves_io(self, adapter, fi_native_llm_attrs):
        result = adapter.normalize(fi_native_llm_attrs)
        assert "input.value" in result
        assert "output.value" in result

    def test_source_name(self, adapter):
        assert adapter.source_name == "traceai"


@pytest.mark.unit
class TestFiNativeSpanKindNormalization:
    def test_normalizes_fi_span_kind_to_gen_ai(self, adapter):
        attrs = {"fi.span.kind": "CHAIN"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "CHAIN"
        assert result["fi.span.kind"] == "CHAIN"  # preserved

    def test_does_not_overwrite_existing_gen_ai_span_kind(self, adapter):
        attrs = {"fi.span.kind": "CHAIN", "gen_ai.span.kind": "LLM"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "LLM"  # not overwritten

    def test_normalizes_all_span_kinds(self, adapter):
        for kind in ("LLM", "CHAIN", "AGENT", "TOOL", "RETRIEVER", "EMBEDDING"):
            attrs = {"fi.span.kind": kind}
            result = adapter.normalize(attrs)
            assert result["gen_ai.span.kind"] == kind


@pytest.mark.unit
class TestFiNativeIONormalization:
    def test_normalizes_fi_llm_input_to_input_value(self, adapter):
        attrs = {
            "fi.span.kind": "LLM",
            "fi.llm.input": json.dumps({"query": "hello"}),
        }
        result = adapter.normalize(attrs)
        assert result["input.value"] == json.dumps({"query": "hello"})
        assert result["input.mime_type"] == "application/json"

    def test_normalizes_fi_llm_output_to_output_value(self, adapter):
        attrs = {
            "fi.span.kind": "LLM",
            "fi.llm.output": json.dumps({"answer": "world"}),
        }
        result = adapter.normalize(attrs)
        assert result["output.value"] == json.dumps({"answer": "world"})
        assert result["output.mime_type"] == "application/json"

    def test_does_not_overwrite_existing_input_value(self, adapter):
        attrs = {
            "fi.span.kind": "CHAIN",
            "fi.llm.input": json.dumps({"old": True}),
            "input.value": "my input",
            "input.mime_type": "text/plain",
        }
        result = adapter.normalize(attrs)
        assert result["input.value"] == "my input"
        assert result["input.mime_type"] == "text/plain"

    def test_infers_json_mime_type(self, adapter):
        attrs = {
            "fi.span.kind": "CHAIN",
            "input.value": json.dumps({"key": "val"}),
        }
        result = adapter.normalize(attrs)
        assert result["input.mime_type"] == "application/json"

    def test_infers_text_mime_type(self, adapter):
        attrs = {
            "fi.span.kind": "CHAIN",
            "input.value": "plain text input",
        }
        result = adapter.normalize(attrs)
        assert result["input.mime_type"] == "text/plain"

    def test_preserves_existing_mime_type(self, adapter):
        attrs = {
            "fi.span.kind": "CHAIN",
            "input.value": "some text",
            "input.mime_type": "text/plain",
        }
        result = adapter.normalize(attrs)
        assert result["input.mime_type"] == "text/plain"

    def test_chain_span_with_io(self, adapter, fi_native_chain_attrs):
        result = adapter.normalize(fi_native_chain_attrs)
        assert result["gen_ai.span.kind"] == "CHAIN"
        assert result["input.value"] is not None
        assert result["output.value"] is not None
        assert result["input.mime_type"] == "application/json"

    def test_chain_span_without_io(self, adapter, fi_native_chain_no_io_attrs):
        result = adapter.normalize(fi_native_chain_no_io_attrs)
        assert result["gen_ai.span.kind"] == "CHAIN"
        assert "input.value" not in result
        assert "output.value" not in result
