"""Unit tests for VapiChatAgentAdapter (TH-5642).

Drives the Vapi chat REST shapes with a fake ``requests`` (no network): create the
session once, then POST the latest user turn and read the assistant output. Mirrors
the proven VapiService request shapes.
"""

import json

import pytest

from simulate.services import vapi_chat_agent_adapter as mod
from simulate.services.chat_agent_adapter_factory import EXTERNAL_HOSTED_CHAT_ADAPTERS
from simulate.services.vapi_chat_agent_adapter import (
    VapiChatAgentAdapter,
    VapiChatAgentError,
)


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self, responses):
        self._responses = {k: list(v) for k, v in responses.items()}
        self.posts = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        for suffix, queue in self._responses.items():
            if url.endswith(suffix) and queue:
                return queue.pop(0)
        raise AssertionError(f"no fake response for POST {url}")


def _adapter(monkeypatch, responses):
    fake = _FakeRequests(responses)
    monkeypatch.setattr(mod, "requests", fake)
    return VapiChatAgentAdapter(agent_id="assistant_x", api_key="k"), fake


@pytest.mark.unit
def test_registered_in_factory():
    assert EXTERNAL_HOSTED_CHAT_ADAPTERS["vapi"] is VapiChatAgentAdapter


@pytest.mark.unit
def test_requires_agent_id_and_key():
    with pytest.raises(ValueError):
        VapiChatAgentAdapter(agent_id="", api_key="k")
    with pytest.raises(ValueError):
        VapiChatAgentAdapter(agent_id="a", api_key="")


@pytest.mark.unit
def test_session_created_once_and_only_latest_turn_sent(monkeypatch):
    adapter, fake = _adapter(
        monkeypatch,
        {
            "/session": [_FakeResp({"id": "sess_1"})],
            "/chat/": [
                _FakeResp({"output": [{"role": "assistant", "content": "Reply A"}]}),
                _FakeResp({"output": [{"role": "assistant", "content": "Reply B"}]}),
            ],
        },
    )
    r1 = adapter.generate_response([{"role": "user", "content": "first"}])
    r2 = adapter.generate_response([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "Reply A"},
        {"role": "user", "content": "second"},
    ])
    assert r1["content"] == "Reply A"
    assert r2["content"] == "Reply B"
    # /session hit exactly once.
    assert len([u for u, _ in fake.posts if u.endswith("/session")]) == 1
    # Each /chat/ sent only the newest turn with the cached sessionId.
    chat_payloads = [p for u, p in fake.posts if u.endswith("/chat/")]
    assert chat_payloads == [
        {"input": [{"role": "user", "content": "first"}], "sessionId": "sess_1"},
        {"input": [{"role": "user", "content": "second"}], "sessionId": "sess_1"},
    ]


@pytest.mark.unit
def test_endcall_tool_marks_chat_ended(monkeypatch):
    adapter, _ = _adapter(
        monkeypatch,
        {
            "/session": [_FakeResp({"id": "sess_1"})],
            "/chat/": [_FakeResp({"output": [
                {"role": "assistant", "content": "Bye",
                 "tool_calls": [{"function": {"name": "endCall"}}]},
            ]})],
        },
    )
    result = adapter.generate_response([{"role": "user", "content": "bye"}])
    assert result["content"] == "Bye"
    assert result["chat_ended"] is True


@pytest.mark.unit
def test_http_error_raises(monkeypatch):
    adapter, _ = _adapter(
        monkeypatch, {"/session": [_FakeResp({"error": "bad"}, status_code=401)]}
    )
    with pytest.raises(VapiChatAgentError):
        adapter.generate_response([{"role": "user", "content": "hi"}])
