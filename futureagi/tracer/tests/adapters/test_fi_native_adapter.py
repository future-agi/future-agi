"""Unit tests for FiNativeAdapter."""

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
