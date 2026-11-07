"""Content boundaries through the adapter and both Python observation converters."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.otel import (
    _convert_single_span,
    convert_otel_span_to_observation_span,
)


@pytest.fixture(params=["single", "bulk"])
def convert(request, monkeypatch):
    project = SimpleNamespace(id="project")
    monkeypatch.setattr("tracer.utils.otel.get_or_create_project", lambda *a: project)
    monkeypatch.setattr(
        "tracer.utils.otel.get_or_create_project_version", lambda **kw: None
    )
    monkeypatch.setattr("tracer.utils.otel.calculate_cost_from_tokens", lambda **kw: 0)
    monkeypatch.setattr("tracer.utils.otel.calculate_cost", lambda **kw: 0)

    def run(attributes):
        span = {
            "scope": {"name": "ordinary", "version": "1.0"},
            "attributes": deepcopy(attributes),
            "name": "content precedence",
            "project_name": "content-precedence",
            "project_type": "observe",
        }
        normalize_span_attributes([span])
        normalized = deepcopy(span["attributes"])
        if request.param == "single":
            result = convert_otel_span_to_observation_span(span, "synthetic-org")
        else:
            result = _convert_single_span(
                span,
                {("content-precedence", "synthetic-org", "observe"): project},
                {},
                "synthetic-org",
            )
        return normalized, result["observation_span"]

    return run


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["input", "output"])
@pytest.mark.parametrize("modern", [None, "", "null", "__REDACTED__", "[]", []])
@pytest.mark.parametrize(
    "result_key", ["gen_ai.tool.call.result", "anthropic.tool_result.content"]
)
def test_modern_tool_messages_suppress_lower_priority_content(
    convert, direction, modern, result_key
):
    attrs = {
        "gen_ai.operation.name": "execute_tool",
        f"gen_ai.{direction}.messages": modern,
        "gen_ai.tool.call.arguments": "PRIVATE_ARGUMENTS",
        result_key: "PRIVATE_RESULT",
    }
    normalized, observation = convert(attrs)
    assert f"{direction}.value" not in normalized
    assert f"raw.{direction}" not in normalized
    assert observation[direction] is None
    # The boundary is directional: the opposite side still has real content.
    other = "output" if direction == "input" else "input"
    assert observation[other] == (
        "PRIVATE_RESULT" if other == "output" else "PRIVATE_ARGUMENTS"
    )


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["input", "output"])
@pytest.mark.parametrize(
    "alias", ["{direction}.value", "fi.llm.{direction}", "raw.{direction}"]
)
@pytest.mark.parametrize("empty", [None, "", "[]", []])
def test_ordinary_indexed_messages_survive_empty_legacy_aliases(
    convert, direction, alias, empty
):
    prefix = "prompt" if direction == "input" else "completion"
    role = "user" if direction == "input" else "assistant"
    attrs = {
        "gen_ai.operation.name": "chat",
        alias.format(direction=direction): empty,
        f"gen_ai.{prefix}.0.role": role,
        f"gen_ai.{prefix}.0.content": "LEGACY_SENTINEL",
    }
    normalized, observation = convert(attrs)
    message = {"role": role, "content": "LEGACY_SENTINEL"}
    expected = [message] if direction == "input" else message
    assert normalized[f"raw.{direction}"] == expected
    assert observation[direction] == expected
