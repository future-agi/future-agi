"""Unit tests for OpenLLMetryAdapter."""

import json
import re

import pytest

from tracer.utils.adapters.openllmetry import (
    OpenLLMetryAdapter,
    _extract_indexed_messages,
)

_PROMPT_RE = re.compile(r"^gen_ai\.prompt\.(\d+)\.(.+)$")
_COMPLETION_RE = re.compile(r"^gen_ai\.completion\.(\d+)\.(.+)$")


@pytest.fixture
def adapter():
    return OpenLLMetryAdapter()


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenLLMetryDetect:
    def test_detect_with_gen_ai_no_traceloop(self, adapter):
        # gen_ai.* without traceloop.* is now handled by OtelGenAIAdapter
        assert adapter.detect({"gen_ai.system": "openai"}) is False

    def test_detect_with_traceloop(self, adapter):
        assert adapter.detect({"traceloop.span.kind": "workflow"}) is True

    def test_detect_without_markers(self, adapter):
        assert adapter.detect({"fi.span.kind": "LLM"}) is False

    def test_detect_empty(self, adapter):
        assert adapter.detect({}) is False


# ---------------------------------------------------------------------------
# _extract_indexed_messages
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractIndexedMessages:
    def test_basic_prompt_messages(self):
        attrs = {
            "gen_ai.prompt.0.role": "system",
            "gen_ai.prompt.0.content": "You are helpful.",
            "gen_ai.prompt.1.role": "user",
            "gen_ai.prompt.1.content": "Hello",
        }
        msgs = _extract_indexed_messages(attrs, _PROMPT_RE)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are helpful."
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "Hello"

    def test_completion_messages(self):
        attrs = {
            "gen_ai.completion.0.role": "assistant",
            "gen_ai.completion.0.content": "Hi there!",
            "gen_ai.completion.0.finish_reason": "stop",
        }
        msgs = _extract_indexed_messages(attrs, _COMPLETION_RE)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "Hi there!"
        assert msgs[0]["finish_reason"] == "stop"

    def test_json_content_parsed(self):
        """Content that is a JSON string should be parsed."""
        content = json.dumps([{"type": "text", "text": "Hello"}])
        attrs = {
            "gen_ai.prompt.0.role": "user",
            "gen_ai.prompt.0.content": content,
        }
        msgs = _extract_indexed_messages(attrs, _PROMPT_RE)
        assert isinstance(msgs[0]["content"], list)
        assert msgs[0]["content"][0]["type"] == "text"

    def test_non_json_content_preserved(self):
        attrs = {
            "gen_ai.prompt.0.role": "user",
            "gen_ai.prompt.0.content": "plain text",
        }
        msgs = _extract_indexed_messages(attrs, _PROMPT_RE)
        assert msgs[0]["content"] == "plain text"

    def test_empty_attrs(self):
        assert _extract_indexed_messages({}, _PROMPT_RE) == []

    def test_sparse_indices(self):
        attrs = {
            "gen_ai.prompt.0.role": "user",
            "gen_ai.prompt.0.content": "first",
            "gen_ai.prompt.5.role": "assistant",
            "gen_ai.prompt.5.content": "fifth",
        }
        msgs = _extract_indexed_messages(attrs, _PROMPT_RE)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "first"
        assert msgs[1]["content"] == "fifth"

    def test_tool_calls_indexed(self):
        attrs = {
            "gen_ai.prompt.0.role": "assistant",
            "gen_ai.prompt.0.content": "",
            "gen_ai.prompt.0.tool_calls.0.function.name": "search",
            "gen_ai.prompt.0.tool_calls.0.function.arguments": "{}",
        }
        msgs = _extract_indexed_messages(attrs, _PROMPT_RE)
        assert msgs[0]["tool_calls.0.function.name"] == "search"


# ---------------------------------------------------------------------------
# normalize() — LLM span
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenLLMetryNormalizeLLM:
    def test_span_kind_from_operation(self, adapter):
        attrs = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-4o"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "LLM"

    def test_span_kind_from_request_type(self, adapter):
        attrs = {"llm.request.type": "chat", "gen_ai.request.model": "gpt-4o"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "LLM"

    def test_model_response_takes_priority(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["llm.model_name"] == "gpt-4o-mini-2024-07-18"

    def test_model_request_fallback(self, adapter):
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.system": "openai",
        }
        result = adapter.normalize(attrs)
        assert result["llm.model_name"] == "gpt-4o"

    def test_provider_from_system(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["llm.provider"] == "openai"
        assert result["llm.system"] == "openai"

    def test_provider_anthropic(self, adapter):
        attrs = {
            "gen_ai.system": "Anthropic",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "claude-3",
        }
        result = adapter.normalize(attrs)
        assert result["llm.provider"] == "anthropic"

    def test_provider_azure(self, adapter):
        attrs = {
            "gen_ai.system": "azure",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4",
        }
        result = adapter.normalize(attrs)
        assert result["llm.provider"] == "azure_openai"

    def test_token_counts(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["llm.token_count.prompt"] == 22
        assert result["llm.token_count.completion"] == 4

    def test_token_counts_legacy_format(self, adapter):
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.prompt_tokens": 100,
            "gen_ai.usage.completion_tokens": 50,
        }
        result = adapter.normalize(attrs)
        assert result["llm.token_count.prompt"] == 100
        assert result["llm.token_count.completion"] == 50

    def test_total_computed_when_missing(self, adapter):
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 5,
        }
        result = adapter.normalize(attrs)
        assert result["llm.token_count.total"] == 15

    def test_invocation_params(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        params = result["llm.invocation_parameters"]
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 20

    def test_io_values_set(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert "input.value" in result
        assert "output.value" in result
        assert result["input.mime_type"] == "application/json"

    def test_flatten_input_messages(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["llm.input_messages.0.message.role"] == "system"
        assert result["llm.input_messages.1.message.role"] == "user"

    def test_flatten_output_messages(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["llm.output_messages.0.message.role"] == "assistant"
        assert result["llm.output_messages.0.message.content"] == "Hello to you!"

    def test_extracts_query(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["query"] == "Say hello in 3 words."

    def test_extracts_response(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["response"] == "Hello to you!"

    def test_sets_raw_io(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert "raw.input" in result
        assert "raw.output" in result


# ---------------------------------------------------------------------------
# normalize() — non-LLM spans
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenLLMetryNonLLM:
    def test_workflow_span(self, adapter, openllmetry_workflow_attrs):
        result = adapter.normalize(openllmetry_workflow_attrs)
        assert result["gen_ai.span.kind"] == "CHAIN"

    def test_agent_span(self, adapter):
        attrs = {"traceloop.span.kind": "agent"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "AGENT"

    def test_tool_span(self, adapter, openllmetry_tool_attrs):
        result = adapter.normalize(openllmetry_tool_attrs)
        assert result["gen_ai.span.kind"] == "TOOL"

    def test_entity_io(self, adapter, openllmetry_workflow_attrs):
        result = adapter.normalize(openllmetry_workflow_attrs)
        assert "input.value" in result
        assert "output.value" in result

    def test_embedding_operation(self, adapter):
        attrs = {"gen_ai.operation.name": "embedding"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "EMBEDDING"

    def test_rerank_operation(self, adapter):
        attrs = {"gen_ai.operation.name": "rerank"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "RERANKER"


# ---------------------------------------------------------------------------
# normalize() — key stripping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenLLMetryStripping:
    def test_strips_gen_ai_keys(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        # gen_ai.span.kind is set by the adapter after stripping; all other gen_ai.* keys should be gone
        remaining_gen_ai = [
            k
            for k in result
            if k.startswith("gen_ai.")
            and k not in ("gen_ai.span.kind", "gen_ai.trace.source")
        ]
        assert not remaining_gen_ai

    def test_strips_traceloop_keys(self, adapter, openllmetry_workflow_attrs):
        result = adapter.normalize(openllmetry_workflow_attrs)
        assert not any(k.startswith("traceloop.") for k in result)

    def test_strips_traceloop_llm_keys(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert "llm.request.type" not in result
        assert "llm.is_streaming" not in result

    def test_preserves_adapter_set_llm_keys(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert "llm.model_name" in result
        assert "llm.token_count.prompt" in result


# ---------------------------------------------------------------------------
# normalize() — metadata and prompt template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenLLMetryMetadataPrompt:
    def test_association_props_to_metadata(self, adapter, openllmetry_workflow_attrs):
        result = adapter.normalize(openllmetry_workflow_attrs)
        assert result["metadata"] == {"user_id": "u-1", "session": "s-1"}

    def test_prompt_template(self, adapter):
        attrs = {
            "gen_ai.system": "openai",
            "traceloop.prompt.key": "my-prompt",
            "traceloop.prompt.version": "2",
            "traceloop.prompt.template": "Hello {{name}}",
            "traceloop.prompt.template_variables": json.dumps({"name": "world"}),
        }
        result = adapter.normalize(attrs)
        assert result["gen_ai.prompt.template.name"] == "my-prompt"
        assert result["gen_ai.prompt.template.version"] == "2"
        assert result["llm.prompt.template"] == "Hello {{name}}"
        assert result["gen_ai.prompt.template.variables"] == {"name": "world"}


# ---------------------------------------------------------------------------
# normalize() — trace source
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenLLMetryTraceSource:
    def test_sets_trace_source(self, adapter, openllmetry_llm_attrs):
        result = adapter.normalize(openllmetry_llm_attrs)
        assert result["gen_ai.trace.source"] == "openllmetry"

    def test_source_name(self, adapter):
        assert adapter.source_name == "openllmetry"


# ---------------------------------------------------------------------------
# normalize() — span kind with model fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenLLMetrySpanKindFallback:
    def test_model_only_defaults_to_llm(self, adapter):
        attrs = {"gen_ai.request.model": "gpt-4o"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "LLM"

    def test_no_kind_no_model(self, adapter):
        attrs = {"gen_ai.system": "openai"}
        result = adapter.normalize(attrs)
        assert "gen_ai.span.kind" not in result
