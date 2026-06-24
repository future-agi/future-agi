"""Unit tests for RetellChatAgentAdapter (TH-5642 — chat agent-under-test).

Exercises the Retell Chat API handling with a fake ``requests`` (no network),
pinning the two protocol facts that are easy to get backwards:
- the chat is created ONCE and ``chat_id`` reused (Retell holds state server-side);
- each completion sends only the *newest* simulated-customer turn (no replay).
"""

import json

import pytest

from simulate.services import retell_chat_agent_adapter as mod
from simulate.services.retell_chat_agent_adapter import (
    RetellChatAgentAdapter,
    RetellChatAgentError,
)


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeRequests:
    """Records POST/PATCH calls and replays queued responses by path."""

    def __init__(self, responses):
        # responses: dict[path_suffix -> list[_FakeResp]] (FIFO per path)
        self._responses = {k: list(v) for k, v in responses.items()}
        self.posts = []  # list[(url, json_payload)]
        self.patches = []
        self.RequestException = Exception

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        for suffix, queue in self._responses.items():
            if url.endswith(suffix) and queue:
                return queue.pop(0)
        raise AssertionError(f"no fake response queued for POST {url}")

    def patch(self, url, headers=None, timeout=None):
        self.patches.append(url)
        return _FakeResp({}, 200)


def _adapter(monkeypatch, responses):
    fake = _FakeRequests(responses)
    monkeypatch.setattr(mod, "requests", fake)
    adapter = RetellChatAgentAdapter(agent_id="agent_x", api_key="key_x")
    return adapter, fake


@pytest.mark.unit
def test_requires_agent_id_and_api_key():
    with pytest.raises(ValueError):
        RetellChatAgentAdapter(agent_id="", api_key="k")
    with pytest.raises(ValueError):
        RetellChatAgentAdapter(agent_id="a", api_key="")


@pytest.mark.unit
def test_turn1_returns_begin_message_from_create_chat(monkeypatch):
    adapter, fake = _adapter(
        monkeypatch,
        {
            "/create-chat": [
                _FakeResp(
                    {
                        "chat_id": "Chat_1",
                        "chat_status": "ongoing",
                        "message_with_tool_calls": [
                            {"role": "agent", "content": "Hello, how can I help?"}
                        ],
                    }
                )
            ],
        },
    )
    # No user turn yet -> the agent speaks first (begin message).
    result = adapter.generate_response([])
    assert result["content"] == "Hello, how can I help?"
    assert result["chat_ended"] is False
    # create-chat called exactly once; no completion yet.
    assert [u for u, _ in fake.posts] == ["https://api.retellai.com/create-chat"]


@pytest.mark.unit
def test_chat_created_once_and_only_latest_turn_sent(monkeypatch):
    adapter, fake = _adapter(
        monkeypatch,
        {
            "/create-chat": [
                _FakeResp({"chat_id": "Chat_1", "message_with_tool_calls": []})
            ],
            "/create-chat-completion": [
                _FakeResp({"messages": [{"role": "agent", "content": "Reply A"}]}),
                _FakeResp({"messages": [{"role": "agent", "content": "Reply B"}]}),
            ],
        },
    )

    # First customer turn.
    r1 = adapter.generate_response([{"role": "user", "content": "first"}])
    # Second customer turn arrives; history now holds both + the agent's reply.
    r2 = adapter.generate_response(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "Reply A"},
            {"role": "user", "content": "second"},
        ]
    )

    assert r1["content"] == "Reply A"
    assert r2["content"] == "Reply B"

    # /create-chat hit exactly once (state is server-side).
    create_calls = [u for u, _ in fake.posts if u.endswith("/create-chat")]
    assert len(create_calls) == 1

    # Each completion sent ONLY the newest turn, with the cached chat_id — no replay.
    completion_payloads = [p for u, p in fake.posts if u.endswith("/create-chat-completion")]
    assert completion_payloads == [
        {"chat_id": "Chat_1", "content": "first"},
        {"chat_id": "Chat_1", "content": "second"},
    ]


@pytest.mark.unit
def test_chat_ended_detected_from_status(monkeypatch):
    adapter, _ = _adapter(
        monkeypatch,
        {
            "/create-chat": [_FakeResp({"chat_id": "Chat_1", "message_with_tool_calls": []})],
            "/create-chat-completion": [
                _FakeResp(
                    {
                        "chat_status": "ended",
                        "messages": [{"role": "agent", "content": "Goodbye!"}],
                    }
                )
            ],
        },
    )
    result = adapter.generate_response([{"role": "user", "content": "bye"}])
    assert result["content"] == "Goodbye!"
    assert result["chat_ended"] is True


@pytest.mark.unit
def test_only_agent_role_messages_are_returned(monkeypatch):
    adapter, _ = _adapter(
        monkeypatch,
        {
            "/create-chat": [_FakeResp({"chat_id": "Chat_1", "message_with_tool_calls": []})],
            "/create-chat-completion": [
                _FakeResp(
                    {
                        "messages": [
                            {"role": "user", "content": "echoed user turn"},
                            {"role": "agent", "content": "the answer"},
                        ]
                    }
                )
            ],
        },
    )
    result = adapter.generate_response([{"role": "user", "content": "q"}])
    # The echoed user turn is dropped; only the agent text comes back.
    assert result["content"] == "the answer"


@pytest.mark.unit
def test_http_error_raises(monkeypatch):
    adapter, _ = _adapter(
        monkeypatch,
        {"/create-chat": [_FakeResp({"error": "bad key"}, status_code=401)]},
    )
    with pytest.raises(RetellChatAgentError):
        adapter.generate_response([{"role": "user", "content": "hi"}])


@pytest.mark.unit
def test_missing_chat_id_raises(monkeypatch):
    adapter, _ = _adapter(
        monkeypatch,
        {"/create-chat": [_FakeResp({"chat_status": "ongoing"})]},
    )
    with pytest.raises(RetellChatAgentError):
        adapter.generate_response([{"role": "user", "content": "hi"}])


@pytest.mark.unit
def test_end_is_idempotent_and_best_effort(monkeypatch):
    adapter, fake = _adapter(
        monkeypatch,
        {"/create-chat": [_FakeResp({"chat_id": "Chat_1", "message_with_tool_calls": []})]},
    )
    # No chat yet -> end() is a no-op (no PATCH).
    adapter.end()
    assert fake.patches == []
    # After a turn, end() PATCHes the cached chat_id.
    adapter.generate_response([])
    adapter.end()
    assert fake.patches == ["https://api.retellai.com/end-chat/Chat_1"]
