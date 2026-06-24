"""Unit tests for ElevenLabsChatAgentAdapter (TH-5642).

Exercises the ConvAI text WS protocol with a fake WebSocket (no network): send a
``user_message``, read the ``agent_response``, answer pings, ignore audio/control.
The sync ``generate_response`` drives the adapter's owned event loop.
"""

import json
from types import SimpleNamespace

import aiohttp
import pytest

from simulate.services.chat_agent_adapter_factory import EXTERNAL_HOSTED_CHAT_ADAPTERS
from simulate.services.elevenlabs_chat_agent_adapter import (
    ElevenLabsChatAgentAdapter,
)


class _FakeWS:
    def __init__(self, incoming=()):
        self._incoming = list(incoming)
        self.sent = []
        self.closed = False

    async def send_json(self, obj):
        self.sent.append(obj)

    async def receive(self):
        if self._incoming:
            mtype, data = self._incoming.pop(0)
            return SimpleNamespace(type=mtype, data=data)
        return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)

    async def close(self):
        self.closed = True


def _agent_response(text):
    return (aiohttp.WSMsgType.TEXT, json.dumps(
        {"type": "agent_response", "agent_response_event": {"agent_response": text}}))


def _connected(ws):
    a = ElevenLabsChatAgentAdapter(agent_id="agent_x", api_key="k")
    a._ws = ws
    a._connected = True  # skip the real _connect()
    return a


@pytest.mark.unit
def test_registered_for_both_provider_spellings():
    assert EXTERNAL_HOSTED_CHAT_ADAPTERS["elevenlabs"] is ElevenLabsChatAgentAdapter
    assert EXTERNAL_HOSTED_CHAT_ADAPTERS["eleven_labs"] is ElevenLabsChatAgentAdapter


@pytest.mark.unit
def test_requires_agent_id():
    with pytest.raises(ValueError):
        ElevenLabsChatAgentAdapter(agent_id="", api_key="k")


@pytest.mark.unit
def test_sends_user_text_and_returns_agent_response():
    ws = _FakeWS([_agent_response("Hello, how can I help?")])
    a = _connected(ws)
    out = a.generate_response([{"role": "user", "content": "hi there"}])
    assert out["content"] == "Hello, how can I help?"
    assert out["role"] == "assistant"
    # The latest user turn was sent as a ConvAI user_message text frame.
    assert {"type": "user_message", "text": "hi there"} in ws.sent


@pytest.mark.unit
def test_answers_ping_then_reads_agent_response():
    ws = _FakeWS([
        (aiohttp.WSMsgType.TEXT, json.dumps({"type": "ping", "ping_event": {"event_id": 7}})),
        (aiohttp.WSMsgType.TEXT, json.dumps({"type": "user_transcript",
                                             "user_transcription_event": {"user_transcript": "hi"}})),
        _agent_response("Answer after ping"),
    ])
    a = _connected(ws)
    out = a.generate_response([{"role": "user", "content": "q"}])
    assert out["content"] == "Answer after ping"
    assert {"type": "pong", "event_id": 7} in ws.sent


@pytest.mark.unit
def test_only_latest_turn_sent_no_replay():
    ws = _FakeWS([_agent_response("R1"), _agent_response("R2")])
    a = _connected(ws)
    a.generate_response([{"role": "user", "content": "first"}])
    a.generate_response([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "R1"},
        {"role": "user", "content": "second"},
    ])
    user_msgs = [m for m in ws.sent if m.get("type") == "user_message"]
    assert user_msgs == [
        {"type": "user_message", "text": "first"},
        {"type": "user_message", "text": "second"},
    ]
