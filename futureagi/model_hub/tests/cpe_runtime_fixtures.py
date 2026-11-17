"""Offline runtime-renderer fixtures; the cloud runner also blocks import-time egress."""

import socket
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def refuse_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Template tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def make_judge(monkeypatch, refuse_network):
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator import (
        evaluator as evaluator_module,
    )

    monkeypatch.setattr(LLM, "__init__", lambda self, **kwargs: None)
    monkeypatch.setattr(
        evaluator_module, "detect_and_build_media_blocks", lambda **kwargs: ([], {})
    )

    def build(rule_prompt, **kwargs):
        judge = evaluator_module.CustomPromptEvaluator(
            rule_prompt=rule_prompt, model="test-model", provider="openai", **kwargs
        )
        judge._is_turing = False
        judge.temperature = 0
        judge.max_tokens = 256
        judge.token_usage = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        judge.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
        judge.call_llm = Mock(return_value='{"result": "Pass", "explanation": "ok"}')
        return judge

    return build


@pytest.fixture
def render_body():
    def render(judge, values):
        # Keep an Input Data delimiter even for a prompt with no variables.
        values = {"fixture_sentinel": "benign", **values}
        judge.call_llm.reset_mock()
        judge._evaluate(required_keys=list(values), **values)
        judge.call_llm.assert_called_once()
        text = judge.call_llm.call_args.kwargs["prompt"][-1]["content"]
        return text.split("\n\n--- Input Data ---\n", 1)[0]

    return render
