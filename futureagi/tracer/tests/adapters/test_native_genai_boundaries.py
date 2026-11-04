"""Privacy and producer boundaries for native GenAI normalization."""

from copy import deepcopy

import pytest

from tracer.utils.adapters import normalize_span_attributes


@pytest.mark.unit
@pytest.mark.parametrize(
    "scope",
    [
        {},
        {"name": "ordinary", "version": "13.3.0"},
        {"name": "com.anthropic.sdk.csharp", "version": "13.3.1"},
    ],
)
def test_unqualified_producer_keeps_legacy_kind_and_session(scope):
    span = {
        "scope": scope,
        "attributes": {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.request.model": "claude-test",
            "gen_ai.conversation.id": "unqualified-session",
        },
    }
    normalize_span_attributes([span])
    assert "session.id" not in span["attributes"]
    assert span["attributes"]["gen_ai.span.kind"] == "LLM"
    assert "fi.normalization.profile" not in span["attributes"]


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "null", "__REDACTED__", "{broken"])
def test_redacted_native_messages_do_not_restore_system(value):
    span = {
        "attributes": {
            "gen_ai.operation.name": "chat",
            "gen_ai.input.messages": value,
            "gen_ai.system_instructions": [
                {"type": "text", "content": "PRIVATE_SYSTEM"}
            ],
        }
    }
    normalize_span_attributes([span])
    assert "input.value" not in span["attributes"]
    assert "raw.input" not in span["attributes"]


@pytest.mark.unit
def test_explicit_null_native_tool_result_does_not_restore_vendor_alias():
    span = {
        "attributes": {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.call.result": None,
            "anthropic.tool_result.content": "STALE_RESULT",
        }
    }
    normalize_span_attributes([span])
    assert "output.value" not in span["attributes"]


@pytest.mark.unit
@pytest.mark.parametrize("session", ["", 0, False, None, "explicit"])
def test_qualified_session_presence_wins(session):
    span = {
        "scope": {"name": "com.anthropic.sdk.csharp", "version": "13.3.0"},
        "attributes": {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.request.model": "claude-test",
            "gen_ai.conversation.id": "native-session",
            "session.id": session,
        },
    }
    normalize_span_attributes([span])
    assert span["attributes"]["session.id"] == session
    assert span["attributes"]["fi.span.kind"] == "AGENT"
    once = deepcopy(span)
    normalize_span_attributes([span])
    assert span == once


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["input", "output"])
@pytest.mark.parametrize("empty", [None, "", "[]", []])
def test_ordinary_legacy_content_retains_existing_raw_fallback(direction, empty):
    from tracer.utils.content_attributes import content_attribute

    attrs = {f"{direction}.value": empty, f"raw.{direction}": "legacy content"}
    assert content_attribute(attrs, direction) == "legacy content"
    # Native messages activate the stricter privacy boundary instead.
    attrs[f"gen_ai.{direction}.messages"] = [{"role": "user", "parts": []}]
    assert content_attribute(attrs, direction) == (None if empty == "" else empty)
