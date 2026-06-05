"""Real, credential-only connectivity probes for simulation providers (TH-5642).

Each probe validates that a provider's credentials are live by making a single read-only
API call — it places NO phone calls and starts NO billable sessions. Probes return
``(ok: bool, detail: str)`` and read credentials from the ``SIM_VERIFY_<PROVIDER>_*``
environment convention. Only providers with a safe, proven probe are implemented here;
the verification harness reports the rest as SKIPPED rather than implying coverage.
"""

from __future__ import annotations

from collections.abc import Mapping

_TIMEOUT_SECONDS = 10.0


def _http_get(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
):
    import httpx

    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        return client.get(url, headers=headers or {}, params=params, auth=auth)


def _classify(resp, ok_detail: str, provider: str) -> tuple[bool, str]:
    """Map an HTTP response to (ok, detail).

    2xx -> valid credentials. 401/403 -> credentials rejected. Anything else is reported
    verbatim so a wrong endpoint reads as an endpoint issue, not a credential failure.
    """
    if 200 <= resp.status_code < 300:
        return True, ok_detail
    if resp.status_code in (401, 403):
        return False, f"{provider} rejected credentials ({resp.status_code})"
    return False, f"{provider} returned {resp.status_code}"


def deepgram_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Validate a Deepgram key via the read-only projects endpoint."""
    key = env.get("SIM_VERIFY_DEEPGRAM_API_KEY")
    if not key:
        return False, "SIM_VERIFY_DEEPGRAM_API_KEY not set"
    resp = _http_get(
        "https://api.deepgram.com/v1/projects",
        headers={"Authorization": f"Token {key}"},
    )
    if resp.status_code == 200:
        return True, "deepgram key valid (GET /v1/projects 200)"
    return False, f"deepgram returned {resp.status_code}"


def elevenlabs_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Validate an ElevenLabs key (and agent, if set) via read-only endpoints."""
    key = env.get("SIM_VERIFY_ELEVENLABS_API_KEY")
    if not key:
        return False, "SIM_VERIFY_ELEVENLABS_API_KEY not set"
    headers = {"xi-api-key": key}
    resp = _http_get("https://api.elevenlabs.io/v1/user", headers=headers)
    if resp.status_code != 200:
        return False, f"elevenlabs key invalid (GET /v1/user {resp.status_code})"
    agent_id = env.get("SIM_VERIFY_ELEVENLABS_AGENT_ID")
    if not agent_id:
        return True, "elevenlabs key valid (agent not checked; AGENT_ID unset)"
    signed = _http_get(
        "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
        headers=headers,
        params={"agent_id": agent_id},
    )
    if signed.status_code == 200:
        return True, "elevenlabs key + agent valid (signed-url 200)"
    return False, f"elevenlabs agent check returned {signed.status_code}"


def vapi_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Validate a Vapi key via the read-only list-assistants endpoint."""
    key = env.get("SIM_VERIFY_VAPI_API_KEY")
    if not key:
        return False, "SIM_VERIFY_VAPI_API_KEY not set"
    resp = _http_get(
        "https://api.vapi.ai/assistant",
        headers={"Authorization": f"Bearer {key}"},
    )
    return _classify(resp, "vapi key valid (GET /assistant 2xx)", "vapi")


def retell_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Validate a Retell key via the read-only list-agents endpoint."""
    key = env.get("SIM_VERIFY_RETELL_API_KEY")
    if not key:
        return False, "SIM_VERIFY_RETELL_API_KEY not set"
    resp = _http_get(
        "https://api.retellai.com/list-agents",
        headers={"Authorization": f"Bearer {key}"},
    )
    return _classify(resp, "retell key valid (GET /list-agents 2xx)", "retell")


def bland_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Validate a Bland key via the read-only account endpoint (no call placed)."""
    key = env.get("SIM_VERIFY_BLAND_API_KEY")
    if not key:
        return False, "SIM_VERIFY_BLAND_API_KEY not set"
    # Bland uses the raw key in the `authorization` header (no Bearer prefix).
    resp = _http_get("https://api.bland.ai/v1/me", headers={"authorization": key})
    return _classify(resp, "bland key valid (GET /v1/me 2xx)", "bland")


def twilio_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Validate Twilio creds via GET Account (Basic auth). Credential is 'AccountSid:AuthToken'."""
    raw = env.get("SIM_VERIFY_TWILIO_API_KEY")
    if not raw:
        return False, "SIM_VERIFY_TWILIO_API_KEY not set (expected 'AccountSid:AuthToken')"
    if ":" not in raw:
        return False, "SIM_VERIFY_TWILIO_API_KEY must be 'AccountSid:AuthToken'"
    sid, token = raw.split(":", 1)
    resp = _http_get(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
        auth=(sid, token),
    )
    return _classify(resp, "twilio creds valid (GET Account 2xx)", "twilio")


def agora_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Validate Agora REST creds via GET projects (Basic auth). Credential is 'CustomerKey:CustomerSecret'."""
    raw = env.get("SIM_VERIFY_AGORA_API_KEY")
    if not raw:
        return False, "SIM_VERIFY_AGORA_API_KEY not set (expected 'CustomerKey:CustomerSecret')"
    if ":" not in raw:
        return False, "SIM_VERIFY_AGORA_API_KEY must be 'CustomerKey:CustomerSecret'"
    customer_key, customer_secret = raw.split(":", 1)
    resp = _http_get(
        "https://api.agora.io/dev/v1/projects",
        auth=(customer_key, customer_secret),
    )
    return _classify(resp, "agora creds valid (GET /dev/v1/projects 2xx)", "agora")


def make_livekit_probe(provider_key: str):
    """Build a LiveKit-server probe for ``provider_key`` (livekit_bridge, pipecat, ...).

    Reads SIM_VERIFY_<PROVIDER>_LIVEKIT_URL/_API_KEY/_API_SECRET and lists rooms on the
    server — a read-only call that validates the server creds without joining a room or
    placing a call. The SDK call is async, so it is run in a fresh event loop.
    """
    prefix = provider_key.upper()
    url_var = f"SIM_VERIFY_{prefix}_LIVEKIT_URL"
    key_var = f"SIM_VERIFY_{prefix}_LIVEKIT_API_KEY"
    secret_var = f"SIM_VERIFY_{prefix}_LIVEKIT_API_SECRET"

    def _probe(env: Mapping[str, str]) -> tuple[bool, str]:
        url = env.get(url_var)
        api_key = env.get(key_var)
        api_secret = env.get(secret_var)
        missing = [
            v for v, val in ((url_var, url), (key_var, api_key), (secret_var, api_secret))
            if not val
        ]
        if missing:
            return False, f"not set: {', '.join(missing)}"

        import asyncio

        async def _list_rooms() -> int:
            from livekit import api

            lkapi = api.LiveKitAPI(url, api_key, api_secret)
            try:
                resp = await lkapi.room.list_rooms(api.ListRoomsRequest())
                return len(resp.rooms)
            finally:
                await lkapi.aclose()

        try:
            count = asyncio.run(_list_rooms())
        except Exception as exc:
            return False, f"{provider_key} list_rooms failed: {type(exc).__name__}: {exc}"
        return True, f"{provider_key} server creds valid (list_rooms ok, {count} rooms)"

    return _probe
