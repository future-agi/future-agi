"""ElevenLabsChatAgentAdapter — drive a real ElevenLabs ConvAI agent in TEXT mode.

The chat analogue for ElevenLabs (TH-5642). Unlike Retell (stateless REST per turn),
ElevenLabs ConvAI has no turn-by-turn REST chat endpoint — its text path is the
stateful ConvAI WebSocket. This adapter holds one WS open for the conversation and
bridges it to the synchronous ``generate_response(conversation_history) -> {...}``
contract (same shape as PromptBasedAgentAdapter) via an owned event loop.

ConvAI WebSocket (https://elevenlabs.io/docs/agents-platform/api-reference/...):
- URL: signed (private agents) or ``wss://api.elevenlabs.io/v1/convai/conversation?agent_id=...``
- Send text:  ``{"type": "user_message", "text": "..."}``
- Receive:    ``{"type": "agent_response", "agent_response_event": {"agent_response": "..."}}``
- Keepalive:  ``{"type": "ping", "ping_event": {"event_id": N}}`` -> ``{"type": "pong", "event_id": N}``
- ``conversation_initiation_metadata`` / ``user_transcript`` / ``audio`` / ``interruption``
  are control/voice frames — handled internally, not returned.

The agent speaks first: on connect we read its opening ``agent_response`` (the
agent's ``first_message``) and surface it as turn 1.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/convai/conversation"
ELEVENLABS_SIGNED_URL = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"
_RECV_TIMEOUT = 30.0
# Short read for the opening greeting: many ConvAI agents have no first_message and
# wait for the user, so we must not block a full turn-timeout on turn 1 (verified
# live 2026-06-05: a no-greeting agent otherwise stalls ~30s before the first user
# turn can be sent).
_GREETING_TIMEOUT = 4.0
_MAX_FRAMES_PER_TURN = 200


class ElevenLabsChatAgentError(RuntimeError):
    """Raised on ElevenLabs ConvAI protocol/transport errors."""


class ElevenLabsChatAgentAdapter:
    """Drives a customer's ElevenLabs ConvAI agent as the text agent-under-test."""

    def __init__(self, agent_id: str, api_key: str, *, ws_url: str = ELEVENLABS_WS_URL):
        if not agent_id:
            raise ValueError("ElevenLabsChatAgentAdapter requires an agent_id")
        self.agent_id = agent_id
        self._api_key = api_key
        self._ws_url = ws_url

        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._session = None
        self._ws = None
        self._connected = False
        self._greeting: str = ""
        self._sent_user_turns = 0

    # ------------------------------------------------------------------
    # sync <-> async bridge
    # ------------------------------------------------------------------
    def _loop(self) -> asyncio.AbstractEventLoop:
        if self._event_loop is None:
            self._event_loop = asyncio.new_event_loop()
        return self._event_loop

    @staticmethod
    def _user_turns(conversation_history: list[dict[str, Any]]) -> list[str]:
        return [
            str(m.get("content") or "")
            for m in conversation_history
            if m.get("role") == "user" and m.get("content")
        ]

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------
    async def _resolve_ws_url(self) -> str:
        if not self._api_key:
            return f"{self._ws_url}?agent_id={self.agent_id}"
        async with self._session.get(
            ELEVENLABS_SIGNED_URL,
            params={"agent_id": self.agent_id},
            headers={"xi-api-key": self._api_key},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise ElevenLabsChatAgentError(
                    f"ElevenLabs signed-url failed ({resp.status}): {body}"
                )
            data = await resp.json()
        signed = data.get("signed_url")
        if not signed:
            raise ElevenLabsChatAgentError(f"No signed_url in response: {data}")
        return signed

    async def _connect(self) -> None:
        import aiohttp

        self._session = aiohttp.ClientSession()
        url = await self._resolve_ws_url()
        self._ws = await self._session.ws_connect(url)
        self._connected = True
        # Agent MAY speak first — capture its opening agent_response if it comes
        # quickly; agents without a first_message just wait for the user (no stall).
        self._greeting = await self._read_agent_text(timeout=_GREETING_TIMEOUT)
        logger.info("elevenlabs_chat_connected", agent_id=self.agent_id)

    async def _read_agent_text(self, timeout: float = _RECV_TIMEOUT) -> str:
        """Read frames until an agent_response; answer pings; skip audio/control."""
        import aiohttp

        for _ in range(_MAX_FRAMES_PER_TURN):
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=timeout)
            except TimeoutError:
                return ""
            if msg.type == aiohttp.WSMsgType.TEXT:
                import json

                try:
                    event = json.loads(msg.data)
                except (ValueError, TypeError):
                    continue
                etype = event.get("type")
                if etype == "agent_response":
                    return str(
                        (event.get("agent_response_event") or {}).get("agent_response")
                        or ""
                    )
                if etype == "ping":
                    event_id = (event.get("ping_event") or {}).get("event_id")
                    await self._ws.send_json({"type": "pong", "event_id": event_id})
                # conversation_initiation_metadata / user_transcript / audio /
                # interruption: ignore.
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        return ""

    async def _generate_async(self, conversation_history: list[dict[str, Any]]) -> str:
        if not self._connected:
            await self._connect()

        user_turns = self._user_turns(conversation_history)
        new_turns = user_turns[self._sent_user_turns :]
        if not new_turns:
            # Turn 1: the agent's opening message.
            return self._greeting

        await self._ws.send_json({"type": "user_message", "text": new_turns[-1]})
        self._sent_user_turns = len(user_turns)
        return await self._read_agent_text()

    # ------------------------------------------------------------------
    # agent-under-test contract
    # ------------------------------------------------------------------
    def generate_response(
        self,
        conversation_history: list[dict[str, Any]],
        additional_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        content = self._loop().run_until_complete(
            self._generate_async(conversation_history)
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {
            "content": content,
            "role": "assistant",
            "finish_reason": "stop",
            "model": f"elevenlabs:{self.agent_id}",
            "latency_ms": latency_ms,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "chat_ended": False,
        }

    def end(self) -> None:
        if self._event_loop is None:
            return
        try:
            self._event_loop.run_until_complete(self._close())
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("elevenlabs_chat_end_failed", error=str(e))
        finally:
            self._event_loop.close()
            self._event_loop = None

    async def _close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._connected = False
