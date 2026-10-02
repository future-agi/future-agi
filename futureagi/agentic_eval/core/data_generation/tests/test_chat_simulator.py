"""Regression tests for bounded simulate_chat (L-01).

Pre-fix, simulate_chat looped forever when models never emitted
'topic resolved'. These tests prove bounded termination.
"""

import pytest

from agentic_eval.core.data_generation.chat_simulator import (
    ChatSimulator,
    simulate_chat,
)


class FakeClient:
    """Minimal stub for LLM._get_completion_content.

    Chat calls (no `model` kwarg) pop from chat_responses.
    Reaction calls (with `model` kwarg) return reaction_response.
    """

    def __init__(self, chat_responses, reaction_response="None"):
        self._chat_responses = list(chat_responses)
        self._reaction_response = reaction_response
        self.chat_calls = 0
        self.reaction_calls = 0

    def _get_completion_content(self, messages=None, model=None, **kwargs):
        if model is not None:
            self.reaction_calls += 1
            return self._reaction_response
        self.chat_calls += 1
        if self._chat_responses:
            return self._chat_responses.pop(0)
        return "keep going"


def _make_pair(expert_texts, human_texts, reaction="None"):
    expert_client = FakeClient(expert_texts, reaction_response=reaction)
    human_client = FakeClient(human_texts, reaction_response=reaction)
    expert = ChatSimulator(expert_client, "expert", "expert-sys", "m")
    human = ChatSimulator(human_client, "human", "human-sys", "m")
    return human, expert, human_client, expert_client


def test_terminates_on_max_turns_when_never_resolved():
    human, expert, human_client, expert_client = _make_pair(
        ["keep going"] * 10, ["keep going"] * 10
    )
    result = simulate_chat(human, expert, "q?", max_turns=3, verbose=False)
    assert result["resolved"] is False
    assert result["reason"] == "max_turns"
    assert result["turns"] == 3
    assert expert_client.chat_calls == 3
    assert human_client.chat_calls == 3


def test_exits_early_when_topic_resolved():
    human, expert, _, _ = _make_pair(["all done, topic resolved!"], ["keep going"])
    result = simulate_chat(human, expert, "q?", max_turns=10, verbose=False)
    assert result == {"resolved": True, "reason": "topic_resolved", "turns": 1}


def test_empty_responses_do_not_hang():
    human, expert, _, _ = _make_pair([""] * 10, [""] * 10)
    result = simulate_chat(human, expert, "q?", max_turns=5, verbose=False)
    assert result["resolved"] is False
    assert result["reason"] == "empty_response"
    assert result["turns"] == 2


def test_single_empty_then_resolved():
    # Transient single empty is tolerated, resolution still detected.
    human, expert, _, _ = _make_pair(["topic resolved now"], [""], reaction="None")
    # expert "topic resolved now" on turn 1, human "" -> non-empty path wins
    result = simulate_chat(human, expert, "q?", max_turns=5, verbose=False)
    assert result["resolved"] is True
    assert result["reason"] == "topic_resolved"


def test_invalid_max_turns_raises():
    human, expert, _, _ = _make_pair(["x"], ["x"])
    with pytest.raises(ValueError):
        simulate_chat(human, expert, "q?", max_turns=0, verbose=False)


def test_invalid_timeout_raises():
    human, expert, _, _ = _make_pair(["x"], ["x"])
    with pytest.raises(ValueError):
        simulate_chat(human, expert, "q?", max_turns=3, timeout_s=0, verbose=False)


def test_nonfinite_timeout_raises():
    human, expert, _, _ = _make_pair(["x"], ["x"])
    with pytest.raises(ValueError):
        simulate_chat(
            human, expert, "q?", max_turns=3, timeout_s=float("nan"), verbose=False
        )
    with pytest.raises(ValueError):
        simulate_chat(
            human, expert, "q?", max_turns=3, timeout_s=float("inf"), verbose=False
        )


def test_timeout_budget_exhausted(monkeypatch):
    human, expert, _, _ = _make_pair(["keep going"] * 10, ["keep going"] * 10)
    import agentic_eval.core.data_generation.chat_simulator as mod

    calls = {"n": 0}

    def fake_mono():
        calls["n"] += 1
        # first call sets deadline, every later call is past deadline
        return 100.0 if calls["n"] == 1 else 200.0

    monkeypatch.setattr(mod.time, "monotonic", fake_mono)
    result = simulate_chat(
        human, expert, "q?", max_turns=10, timeout_s=10, verbose=False
    )
    assert result["resolved"] is False
    assert result["reason"] == "timeout"
    assert result["turns"] == 0
