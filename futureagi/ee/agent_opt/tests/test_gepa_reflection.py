"""Unit tests for GEPAOptimizer reflection LM key plumbing."""

import litellm
import pytest

from ee.agent_opt.optimizers.gepa import GEPAOptimizer


class FakeResponse:
    def __init__(self, content):
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]


@pytest.fixture
def optimizer():
    return GEPAOptimizer(reflection_model="gemini/gemini-2.5-flash")


def test_no_key_raises(optimizer):
    with pytest.raises(ValueError, match="No API key provided"):
        optimizer._build_reflection_lm()
    optimizer.api_key = ""
    with pytest.raises(ValueError, match="No API key provided"):
        optimizer._build_reflection_lm()


def test_key_returns_callable_and_forwards_key(optimizer, monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return FakeResponse("improved")

    monkeypatch.setattr(litellm, "completion", fake_completion)
    optimizer.api_key = "sk-org-key"

    lm = optimizer._build_reflection_lm()
    assert callable(lm)
    assert len(calls) == 1  # auth pre-flight

    result = lm("rewrite this prompt")
    assert result == "improved"
    assert calls[-1]["api_key"] == "sk-org-key"
    assert calls[-1]["drop_params"] is True
    assert calls[-1]["messages"] == [
        {"role": "user", "content": "rewrite this prompt"}
    ]


def test_callable_passes_message_list_through(optimizer, monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return FakeResponse("ok")

    monkeypatch.setattr(litellm, "completion", fake_completion)
    optimizer.api_key = "sk-org-key"

    lm = optimizer._build_reflection_lm()
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    lm(messages)
    assert calls[-1]["messages"] is messages


def test_none_content_returns_empty_string(optimizer, monkeypatch):
    monkeypatch.setattr(litellm, "completion", lambda **_: FakeResponse(None))
    optimizer.api_key = "sk-org-key"

    lm = optimizer._build_reflection_lm()
    assert lm("prompt") == ""


def test_rejected_key_fails_fast(optimizer, monkeypatch):
    def rejecting_completion(**_kwargs):
        raise litellm.AuthenticationError(
            message="API key not valid",
            llm_provider="gemini",
            model="gemini/gemini-2.5-flash",
        )

    monkeypatch.setattr(litellm, "completion", rejecting_completion)
    optimizer.api_key = "sk-bad-key"

    with pytest.raises(RuntimeError, match="rejected the configured API key"):
        optimizer._build_reflection_lm()


def test_transient_preflight_error_does_not_block(optimizer, monkeypatch):
    calls = []

    def flaky_completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise litellm.RateLimitError(
                message="slow down",
                llm_provider="gemini",
                model="gemini/gemini-2.5-flash",
            )
        return FakeResponse("ok")

    monkeypatch.setattr(litellm, "completion", flaky_completion)
    optimizer.api_key = "sk-org-key"

    lm = optimizer._build_reflection_lm()
    assert lm("prompt") == "ok"
