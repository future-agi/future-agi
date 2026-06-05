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


def _http_get(url: str, headers: dict[str, str], params: dict[str, str] | None = None):
    import httpx

    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        return client.get(url, headers=headers, params=params)


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
