"""Unit tests for LangfuseAdapter."""

import json

import pytest

from tracer.utils.adapters.langfuse import LangfuseAdapter


@pytest.fixture
def adapter():
    return LangfuseAdapter()


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseDetect:
    def test_detect_with_observation_type(self, adapter):
        assert adapter.detect({"langfuse.observation.type": "generation"}) is True

    def test_detect_with_langfuse_prefix_only(self, adapter):
        assert adapter.detect({"langfuse.environment": "dev"}) is True

    def test_detect_no_langfuse_keys(self, adapter):
        assert adapter.detect({"fi.span.kind": "LLM"}) is False

    def test_detect_empty(self, adapter):
        assert adapter.detect({}) is False


# ---------------------------------------------------------------------------
# normalize() — generation span
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseNormalizeGeneration:
    def test_sets_span_kind_llm(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["gen_ai.span.kind"] == "LLM"

    def test_sets_model_name(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["llm.model_name"] == "gpt-4o-mini"

    def test_sets_provider(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["llm.provider"] == "openai"
        assert result["llm.system"] == "openai"

    def test_sets_token_counts(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["llm.token_count.prompt"] == 22
        assert result["llm.token_count.completion"] == 4
        assert result["llm.token_count.total"] == 26

    def test_total_computed_when_missing(self, adapter):
        """When total_tokens is absent from usage, it should be computed."""
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "gpt-4o",
            "langfuse.observation.usage_details": json.dumps(
                {"input_tokens": 10, "output_tokens": 5}
            ),
        }
        result = adapter.normalize(attrs)
        assert result["llm.token_count.total"] == 15

    def test_sets_io_values(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert "input.value" in result
        assert "output.value" in result
        assert result["input.mime_type"] == "application/json"
        assert result["output.mime_type"] == "application/json"

    def test_flattens_input_messages(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["llm.input_messages.0.message.role"] == "system"
        assert (
            result["llm.input_messages.0.message.content"]
            == "You are a helpful assistant."
        )
        assert result["llm.input_messages.1.message.role"] == "user"
        assert result["llm.input_messages.1.message.content"] == "Say hello in 3 words."

    def test_flattens_output_messages(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["llm.output_messages.0.message.role"] == "assistant"
        assert result["llm.output_messages.0.message.content"] == "Hello to you!"

    def test_extracts_query(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["query"] == "Say hello in 3 words."

    def test_sets_response(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["response"] == "Hello to you!"

    def test_sets_raw_io(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert "raw.input" in result
        assert "raw.output" in result

    def test_sets_model_params(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        params = result["llm.invocation_parameters"]
        assert params["max_tokens"] == 20
        assert params["temperature"] == 0.7

    def test_strips_langfuse_keys(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert not any(k.startswith("langfuse.") for k in result)

    def test_sets_trace_source(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["gen_ai.trace.source"] == "langfuse"


# ---------------------------------------------------------------------------
# normalize() — span types
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseSpanTypes:
    def test_span_type_maps_to_chain(self, adapter, langfuse_chain_attrs):
        result = adapter.normalize(langfuse_chain_attrs)
        assert result["gen_ai.span.kind"] == "CHAIN"

    def test_event_type(self, adapter):
        attrs = {"langfuse.observation.type": "event"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "UNKNOWN"

    def test_agent_type(self, adapter):
        attrs = {"langfuse.observation.type": "agent"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "AGENT"

    def test_embedding_type(self, adapter):
        attrs = {"langfuse.observation.type": "embedding"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "EMBEDDING"

    def test_tool_type(self, adapter):
        attrs = {"langfuse.observation.type": "tool"}
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "TOOL"

    def test_no_type_but_model_defaults_to_llm(self, adapter):
        attrs = {
            "langfuse.observation.model.name": "gpt-4o",
        }
        result = adapter.normalize(attrs)
        assert result["gen_ai.span.kind"] == "LLM"

    def test_no_type_no_model_no_span_kind(self, adapter):
        attrs = {"langfuse.environment": "test"}
        result = adapter.normalize(attrs)
        assert "gen_ai.span.kind" not in result


# ---------------------------------------------------------------------------
# normalize() — metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseMetadata:
    def test_metadata_from_json_string(self, adapter):
        attrs = {
            "langfuse.observation.type": "span",
            "langfuse.observation.metadata": json.dumps({"key": "value"}),
        }
        result = adapter.normalize(attrs)
        assert result["metadata"] == {"key": "value"}

    def test_metadata_from_flattened_keys(self, adapter):
        attrs = {
            "langfuse.observation.type": "span",
            "langfuse.observation.metadata.key1": "val1",
            "langfuse.observation.metadata.key2": "val2",
        }
        result = adapter.normalize(attrs)
        assert result["metadata"] == {"key1": "val1", "key2": "val2"}


# ---------------------------------------------------------------------------
# normalize() — tags, user, session
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseTagsUserSession:
    def test_tags(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["tag.tags"] == ["test", "unit"]

    def test_user_id(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["user.id"] == "user-42"

    def test_session_id(self, adapter, langfuse_generation_attrs):
        result = adapter.normalize(langfuse_generation_attrs)
        assert result["session.id"] == "sess-abc"

    def test_langfuse_user_id_compat(self, adapter):
        """langfuse.user.id compat alias should work."""
        attrs = {"langfuse.observation.type": "span", "langfuse.user.id": "u-compat"}
        result = adapter.normalize(attrs)
        assert result["user.id"] == "u-compat"

    def test_langfuse_session_id_compat(self, adapter):
        attrs = {"langfuse.observation.type": "span", "langfuse.session.id": "s-compat"}
        result = adapter.normalize(attrs)
        assert result["session.id"] == "s-compat"


# ---------------------------------------------------------------------------
# normalize() — prompt template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfusePromptTemplate:
    def test_prompt_name_and_version(self, adapter):
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.prompt.name": "my-prompt",
            "langfuse.observation.prompt.version": "3",
        }
        result = adapter.normalize(attrs)
        assert result["gen_ai.prompt.template.name"] == "my-prompt"
        assert result["gen_ai.prompt.template.version"] == "3"


# ---------------------------------------------------------------------------
# normalize() — tool calls
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseToolCalls:
    def test_output_with_tool_calls(self, adapter, tool_call_data):
        output_msg = tool_call_data["output_message"]
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "gpt-4o",
            "langfuse.observation.output": json.dumps(output_msg),
            "langfuse.observation.input": json.dumps(tool_call_data["input_messages"]),
        }
        result = adapter.normalize(attrs)
        assert (
            result["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"]
            == "get_weather"
        )
        assert (
            result["llm.output_messages.0.message.tool_calls.0.tool_call.id"]
            == "call_abc123"
        )


# ---------------------------------------------------------------------------
# normalize() — provider variants
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseProviders:
    def test_anthropic_provider(self, adapter):
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "claude-3.5-sonnet",
        }
        result = adapter.normalize(attrs)
        assert result["llm.provider"] == "anthropic"

    def test_google_provider(self, adapter):
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "gemini-2.0-flash",
        }
        result = adapter.normalize(attrs)
        assert result["llm.provider"] == "google"

    def test_unknown_provider(self, adapter):
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "custom-model-v1",
        }
        result = adapter.normalize(attrs)
        assert "llm.provider" not in result


# ---------------------------------------------------------------------------
# normalize() — usage key variants
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLangfuseUsageVariants:
    def test_input_output_keys(self, adapter):
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "gpt-4o",
            "langfuse.observation.usage_details": json.dumps(
                {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            ),
        }
        result = adapter.normalize(attrs)
        assert result["llm.token_count.prompt"] == 10
        assert result["llm.token_count.completion"] == 5
        assert result["llm.token_count.total"] == 15

    def test_input_output_shorthand(self, adapter):
        """Langfuse also supports 'input'/'output' shorthand."""
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "gpt-4o",
            "langfuse.observation.usage_details": json.dumps(
                {"input": 10, "output": 5}
            ),
        }
        result = adapter.normalize(attrs)
        assert result["llm.token_count.prompt"] == 10
        assert result["llm.token_count.completion"] == 5
        assert result["llm.token_count.total"] == 15  # Computed

    def test_source_name(self, adapter):
        assert adapter.source_name == "langfuse"
