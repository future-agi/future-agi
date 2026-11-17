"""Exercise the evaluator's real input rendering with a local judge response."""

import json
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.context_window import (
    fit_to_context,
)
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    _MAX_CONTEXT_CHARS,
    CustomPromptEvaluator,
)


@pytest.fixture
def judge(monkeypatch):
    # No provider clients or network calls: only the provider boundary is replaced.
    monkeypatch.setattr(LLM, "__init__", lambda self, **kwargs: None)
    evaluator = CustomPromptEvaluator(
        rule_prompt="Evaluate {{output}}", model="test-model", provider="openai"
    )
    evaluator.token_usage = {
        "completion_tokens": 0,
        "prompt_tokens": 0,
        "total_tokens": 0,
    }
    evaluator.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
    provider_call = Mock(return_value='{"result": "Pass", "explanation": "ok"}')
    monkeypatch.setattr(evaluator, "call_llm", provider_call)
    return evaluator, provider_call


def truncation_events(events):
    return [
        event
        for event in events
        if event["event"] == "custom_prompt_eval_input_truncated"
    ]


@pytest.mark.parametrize("kind", ["string", "dict", "list"])
@pytest.mark.parametrize("source", ["mapped", "row_context"])
def test_oversized_input_warns_and_reaches_judge(judge, kind, source):
    evaluator, provider_call = judge
    text = "synthetic-eval-data " * (_MAX_CONTEXT_CHARS // 19 + 1)
    value = {"text": text} if kind == "dict" else [text] if kind == "list" else text
    original_length = len(value) if kind == "string" else len(json.dumps(value))
    expected = fit_to_context(value, max_total_chars=_MAX_CONTEXT_CHARS)
    kwargs = (
        {"required_keys": ["output"], "output": value}
        if source == "mapped"
        else {"row_context": value}
    )
    if source == "row_context":
        evaluator.rule_prompt = "Evaluate the data"
    with capture_logs() as events:
        result = evaluator._evaluate(**kwargs)
    assert result["data"]["result"] == "Pass"
    assert truncation_events(events) == [
        {
            "event": "custom_prompt_eval_input_truncated",
            "log_level": "warning",
            "field": "output" if source == "mapped" else "row_context",
            "original_length": original_length,
            "truncated_length": len(expected),
            "limit": _MAX_CONTEXT_CHARS,
        }
    ]
    # The event has only field/count metadata, never the evaluated contents.
    message = provider_call.call_args.kwargs["prompt"][-1]["content"]
    assert expected in message
    assert text not in message
    if kind == "string":
        assert len(expected) == _MAX_CONTEXT_CHARS
    provider_call.assert_called_once()


@pytest.mark.parametrize("source", ["mapped", "row_context"])
@pytest.mark.parametrize("length", [10, _MAX_CONTEXT_CHARS])
def test_unchanged_string_does_not_warn(judge, source, length):
    evaluator, provider_call = judge
    value = "x" * length
    kwargs = (
        {"required_keys": ["output"], "output": value}
        if source == "mapped"
        else {"row_context": value}
    )
    with capture_logs() as events:
        evaluator._evaluate(**kwargs)
    assert truncation_events(events) == []
    assert value in provider_call.call_args.kwargs["prompt"][-1]["content"]


def test_multiple_fields_warn_independently(judge):
    evaluator, _ = judge
    with capture_logs() as events:
        evaluator._evaluate(
            required_keys=["output", "input", "short"],
            output="a" * (_MAX_CONTEXT_CHARS + 1),
            input="b" * (_MAX_CONTEXT_CHARS + 1),
            short="unchanged",
        )
    assert [event["field"] for event in truncation_events(events)] == [
        "output",
        "input",
    ]
