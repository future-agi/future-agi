import uuid
import pytest
from rest_framework import serializers

from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    PayloadBuilder,
)
from model_hub.models.openai_tools import (
    ensure_openai_tool_envelope,
    openai_tool_envelope,
)
from model_hub.serializers.run_prompt import LitellmSerializer, PromptConfigSerializer
from model_hub.views.run_prompt import _extract_tool_ids


SAMPLE_PARAMETERS = {
    "type": "object",
    "properties": {
        "location": {"type": "string", "description": "City name"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
    },
    "required": ["location"],
}


class TestOpenAiToolEnvelope:
    """Unit tests for openai_tool_envelope logic."""

    def test_wraps_bare_schema_with_parameters_key(self):
        result = openai_tool_envelope(
            name="get_weather",
            description="Get current weather",
            config={"parameters": SAMPLE_PARAMETERS},
        )
        assert result == {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": SAMPLE_PARAMETERS,
            },
        }

    def test_wraps_bare_parameters_directly(self):
        result = openai_tool_envelope(
            name="get_weather",
            description="Get current weather",
            config=SAMPLE_PARAMETERS,
        )
        assert result["type"] == "function"
        assert result["function"]["name"] == "get_weather"
        assert result["function"]["parameters"] == SAMPLE_PARAMETERS

    def test_preserves_already_wrapped_envelope(self):
        already_wrapped = {
            "type": "function",
            "function": {
                "name": "custom_func",
                "description": "Custom desc",
                "parameters": SAMPLE_PARAMETERS,
            },
        }
        result = openai_tool_envelope("fallback_name", "fallback_desc", already_wrapped)
        assert result == already_wrapped

    def test_fills_blank_wrapped_name_and_desc_from_args(self):
        wrapped_with_empty_meta = {
            "type": "function",
            "function": {
                "name": "",
                "description": "",
                "parameters": SAMPLE_PARAMETERS,
            },
        }
        result = openai_tool_envelope(
            "fallback_name", "fallback_desc", wrapped_with_empty_meta
        )
        assert result["function"]["name"] == "fallback_name"
        assert result["function"]["description"] == "fallback_desc"

    def test_sanitizes_tool_identifier(self):
        result = openai_tool_envelope(
            name="Get Weather (v2.0)!",
            description="desc",
            config={"parameters": SAMPLE_PARAMETERS},
        )
        assert result["function"]["name"] == "Get_Weather_v2_0"

    def test_substitutes_empty_object_when_empty_parameters(self):
        result = openai_tool_envelope(
            name="ping",
            description="Ping health",
            config={"parameters": {}},
        )
        assert result["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    def test_keeps_schema_with_no_top_level_properties(self):
        ref_schema = {"type": "object", "$ref": "#/definitions/Payload"}
        result = openai_tool_envelope(
            name="lookup",
            description="Lookup",
            config={"parameters": ref_schema},
        )
        assert result["function"]["parameters"] == ref_schema

    def test_preserves_malformed_parameters_without_silent_coercion(self):
        malformed = {"parameters": "not-a-dict"}
        result = openai_tool_envelope("func", "desc", malformed)
        assert result == malformed

    def test_preserves_malformed_config_without_silent_coercion(self):
        malformed = {"random_key": "some_value"}
        result = openai_tool_envelope("func", "desc", malformed)
        assert result == malformed

    def test_returns_non_dict_config_as_is(self):
        assert openai_tool_envelope("func", "desc", "not-a-dict") == "not-a-dict"
        assert openai_tool_envelope("func", "desc", None) is None


class TestEnsureOpenAiToolEnvelope:
    """Unit tests for ensure_openai_tool_envelope."""

    def test_handles_already_wrapped_envelope(self):
        envelope = {
            "type": "function",
            "function": {
                "name": "search",
                "description": "search tool",
                "parameters": SAMPLE_PARAMETERS,
            },
        }
        assert ensure_openai_tool_envelope(envelope) == envelope

    def test_handles_nested_config(self):
        tool_data = {
            "name": "search",
            "description": "search tool",
            "config": {"parameters": SAMPLE_PARAMETERS},
        }
        enveloped = ensure_openai_tool_envelope(tool_data)
        assert enveloped["type"] == "function"
        assert enveloped["function"]["name"] == "search"
        assert enveloped["function"]["description"] == "search tool"
        assert enveloped["function"]["parameters"] == SAMPLE_PARAMETERS

    def test_handles_bare_parameters_with_top_level_meta(self):
        tool_data = {
            "name": "search",
            "description": "search tool",
            "parameters": SAMPLE_PARAMETERS,
        }
        enveloped = ensure_openai_tool_envelope(tool_data)
        assert enveloped["type"] == "function"
        assert enveloped["function"]["name"] == "search"
        assert enveloped["function"]["parameters"] == SAMPLE_PARAMETERS

    def test_handles_bare_parameters_schema_without_name(self):
        schema = {"parameters": SAMPLE_PARAMETERS}
        enveloped = ensure_openai_tool_envelope(
            schema, name="default_tool", description="default_desc"
        )
        assert enveloped["type"] == "function"
        assert enveloped["function"]["name"] == "default_tool"
        assert enveloped["function"]["parameters"] == SAMPLE_PARAMETERS

    def test_handles_tools_model_instance(self):
        class MockToolsModel:
            def as_openai_tool(self):
                return {
                    "type": "function",
                    "function": {
                        "name": "mocked",
                        "description": "mocked tool",
                        "parameters": SAMPLE_PARAMETERS,
                    },
                }

        mock_tool = MockToolsModel()
        result = ensure_openai_tool_envelope(mock_tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "mocked"

    def test_preserves_malformed_tool_dict(self):
        malformed = {"broken": "data"}
        assert ensure_openai_tool_envelope(malformed) == malformed

    def test_preserves_non_dict_tool(self):
        assert ensure_openai_tool_envelope("string-tool-id") == "string-tool-id"
        assert ensure_openai_tool_envelope(None) is None


class TestExtractToolIds:
    """Unit tests for _extract_tool_ids utility."""

    def test_extracts_string_uuids(self):
        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        assert _extract_tool_ids([u1, u2]) == [u1, u2]

    def test_extracts_from_dict_with_id(self):
        u1 = str(uuid.uuid4())
        assert _extract_tool_ids([{"id": u1}]) == [u1]

    def test_extracts_from_frontend_config_tool_shape(self):
        u1 = str(uuid.uuid4())
        assert _extract_tool_ids([{"tool": {"value": u1}}]) == [u1]

    def test_extracts_from_frontend_shorthand_shape(self):
        u1 = str(uuid.uuid4())
        assert _extract_tool_ids([{"tool": u1}]) == [u1]

    def test_ignores_bare_tool_definitions(self):
        bare = {"name": "test", "parameters": SAMPLE_PARAMETERS}
        assert _extract_tool_ids([bare]) == []

    def test_handles_mixed_list(self):
        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        u3 = str(uuid.uuid4())
        u4 = str(uuid.uuid4())
        mixed = [
            u1,
            {"id": u2},
            {"tool": {"value": u3}},
            {"tool": u4},
            {"name": "inline_tool", "parameters": SAMPLE_PARAMETERS},
        ]
        assert _extract_tool_ids(mixed) == [u1, u2, u3, u4]


class TestRunPromptSerializersToolValidation:
    """Validates that PromptConfigSerializer and LitellmSerializer accept string tool IDs and dict schemas."""

    def test_prompt_config_serializer_accepts_string_tool_ids(self):
        tool_id = str(uuid.uuid4())
        serializer = PromptConfigSerializer(
            data={"model": "gpt-4o", "tools": [tool_id]}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["tools"] == [tool_id]

    def test_prompt_config_serializer_accepts_tool_dicts(self):
        tool_dict = {"name": "calc", "parameters": SAMPLE_PARAMETERS}
        serializer = PromptConfigSerializer(
            data={"model": "gpt-4o", "tools": [tool_dict]}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["tools"] == [tool_dict]

    def test_prompt_config_serializer_accepts_mixed_tools(self):
        tool_id = str(uuid.uuid4())
        tool_dict = {"name": "calc", "parameters": SAMPLE_PARAMETERS}
        serializer = PromptConfigSerializer(
            data={"model": "gpt-4o", "tools": [tool_id, tool_dict]}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["tools"] == [tool_id, tool_dict]

    def test_litellm_serializer_accepts_string_and_dict_tools(self):
        tool_id = str(uuid.uuid4())
        tool_dict = {"type": "function", "function": {"name": "test"}}
        serializer = LitellmSerializer(
            data={
                "dataset_id": "ds-1",
                "model": "gpt-4o",
                "name": "test-prompt",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [tool_id, tool_dict],
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["tools"] == [tool_id, tool_dict]

    def test_serializers_reject_invalid_tool_element_types(self):
        serializer = PromptConfigSerializer(
            data={"model": "gpt-4o", "tools": [123, True, [1, 2]]}
        )
        assert not serializer.is_valid()
        assert "tools" in serializer.errors


class TestRunPromptPayloadCreation:
    """Tests that RunPrompt and PayloadBuilder envelope bare tools when constructing litellm payloads."""

    def test_run_prompt_envelopes_bare_schema_in_payload(self):
        bare_tool = {
            "name": "weather_lookup",
            "description": "Check temperature",
            "parameters": SAMPLE_PARAMETERS,
        }
        rp = RunPrompt(
            model="gpt-4o",
            messages=[{"role": "user", "content": "weather"}],
            organization_id="org-123",
            output_format="string",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice="auto",
            tools=[bare_tool],
        )
        payload = rp._create_payload("openai", "dummy-key")
        assert "tools" in payload
        assert len(payload["tools"]) == 1
        assert payload["tools"][0] == {
            "type": "function",
            "function": bare_tool,
        }

    def test_run_prompt_preserves_already_enveloped_tools(self):
        enveloped_tool = {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "desc",
                "parameters": SAMPLE_PARAMETERS,
            },
        }
        rp = RunPrompt(
            model="gpt-4o",
            messages=[{"role": "user", "content": "weather"}],
            organization_id="org-123",
            output_format="string",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice="auto",
            tools=[enveloped_tool],
        )
        payload = rp._create_payload("openai", "dummy-key")
        assert payload["tools"][0] == enveloped_tool

    def test_payload_builder_envelopes_bare_tools(self):
        class MockContext:
            model = "gpt-4o"
            messages = [{"role": "user", "content": "hi"}]
            temperature = 0.5
            frequency_penalty = 0.0
            presence_penalty = 0.0
            max_tokens = 100
            top_p = 1.0
            response_format = None
            tool_choice = "auto"
            tools = [
                {
                    "name": "calc",
                    "description": "calculator",
                    "parameters": SAMPLE_PARAMETERS,
                }
            ]
            stop = None
            seed = None
            metadata = None
            organization_id = "org-123"
            extra_body = None
            custom_llm_provider = None
            reasoning_effort = None
            thinking_budget = None

        payload = PayloadBuilder.build_llm_payload(MockContext(), "openai", "test-key")
        assert payload["tools"][0]["type"] == "function"
        assert payload["tools"][0]["function"]["name"] == "calc"
        assert payload["tools"][0]["function"]["parameters"] == SAMPLE_PARAMETERS
