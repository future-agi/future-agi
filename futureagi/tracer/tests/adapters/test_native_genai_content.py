"""Native GenAI content contract, including the Anthropic C# 12.56/13.3 schema."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.otel import _convert_single_span


def convert(attributes, monkeypatch):
    monkeypatch.setattr("tracer.utils.otel.calculate_cost_from_tokens", lambda **_: 0)
    monkeypatch.setattr("tracer.utils.otel.calculate_cost", lambda **_: 0)
    span = {
        "attributes": deepcopy(attributes),
        "name": "native contract",
        "project_name": "native-contract",
        "project_type": "observe",
    }
    normalize_span_attributes([span])
    return _convert_single_span(
        span,
        {
            ("native-contract", "synthetic-org", "observe"): SimpleNamespace(
                id="project"
            )
        },
        {},
        "synthetic-org",
    )


def chat(**attributes):
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "claude-test",
        **attributes,
    }


@pytest.mark.unit
def test_native_multipart_and_system_content(monkeypatch):
    parts = [
        {"type": "text", "content": "PROMPT_SENTINEL"},
        {"type": "anthropic_image"},
        {"type": "anthropic_document"},
        {"type": "future_part", "data": {"synthetic": True}},
    ]
    attrs = chat(
        **{
            "gen_ai.system_instructions": json.dumps(
                [{"type": "text", "content": "SYSTEM_SENTINEL"}]
            ),
            "gen_ai.input.messages": json.dumps([{"role": "user", "parts": parts}]),
            "gen_ai.output.messages": json.dumps(
                [
                    {
                        "role": "assistant",
                        "parts": [{"type": "text", "content": "RESPONSE_SENTINEL"}],
                    }
                ]
            ),
            "gen_ai.usage.input_tokens": 12,
            "gen_ai.usage.cache_read.input_tokens": 3,
            "gen_ai.usage.cache_creation.input_tokens": 2,
            "gen_ai.usage.output_tokens": 5,
        }
    )
    original = deepcopy(attrs)
    obs = convert(attrs, monkeypatch)["observation_span"]
    assert obs["input"] == [
        {"role": "system", "content": [{"type": "text", "text": "SYSTEM_SENTINEL"}]},
        {
            "role": "user",
            "content": [{"type": "text", "text": "PROMPT_SENTINEL"}, *parts[1:]],
        },
    ]
    assert obs["output"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "RESPONSE_SENTINEL"}],
    }
    assert (obs["prompt_tokens"], obs["completion_tokens"], obs["total_tokens"]) == (
        12,
        5,
        17,
    )
    assert obs["model"] == "claude-test" and obs["provider"] == "anthropic"
    assert attrs == original  # Never mutate nested caller data.
    assert obs["span_attributes"]["gen_ai.input.messages"] == json.loads(
        original["gen_ai.input.messages"]
    )


@pytest.mark.unit
def test_native_tool_messages_keep_arguments_results_and_order(monkeypatch):
    call = {"type": "tool_call", "id": "call-1", "name": "lookup", "arguments": {}}
    result = {
        "type": "tool_call_response",
        "id": "call-1",
        "response": [{"type": "text", "text": "TOOL_RESULT_SENTINEL"}],
    }
    attrs = chat(
        **{
            "gen_ai.input.messages": [
                {"role": "assistant", "parts": [call]},
                {"role": "user", "parts": [result]},
            ],
            "gen_ai.output.messages": [{"role": "assistant", "parts": [call]}],
        }
    )
    obs = convert(attrs, monkeypatch)["observation_span"]
    assert obs["input"] == [
        {"role": "assistant", "content": [call]},
        {"role": "user", "content": [result]},
    ]
    assert obs["output"] == {"role": "assistant", "content": [call]}


@pytest.mark.unit
@pytest.mark.parametrize(
    "conversation", ["conversation-a", "conversation-b", "conversation-a"]
)
def test_native_conversation_reaches_converter_session(conversation, monkeypatch):
    attrs = chat(**{"gen_ai.conversation.id": conversation})
    assert convert(attrs, monkeypatch)["session_name"] == conversation
    attrs["session.id"] = "explicit-session"
    assert convert(attrs, monkeypatch)["session_name"] == "explicit-session"


@pytest.mark.unit
def test_native_runner_is_not_an_extra_model_call(monkeypatch):
    attrs = chat(**{"gen_ai.operation.name": "invoke_agent"})
    obs = convert(attrs, monkeypatch)["observation_span"]
    assert obs["observation_type"] == "agent"
    assert obs["total_tokens"] is None
    attrs["gen_ai.span.kind"] = "CHAIN"
    assert (
        convert(attrs, monkeypatch)["observation_span"]["observation_type"] == "chain"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "output_key", ["anthropic.tool_result.content", "gen_ai.tool.call.result"]
)
@pytest.mark.parametrize(
    "arguments,result",
    [
        ({}, []),
        ({"query": "synthetic"}, [{"type": "text", "text": "TOOL_RESULT_SENTINEL"}]),
        (False, 0),
    ],
)
def test_native_tool_io_including_empty_and_false_values(
    output_key, arguments, result, monkeypatch
):
    attrs = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.call.arguments": json.dumps(arguments),
        output_key: json.dumps(result),
    }
    obs = convert(attrs, monkeypatch)["observation_span"]
    assert obs["input"] == arguments
    assert obs["output"] == result


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["chat", "invoke_agent", "execute_tool"])
def test_metadata_only_never_invents_content(operation, monkeypatch):
    obs = convert(chat(**{"gen_ai.operation.name": operation}), monkeypatch)[
        "observation_span"
    ]
    assert obs["input"] is None and obs["output"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "value", ["{broken", "null", "42", "{}", "[]", [None, 5, "text"]]
)
def test_invalid_or_empty_modern_messages_do_not_fall_back_to_stale_content(
    value, monkeypatch
):
    attrs = chat(
        **{
            "gen_ai.input.messages": value,
            "gen_ai.output.messages": value,
            "gen_ai.prompt.0.content": "STALE_INPUT",
            "gen_ai.completion.0.content": "STALE_OUTPUT",
        }
    )
    obs = convert(attrs, monkeypatch)["observation_span"]
    assert obs["input"] is None and obs["output"] is None


@pytest.mark.unit
def test_legacy_indexed_messages_still_work(monkeypatch):
    obs = convert(
        chat(
            **{
                "gen_ai.prompt.0.content": "legacy input",
                "gen_ai.completion.0.content": "legacy output",
                "gen_ai.completion.0.role": "assistant",
            }
        ),
        monkeypatch,
    )["observation_span"]
    assert obs["input"] == [{"role": "user", "content": "legacy input"}]
    assert obs["output"] == {"role": "assistant", "content": "legacy output"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "input_key,output_key",
    [
        ("input.value", "output.value"),
        ("fi.llm.input", "fi.llm.output"),
        ("raw.input", "raw.output"),
    ],
)
@pytest.mark.parametrize("value", ["explicit", "", "[]"])
@pytest.mark.parametrize("operation", ["chat", "execute_tool"])
def test_explicit_legacy_io_wins_over_native_content(
    input_key, output_key, value, operation, monkeypatch
):
    attrs = chat(
        **{
            input_key: value,
            output_key: value,
            "gen_ai.operation.name": operation,
            "gen_ai.system_instructions": [
                {"type": "text", "content": "native system"}
            ],
            "gen_ai.input.messages": [
                {"role": "user", "parts": [{"type": "text", "content": "native input"}]}
            ],
            "gen_ai.output.messages": [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": "native output"}],
                }
            ],
            "gen_ai.tool.call.arguments": "native input",
            "anthropic.tool_result.content": "native output",
        }
    )
    obs = convert(attrs, monkeypatch)["observation_span"]
    expected = {"explicit": "explicit", "": None, "[]": []}[value]
    assert obs["input"] == expected and obs["output"] == expected


@pytest.mark.unit
def test_native_normalization_is_idempotent():
    span = {
        "attributes": chat(
            **{
                "gen_ai.system_instructions": [{"type": "text", "content": "system"}],
                "gen_ai.input.messages": [
                    {"role": "user", "parts": [{"type": "text", "content": "user"}]}
                ],
                "gen_ai.conversation.id": "conversation-a",
            }
        )
    }
    normalize_span_attributes([span])
    once = deepcopy(span)
    normalize_span_attributes([span])
    assert span == once
