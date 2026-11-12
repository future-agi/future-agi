"""OBS-SW-40: the judge must see the answer, not the last user prompt."""

import json
from types import SimpleNamespace

import pytest


def _history(user_role="user", assistant_role="assistant"):
    return [
        {"role": "system", "content": "Follow instructions."},
        {"role": user_role, "content": "old question"},
        {"role": assistant_role, "content": "old answer"},
        {"role": user_role, "content": "latest question"},
        {"role": assistant_role, "content": "actual response"},
        {"role": "tool", "content": "not the assistant response"},
    ]


def _encode(messages, shape):
    value = {"messages": messages} if "wrapped" in shape else messages
    return json.dumps(value) if "json" in shape else value


@pytest.mark.parametrize("shape", ["list", "wrapped", "json-list", "json-wrapped"])
@pytest.mark.parametrize("roles", [("user", "assistant"), ("human", "ai")])
def test_trace_tree_mapping_judges_the_assistant_message(monkeypatch, shape, roles):
    from tracer.utils import eval as evaluation

    history = _encode(_history(*roles), shape)
    span = SimpleNamespace(
        id="root",
        name="instrumented chain",
        observation_type="chain",
        input=history,
        output=history,
    )
    trace = SimpleNamespace(id="trace", project_id="project")
    monkeypatch.setattr(evaluation, "_ordered_trace_spans", lambda _: [span])
    monkeypatch.setattr(evaluation, "_filter_bound_span_for_path", lambda *args: None)

    rendered = evaluation._resolve_trace_path(trace, "trace_tree")

    assert "  in: latest question\n  out: actual response" in rendered
    assert "out: latest question" not in rendered
    assert "old answer" not in rendered
    assert "not the assistant response" not in rendered
    full = evaluation.render_session_full_transcript([trace], lambda _: [span])
    assert full == "[Turn 1]\n" + rendered


@pytest.mark.parametrize("shape", ["list", "wrapped", "json-list", "json-wrapped"])
def test_session_transcript_uses_distinct_input_and_output_roles(shape):
    from tracer.utils.eval import render_session_transcript

    history = _encode(_history(), shape)
    trace = SimpleNamespace(input=history, output=history)

    assert render_session_transcript([trace]) == (
        "[Turn 1] User: latest question\nAssistant: actual response"
    )


def test_structured_assistant_content_is_not_replaced_by_the_prompt():
    from tracer.utils.eval import render_trace_tree

    history = _history()
    content = [{"type": "text", "text": "actual structured response"}]
    history[-2]["content"] = content
    span = SimpleNamespace(id="root", input=history, output=history)

    text = render_trace_tree([span])

    assert "  in: latest question\n  out: " + json.dumps(content) in text
    assert "out: latest question" not in text


def test_output_without_an_assistant_retains_history_not_a_fabricated_answer():
    from tracer.utils.eval import render_trace_tree

    history = [{"role": "user", "content": "unanswered question"}]
    span = SimpleNamespace(id="root", input=history, output=history)

    text = render_trace_tree([span])

    assert "  in: unanswered question" in text
    assert "  out: " + json.dumps(history) in text
    assert "out: unanswered question" not in text


@pytest.mark.parametrize("output", ["plain answer", {"content": "plain answer"}])
def test_scalar_and_content_outputs_keep_their_existing_text(output):
    from tracer.utils.eval import render_session_transcript

    assert render_session_transcript(
        [SimpleNamespace(input="question", output=output)]
    ) == ("[Turn 1] User: question\nAssistant: plain answer")
