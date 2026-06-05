"""Per-provider user-side outbound dialers (TH-5642).

OUTBOUND simulation = the customer's agent CALLS our pool number and our simulator
answers. The system-side answer leg (LiveKit SIP inbound trunk + dispatch) is
provider-agnostic; the only per-provider piece is TRIGGERING the customer's agent to
place the call. That was hard-wired to Vapi (voice_large.py: "currently always
VAPI"), so a Retell/Bland user agent was silently dialed through the Vapi API.

This module provides a dialer registry keyed by the user's provider, each a thin
REST client returning ``{"id": <provider_call_id>}`` to match the existing
``engine.create_outbound_call`` contract. Raw REST (no ee dependency) and
unit-tested against the documented provider shapes; placing a real outbound call
costs money and dials a live number, so these are not live-exercised here.
"""

from __future__ import annotations

import os
from typing import Any

import requests
import structlog

logger = structlog.get_logger(__name__)

REQUEST_TIMEOUT = 30


class OutboundDialError(RuntimeError):
    """Raised when a provider's outbound-call API errors or returns no call id."""


class OutboundDialer:
    """Triggers the customer's agent to place an outbound call to ``to_phone_number``."""

    provider: str = ""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(f"{type(self).__name__} requires an api_key")
        self._api_key = api_key

    def create_outbound_call(
        self,
        *,
        assistant_id: str,
        from_phone_number: str,
        to_phone_number: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _require_id(call_id: Any, payload: Any) -> dict[str, Any]:
        if not call_id:
            raise OutboundDialError(f"No call id returned by provider: {payload}")
        return {"id": call_id}


class RetellOutboundDialer(OutboundDialer):
    """Retell outbound via ``POST /v2/create-phone-call`` (Bearer auth)."""

    provider = "retell"
    BASE_URL = "https://api.retellai.com"

    def create_outbound_call(
        self, *, assistant_id, from_phone_number, to_phone_number, metadata=None
    ):
        resp = requests.post(
            f"{self.BASE_URL}/v2/create-phone-call",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from_number": from_phone_number,
                "to_number": to_phone_number,
                "override_agent_id": assistant_id,
                "metadata": metadata or {},
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise OutboundDialError(
                f"Retell create-phone-call failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json() or {}
        return self._require_id(data.get("call_id"), data)


class BlandOutboundDialer(OutboundDialer):
    """Bland.ai outbound via ``POST /v1/calls`` (raw api_key auth, pathway = agent)."""

    provider = "bland"
    BASE_URL = "https://api.bland.ai"

    def create_outbound_call(
        self, *, assistant_id, from_phone_number, to_phone_number, metadata=None
    ):
        # Bland identifies the customer's agent by pathway_id; from_number is optional.
        body: dict[str, Any] = {
            "phone_number": to_phone_number,
            "pathway_id": assistant_id,
            "metadata": metadata or {},
        }
        if from_phone_number:
            body["from"] = from_phone_number
        resp = requests.post(
            f"{self.BASE_URL}/v1/calls",
            headers={"Authorization": self._api_key, "Content-Type": "application/json"},
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise OutboundDialError(
                f"Bland /v1/calls failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json() or {}
        return self._require_id(data.get("call_id"), data)


class ElevenLabsOutboundDialer(OutboundDialer):
    """ElevenLabs ConvAI outbound via ``POST /v1/convai/twilio/outbound-call``.

    ElevenLabs places outbound calls through its Twilio integration, so
    ``from_phone_number`` here carries the agent's registered
    ``agent_phone_number_id`` (not a raw E.164 number). Auth is ``xi-api-key``.
    """

    provider = "elevenlabs"
    BASE_URL = "https://api.elevenlabs.io"

    def create_outbound_call(
        self, *, assistant_id, from_phone_number, to_phone_number, metadata=None
    ):
        resp = requests.post(
            f"{self.BASE_URL}/v1/convai/twilio/outbound-call",
            headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
            json={
                "agent_id": assistant_id,
                "agent_phone_number_id": from_phone_number,
                "to_number": to_phone_number,
                "conversation_initiation_client_data": {"metadata": metadata or {}},
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise OutboundDialError(
                f"ElevenLabs outbound-call failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json() or {}
        # The call id is conversation_id (fall back to Twilio's callSid).
        return self._require_id(data.get("conversation_id") or data.get("callSid"), data)


# provider key -> dialer class. Vapi keeps its existing engine path (it's already
# wired); these add the previously-missing non-Vapi user-side dialers.
OUTBOUND_DIALERS: dict[str, type[OutboundDialer]] = {
    "retell": RetellOutboundDialer,
    "bland": BlandOutboundDialer,
    "elevenlabs": ElevenLabsOutboundDialer,
    "eleven_labs": ElevenLabsOutboundDialer,  # provider-string drift
}


def get_outbound_dialer(provider: str | None, api_key: str) -> OutboundDialer | None:
    """Return a dialer for the user's provider, or None to fall back to the existing
    (Vapi) engine path. Lets the orchestration replace the Vapi hard-wire with a
    registry lookup without breaking the Vapi flow."""
    cls = OUTBOUND_DIALERS.get((provider or "").strip().lower())
    if cls is None:
        return None
    key = api_key or os.getenv(f"{(provider or '').upper()}_API_KEY", "")
    return cls(api_key=key)
