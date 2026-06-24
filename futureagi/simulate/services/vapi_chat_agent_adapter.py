"""VapiChatAgentAdapter — drive a real Vapi assistant in chat (text) mode (TH-5642).

The chat analogue for Vapi. Like Retell, Vapi exposes a turn-by-turn REST chat API
(the same endpoints the platform's VapiService already uses for the simulator side),
so this mirrors RetellChatAgentAdapter: a chat session is created once, then each
turn POSTs the latest user message and returns the assistant's reply.

Vapi chat API (https://docs.vapi.ai, mirrored from ee VapiService):
- ``POST {base}/session``  ``{"assistantId": ..., "name": ...}`` -> ``{"id": <session_id>}``
- ``POST {base}/chat/``    ``{"input": [{"role":"user","content":...}], "sessionId": ...}``
                           -> ``{"output": [{"role":"assistant","content":...}], ...}``
- An ``endCall`` tool call in ``output`` signals the assistant ended the chat.

Built from the proven VapiService request shapes; raw REST (no ee dependency) so the
routing factory stays light to import. Not live-verified (no Vapi key in this env) —
unit-tested against the documented shapes; flagged for a live run.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests
import structlog

logger = structlog.get_logger(__name__)

VAPI_DEFAULT_BASE_URL = "https://api.vapi.ai"
VAPI_REQUEST_TIMEOUT = 30


class VapiChatAgentError(RuntimeError):
    """Raised on Vapi chat API errors / unexpected payloads."""


class VapiChatAgentAdapter:
    """Drives a customer's Vapi assistant as the text agent-under-test."""

    def __init__(self, agent_id: str, api_key: str, *, base_url: str | None = None):
        if not agent_id:
            raise ValueError("VapiChatAgentAdapter requires an agent_id (assistantId)")
        if not api_key:
            raise ValueError("VapiChatAgentAdapter requires an api_key")
        self.assistant_id = agent_id
        self._api_key = api_key
        self._base_url = (
            base_url or os.getenv("VAPI_API_BASE_URL") or VAPI_DEFAULT_BASE_URL
        ).rstrip("/")
        self._session_id: str | None = None
        self._sent_user_turns = 0
        self._chat_ended = False

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
            timeout=VAPI_REQUEST_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise VapiChatAgentError(
                f"Vapi {path} failed ({resp.status_code}): {resp.text}"
            )
        try:
            return resp.json() or {}
        except ValueError as e:
            raise VapiChatAgentError(
                f"Vapi {path} returned non-JSON body: {resp.text}"
            ) from e

    @staticmethod
    def _latest_user_content(conversation_history: list[dict[str, Any]]) -> list[str]:
        return [
            str(m.get("content") or "")
            for m in conversation_history
            if m.get("role") == "user" and m.get("content")
        ]

    @staticmethod
    def _extract_assistant_text(output: list[dict[str, Any]]) -> str:
        return "\n".join(
            str(m.get("content") or "")
            for m in output
            if m.get("role") == "assistant" and m.get("content")
        )

    @staticmethod
    def _ended(output: list[dict[str, Any]]) -> bool:
        for m in output:
            for tc in m.get("tool_calls") or []:
                if (tc.get("function") or {}).get("name") == "endCall":
                    return True
        return False

    def _ensure_session(self) -> None:
        if self._session_id is not None:
            return
        payload = self._post(
            "/session", {"assistantId": self.assistant_id, "name": "FI simulation chat"}
        )
        session_id = payload.get("id")
        if not session_id:
            raise VapiChatAgentError(f"Vapi /session returned no id: {payload}")
        self._session_id = session_id
        logger.info("vapi_chat_session_created", session_id=session_id)

    def generate_response(
        self,
        conversation_history: list[dict[str, Any]],
        additional_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        self._ensure_session()

        user_turns = self._latest_user_content(conversation_history)
        new_turns = user_turns[self._sent_user_turns :]
        if not new_turns:
            # Vapi assistants respond to user input; no separate begin message here.
            content, ended = "", False
        else:
            result = self._post(
                "/chat/",
                {
                    "input": [{"role": "user", "content": new_turns[-1]}],
                    "sessionId": self._session_id,
                },
            )
            self._sent_user_turns = len(user_turns)
            output = result.get("output") or []
            content = self._extract_assistant_text(output)
            ended = self._ended(output)

        self._chat_ended = ended
        return {
            "content": content,
            "role": "assistant",
            "finish_reason": "stop",
            "model": f"vapi:{self.assistant_id}",
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "chat_ended": ended,
        }
