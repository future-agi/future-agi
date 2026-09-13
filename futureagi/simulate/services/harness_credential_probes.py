"""Live, read-only checks that a target-provider credential is accepted by its provider.

Presence of an alias says nothing about whether the key is right; a wrong key otherwise
surfaces from the agent's own startup or first call, minutes and one sandbox later. Each
provider here is one table row: the aliases it needs and the single cheapest request that
only succeeds with a valid key. Adding a provider means adding a row, nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import requests

PROBE_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class ProbeRequest:
    method: str
    url: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class ProviderProbe:
    label: str
    aliases: tuple[str, ...]
    build: Callable[[Mapping[str, str]], ProbeRequest]
    # Statuses that mean "the key is wrong" rather than "the request is wrong".
    rejected_statuses: frozenset[int] = frozenset({401, 403})

    def applies(self, values: Mapping[str, str]) -> bool:
        return all(str(values.get(alias) or "").strip() for alias in self.aliases)


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    label: str
    aliases: tuple[str, ...]
    ok: bool
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "label": self.label,
            "aliases": list(self.aliases),
            "ok": self.ok,
            "message": self.message,
        }


def _bearer(url: str, alias: str, **extra_headers: str):
    def build(values: Mapping[str, str]) -> ProbeRequest:
        return ProbeRequest(
            "GET",
            url,
            {"Authorization": f"Bearer {values[alias].strip()}", **extra_headers},
        )

    return build


def _header(url: str, alias: str, header: str, **extra_headers: str):
    def build(values: Mapping[str, str]) -> ProbeRequest:
        return ProbeRequest("GET", url, {header: values[alias].strip(), **extra_headers})

    return build


def _livekit_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme, parsed.scheme or "https")
    return f"{scheme}://{parsed.netloc or parsed.path}".rstrip("/")


def _livekit(values: Mapping[str, str]) -> ProbeRequest:
    # LiveKit is the customer's own server: sign a room-list grant and call RoomService.
    from livekit import api as livekit_api

    token = (
        livekit_api.AccessToken(
            values["LIVEKIT_API_KEY"].strip(), values["LIVEKIT_API_SECRET"].strip()
        )
        .with_grants(livekit_api.VideoGrants(room_list=True))
        .to_jwt()
    )
    return ProbeRequest(
        "POST",
        f"{_livekit_http_url(values['LIVEKIT_URL'])}/twirp/livekit.RoomService/ListRooms",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


# Transport providers first (they decide how the simulator reaches the agent), then the
# model/speech vendors an agent commonly needs at runtime.
PROVIDER_PROBES: dict[str, ProviderProbe] = {
    "livekit": ProviderProbe(
        "LiveKit", ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"), _livekit
    ),
    "vapi": ProviderProbe(
        "Vapi", ("VAPI_API_KEY",), _bearer("https://api.vapi.ai/assistant?limit=1", "VAPI_API_KEY")
    ),
    "retell": ProviderProbe(
        "Retell",
        ("RETELL_API_KEY",),
        _bearer("https://api.retellai.com/list-agents", "RETELL_API_KEY"),
    ),
    "openai": ProviderProbe(
        "OpenAI",
        ("OPENAI_API_KEY",),
        _bearer("https://api.openai.com/v1/models", "OPENAI_API_KEY"),
    ),
    "anthropic": ProviderProbe(
        "Anthropic",
        ("ANTHROPIC_API_KEY",),
        _header(
            "https://api.anthropic.com/v1/models?limit=1",
            "ANTHROPIC_API_KEY",
            "x-api-key",
            **{"anthropic-version": "2023-06-01"},
        ),
    ),
    "gemini": ProviderProbe(
        "Gemini",
        ("GEMINI_API_KEY",),
        _header(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            "GEMINI_API_KEY",
            "x-goog-api-key",
        ),
        # Google answers an invalid key with 400 INVALID_ARGUMENT, not 401.
        rejected_statuses=frozenset({400, 401, 403}),
    ),
    "deepgram": ProviderProbe(
        "Deepgram",
        ("DEEPGRAM_API_KEY",),
        lambda values: ProbeRequest(
            "GET",
            "https://api.deepgram.com/v1/projects",
            {"Authorization": f"Token {values['DEEPGRAM_API_KEY'].strip()}"},
        ),
    ),
    "cartesia": ProviderProbe(
        "Cartesia",
        ("CARTESIA_API_KEY",),
        _bearer(
            "https://api.cartesia.ai/voices?limit=1",
            "CARTESIA_API_KEY",
            **{"Cartesia-Version": "2025-04-16"},
        ),
    ),
    "elevenlabs": ProviderProbe(
        "ElevenLabs",
        ("ELEVENLABS_API_KEY",),
        _header("https://api.elevenlabs.io/v1/user", "ELEVENLABS_API_KEY", "xi-api-key"),
    ),
}


def probe(provider: str, values: Mapping[str, str]) -> ProbeResult:
    """Exercise one provider's credentials. Callers pass only providers that ``applies``."""
    spec = PROVIDER_PROBES[provider]
    named = " + ".join(spec.aliases)
    try:
        request = spec.build(values)
        response = requests.request(
            request.method,
            request.url,
            headers=dict(request.headers),
            json={} if request.method == "POST" else None,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # The run's own connect stage would hit the same wall, so this is a failure too.
        return ProbeResult(
            provider, spec.label, spec.aliases, False,
            f"{spec.label} is unreachable ({type(exc).__name__}); check {named}",
        )
    except Exception as exc:  # noqa: BLE001 - a malformed URL/secret must not 500 preflight
        return ProbeResult(
            provider, spec.label, spec.aliases, False,
            f"{spec.label} check could not run ({type(exc).__name__}); check {named}",
        )
    if response.status_code in spec.rejected_statuses:
        return ProbeResult(
            provider, spec.label, spec.aliases, False,
            f"{spec.label} rejected {named} (HTTP {response.status_code})",
        )
    if response.status_code >= 400:
        return ProbeResult(
            provider, spec.label, spec.aliases, False,
            f"{spec.label} returned HTTP {response.status_code} for {named}",
        )
    return ProbeResult(
        provider, spec.label, spec.aliases, True, f"{spec.label} accepted {named}"
    )


def probe_all(values: Mapping[str, str]) -> list[ProbeResult]:
    """Probe every provider whose full alias set is present. Order follows the registry."""
    return [
        probe(name, values)
        for name, spec in PROVIDER_PROBES.items()
        if spec.applies(values)
    ]


def probe_provider_target(
    connector: str, target_id: str, values: Mapping[str, str]
) -> ProbeResult | None:
    """Verify that a connected provider target exists and belongs to this key.

    A list endpoint only proves that the credential is valid. It says nothing
    about the assistant/agent ID the user entered, which otherwise fails much
    later inside the guest runtime. Keep this read-only and provider-generic:
    each supported connector contributes only its target lookup endpoint.
    """
    connector = str(connector or "").strip().lower()
    target_id = str(target_id or "").strip()
    if not target_id:
        return None

    escaped_id = quote(target_id, safe="")
    if connector == "vapi":
        label = "Vapi assistant"
        alias = "VAPI_API_KEY"
        url = f"https://api.vapi.ai/assistant/{escaped_id}"
    elif connector == "retell":
        label = "Retell voice agent"
        alias = "RETELL_API_KEY"
        url = f"https://api.retellai.com/get-agent/{escaped_id}"
    elif connector == "retell_chat":
        label = "Retell chat agent"
        alias = "RETELL_API_KEY"
        url = f"https://api.retellai.com/get-chat-agent/{escaped_id}"
    else:
        return None

    api_key = str(values.get(alias) or "").strip()
    if not api_key:
        return None

    aliases = (alias,)
    provider = f"{connector}_target"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return ProbeResult(
            provider,
            label,
            aliases,
            False,
            f"{label} lookup is unreachable ({type(exc).__name__}); retry Preflight",
        )

    if response.status_code == 404:
        return ProbeResult(
            provider,
            label,
            aliases,
            False,
            f"{label} ID was not found or is not accessible with {alias}",
        )
    if response.status_code in {401, 403}:
        return ProbeResult(
            provider,
            label,
            aliases,
            False,
            f"{label} lookup rejected {alias} (HTTP {response.status_code})",
        )
    if response.status_code >= 400:
        return ProbeResult(
            provider,
            label,
            aliases,
            False,
            f"{label} ID could not be validated (HTTP {response.status_code})",
        )
    return ProbeResult(
        provider,
        label,
        aliases,
        True,
        f"{label} ID exists and is accessible with {alias}",
    )
