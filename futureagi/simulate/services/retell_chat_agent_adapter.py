"""RetellChatAgentAdapter — drive a real Retell *chat* agent as the agent-under-test.

This is the chat analogue of the WebRTC bridge connectors (TH-5642): where the
voice path tests a customer's Retell *voice* agent by joining it over LiveKit, the
chat path tests a customer's Retell *chat* agent by driving its text conversation
server-side.

It deliberately mirrors the ``generate_response(conversation_history) -> {...}``
contract of :class:`PromptBasedAgentAdapter` so it can be slotted into the same
``run_prompt_based_conversation`` loop — the simulated customer (a
``ChatServiceBlueprint`` engine) plays the user, and this adapter plays the agent.

Retell Chat API (https://docs.retellai.com/api-references/create-chat,
/create-chat-completion):
- ``POST /create-chat``            ``{"agent_id": ...}`` -> ``{"chat_id", "message_with_tool_calls":[...], ...}``
- ``POST /create-chat-completion`` ``{"chat_id", "content": <latest user turn>}`` -> ``{"messages":[{"role":"agent","content":...}], ...}``
- ``PATCH /end-chat/{chat_id}``     ends the chat.

Two protocol facts this adapter is built around (both confirmable only with a live
Retell agent — see ``WIRING & LIVE-VERIFICATION`` below):
1. **Retell holds conversation state server-side.** The chat is created ONCE
   (lazily, on first turn) and ``chat_id`` cached; each completion sends only the
   *newest* simulated-customer turn as ``content``. We never replay history — that
   would double-send.
2. **On turn 1 the agent speaks first.** ``/create-chat`` may return the agent's
   ``begin_message`` in ``message_with_tool_calls``; we surface that as the first
   response. If absent, we return empty content (the sim loop treats empty as a
   natural end) rather than fabricating a turn.

WIRING & LIVE-VERIFICATION (intentionally deferred — DESIGN.md §5):
- No factory reads an agent-definition field here: chat agents-under-test are today
  either prompt-templates (``source_type="prompt"``) or SDK-pushed
  (``source_type="agent_definition"``). Hosting an *external* chat agent
  server-side needs a new source_type + a schema field carrying ``agent_id``; that
  product decision is out of scope for this unit (same gate as Deepgram).
- The exact ``/create-chat-completion`` response envelope (``messages`` vs
  ``message_with_tool_calls``) and whether token usage is returned can only be
  pinned against a real Retell agent. The parser below accepts both message keys
  defensively; usage defaults to zeros when Retell omits it.
"""

from __future__ import annotations

import time
from typing import Any

import requests
import structlog

logger = structlog.get_logger(__name__)

RETELL_BASE_URL = "https://api.retellai.com"
RETELL_AGENT_ROLE = "agent"  # Retell labels assistant turns "agent" in chat
RETELL_REQUEST_TIMEOUT = 30


class RetellChatAgentError(RuntimeError):
    """Raised when the Retell Chat API returns an error or unexpected payload."""


class RetellChatAgentAdapter:
    """Drives a customer's Retell chat agent as the agent-under-test.

    Implements the same ``generate_response`` contract as
    :class:`PromptBasedAgentAdapter`; see module docstring for the protocol.
    """

    def __init__(
        self,
        agent_id: str,
        api_key: str,
        *,
        base_url: str = RETELL_BASE_URL,
        timeout: int = RETELL_REQUEST_TIMEOUT,
    ):
        """Initialize the adapter.

        Args:
            agent_id: The customer's Retell chat-agent id (agent-under-test).
            api_key: Retell API key (Bearer).
            base_url: Override for the Retell API base (tests / self-host).
            timeout: Per-request timeout in seconds.
        """
        if not agent_id:
            raise ValueError("RetellChatAgentAdapter requires an agent_id")
        if not api_key:
            raise ValueError("RetellChatAgentAdapter requires an api_key")
        self.agent_id = agent_id
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

        # Server-side conversation state.
        self._chat_id: str | None = None
        # How many of the conversation_history "user" turns we have already
        # forwarded to Retell — guards against re-sending the same turn.
        self._sent_user_turns = 0
        self._chat_ended = False

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(
            f"{self._base_url}{path}",
            json=payload,
            headers=self._headers,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            raise RetellChatAgentError(
                f"Retell {path} failed ({resp.status_code}): {resp.text}"
            )
        try:
            return resp.json() or {}
        except ValueError as e:
            raise RetellChatAgentError(
                f"Retell {path} returned non-JSON body: {resp.text}"
            ) from e

    # ------------------------------------------------------------------
    # Protocol helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _latest_user_content(conversation_history: list[dict[str, Any]]) -> list[str]:
        """Return the user (simulated-customer) turn contents, in order.

        From the agent-under-test's perspective the simulated customer is the
        "user"; "assistant" entries are the agent's own prior turns (which Retell
        already tracks server-side, so we never resend them).
        """
        return [
            str(m.get("content") or "")
            for m in conversation_history
            if m.get("role") == "user" and m.get("content")
        ]

    def _extract_agent_text(self, payload: dict[str, Any]) -> str:
        """Pull the agent's reply text out of a Retell chat payload.

        Accepts either ``messages`` (create-chat-completion) or
        ``message_with_tool_calls`` (create-chat / get-chat); both are lists of
        ``{"role", "content"}``. Only ``agent`` role messages are returned.
        """
        messages = payload.get("messages")
        if messages is None:
            messages = payload.get("message_with_tool_calls") or []
        parts = [
            str(m.get("content") or "")
            for m in messages
            if m.get("role") == RETELL_AGENT_ROLE and m.get("content")
        ]
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _is_ended(payload: dict[str, Any]) -> bool:
        return str(payload.get("chat_status") or "").lower() == "ended"

    def _ensure_chat(self) -> dict[str, Any]:
        """Create the Retell chat once and cache the chat_id; return the payload."""
        if self._chat_id is not None:
            return {}
        payload = self._post("/create-chat", {"agent_id": self.agent_id})
        chat_id = payload.get("chat_id")
        if not chat_id:
            raise RetellChatAgentError(
                f"Retell /create-chat returned no chat_id: {payload}"
            )
        self._chat_id = chat_id
        logger.info("retell_chat_agent_created", chat_id=chat_id, agent_id=self.agent_id)
        return payload

    # ------------------------------------------------------------------
    # Agent-under-test contract (mirrors PromptBasedAgentAdapter)
    # ------------------------------------------------------------------

    def generate_response(
        self,
        conversation_history: list[dict[str, Any]],
        additional_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate the Retell agent's next turn for the given conversation.

        Args:
            conversation_history: ``[{"role": "user"|"assistant", "content": ...}]``
                from the agent-under-test's perspective (user = simulated customer).
            additional_context: Unused; kept for contract parity.

        Returns:
            ``{"content", "role", "finish_reason", "model", "latency_ms", "usage",
            "chat_ended"}`` — same shape PromptBasedAgentAdapter returns.
        """
        start = time.perf_counter()
        create_payload = self._ensure_chat()

        user_turns = self._latest_user_content(conversation_history)
        new_turns = user_turns[self._sent_user_turns :]

        if not new_turns:
            # Turn 1 (agent speaks first): surface the begin_message from
            # /create-chat if the agent has one; otherwise empty (loop ends).
            content = self._extract_agent_text(create_payload)
            ended = self._is_ended(create_payload)
        else:
            # Send only the newest customer turn; Retell holds the rest.
            content_to_send = new_turns[-1]
            completion = self._post(
                "/create-chat-completion",
                {"chat_id": self._chat_id, "content": content_to_send},
            )
            self._sent_user_turns = len(user_turns)
            content = self._extract_agent_text(completion)
            ended = self._is_ended(completion)

        self._chat_ended = ended
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "retell_chat_agent_turn",
            chat_id=self._chat_id,
            has_content=bool(content),
            chat_ended=ended,
        )
        return {
            "content": content,
            "role": "assistant",
            "finish_reason": "stop",
            "model": f"retell:{self.agent_id}",
            "latency_ms": latency_ms,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "chat_ended": ended,
        }

    def end(self) -> None:
        """Best-effort end of the Retell chat (idempotent)."""
        if not self._chat_id:
            return
        try:
            requests.patch(
                f"{self._base_url}/end-chat/{self._chat_id}",
                headers=self._headers,
                timeout=self._timeout,
            )
        except requests.RequestException as e:  # pragma: no cover - best effort
            logger.warning("retell_chat_agent_end_failed", error=str(e))
