"""Live, read-only checks that a target-provider credential is accepted by its provider.

Presence of an alias says nothing about whether the key is right; a wrong key otherwise
surfaces from the agent's own startup or first call, minutes and one sandbox later. Each
provider here is one table row: the aliases it needs and the single cheapest request that
only succeeds with a valid key. Adding a provider means adding a row, nothing else.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import requests
import structlog
from google.protobuf import json_format

from tfc.utils.lazy_extras import load_extra

logger = structlog.get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 8
LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS = 8
LIVEKIT_AGENT_POLL_SECONDS = 1
LIVEKIT_ALIASES = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


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
    # The name the provider holds for a looked-up target, so a build can be
    # labelled by it instead of by the opaque ID the user pasted.
    target_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        result = {
            "provider": self.provider,
            "label": self.label,
            "aliases": list(self.aliases),
            "ok": self.ok,
            "message": self.message,
        }
        if self.target_name:
            result["target_name"] = self.target_name
        return result


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


def _dispatched_identities(livekit_api: Any, listing: dict[str, Any]) -> set[str]:
    response = json_format.ParseDict(
        listing, livekit_api.ListAgentDispatchResponse(), ignore_unknown_fields=True
    )
    return {
        job.state.participant_identity
        for dispatch in response.agent_dispatches
        for job in dispatch.state.jobs
        if job.state.participant_identity
    }


def probe_livekit_agent(
    agent_name: str, values: Mapping[str, str]
) -> ProbeResult | None:
    agent_name = str(agent_name or "").strip()
    if not agent_name or not all(
        str(values.get(alias) or "").strip() for alias in LIVEKIT_ALIASES
    ):
        return None

    def result(ok: bool, message: str) -> ProbeResult:
        return ProbeResult(
            "livekit_target",
            "LiveKit agent",
            LIVEKIT_ALIASES,
            ok,
            message,
            agent_name if ok else "",
        )

    try:
        livekit_api = load_extra("livekit.api", "voice")
    except ImportError as exc:
        logger.warning("livekit_agent_probe_unavailable", exc_info=True)
        return result(False, str(exc))

    room = f"_preflight_agent_{uuid.uuid4().hex[:8]}"
    base_url = f"{_livekit_http_url(values['LIVEKIT_URL'])}/twirp"
    token = (
        livekit_api.AccessToken(
            values["LIVEKIT_API_KEY"].strip(), values["LIVEKIT_API_SECRET"].strip()
        )
        .with_grants(
            livekit_api.VideoGrants(
                room_create=True, room_admin=True, room_list=True, room=room
            )
        )
        .to_jwt()
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(service: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{base_url}/livekit.{service}/{method}",
            headers=headers,
            json=body,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    try:
        call(
            "RoomService",
            "CreateRoom",
            {"name": room, "empty_timeout": LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS + 15},
        )
        try:
            dispatch = call(
                "AgentDispatchService",
                "CreateDispatch",
                {"agent_name": agent_name, "room": room},
            )
            dispatch_id = dispatch.get("id") or ""
            deadline = time.monotonic() + LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS
            while dispatch_id and time.monotonic() < deadline:
                identities = _dispatched_identities(
                    livekit_api,
                    call(
                        "AgentDispatchService",
                        "ListDispatch",
                        {"dispatch_id": dispatch_id, "room": room},
                    ),
                )
                if identities:
                    listing = call("RoomService", "ListParticipants", {"room": room})
                    if any(
                        item.get("identity") in identities
                        for item in listing.get("participants") or []
                    ):
                        return result(
                            True, f"LiveKit agent '{agent_name}' joined a test room"
                        )
                time.sleep(LIVEKIT_AGENT_POLL_SECONDS)
        finally:
            try:
                call("RoomService", "DeleteRoom", {"room": room})
            except requests.RequestException:
                logger.warning(
                    "livekit_agent_probe_room_cleanup_failed", room=room, exc_info=True
                )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in {401, 403}:
            return result(
                False,
                f"LiveKit rejected LIVEKIT_API_KEY + LIVEKIT_API_SECRET (HTTP {status})",
            )
        logger.warning("livekit_agent_probe_http_error", status=status, exc_info=True)
        return result(
            False,
            f"LiveKit returned HTTP {status} while checking agent '{agent_name}'",
        )
    except requests.RequestException as exc:
        logger.warning("livekit_agent_probe_unreachable", exc_info=True)
        return result(
            False, f"LiveKit is unreachable ({type(exc).__name__}); retry Preflight"
        )
    return result(
        False,
        f"No agent named '{agent_name}' joined a test room within "
        f"{LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS}s; check the agent name and that "
        "the agent is running on this LiveKit project",
    )


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
    if connector == "livekit":
        return probe_livekit_agent(target_id, values)

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
    try:
        body = response.json()
    except ValueError:
        body = None
    name_field = "name" if connector == "vapi" else "agent_name"
    target_name = (
        str(body.get(name_field) or "").strip()[:255] if isinstance(body, dict) else ""
    )
    return ProbeResult(
        provider,
        label,
        aliases,
        True,
        f"{label} ID exists and is accessible with {alias}",
        target_name,
    )


# Entra error codes the form can name in plain words. Anything else shows Entra's own text.
_ENTRA_ERRORS = {
    "AADSTS90002": "Tenant ID was not found",
    "AADSTS900023": "Tenant ID is not a valid tenant",
    "AADSTS700016": "Client ID was not found in this tenant",
    "AADSTS7000215": "Client secret is wrong; copy the secret's Value, not its ID",
    "AADSTS7000222": "Client secret has expired; create a new one",
}


def _entra_reason(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    description = str(body.get("error_description") or body.get("error") or "")
    for code, reason in _ENTRA_ERRORS.items():
        if code in description:
            return reason
    # Entra appends trace and correlation IDs on new lines; keep only the explanation.
    return " ".join(description.split("Trace ID")[0].split())[:200] or (
        f"HTTP {response.status_code}"
    )


def _microsoft_token(
    connector: str, config: Mapping[str, Any], client_secret: str
) -> tuple[ProbeResult, str]:
    from simulate.services.microsoft_agents import (
        MICROSOFT_CLIENT_SECRET,
        token_scope,
        token_url,
    )

    aliases = (MICROSOFT_CLIENT_SECRET,)

    def result(ok: bool, message: str) -> ProbeResult:
        return ProbeResult(connector, "Microsoft Entra app", aliases, ok, message)

    try:
        response = requests.post(
            token_url(str(config.get("tenant_id") or "")),
            data={
                "grant_type": "client_credentials",
                "client_id": str(config.get("client_id") or "").strip(),
                "client_secret": client_secret,
                "scope": token_scope(connector, config),
            },
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return result(
            False, f"Microsoft sign-in is unreachable ({type(exc).__name__}); retry"
        ), ""
    if response.status_code >= 400:
        return result(
            False, f"Microsoft Entra rejected the app: {_entra_reason(response)}"
        ), ""
    try:
        token = str(response.json().get("access_token") or "")
    except ValueError:
        token = ""
    if not token:
        return result(False, "Microsoft Entra returned no access token"), ""
    return result(
        True, "Microsoft Entra accepted the tenant, client ID and secret"
    ), token


def _microsoft_get(url: str, token: str) -> requests.Response:
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=PROBE_TIMEOUT_SECONDS,
    )


def _copilot_studio_agent(config: Mapping[str, Any], token: str) -> ProbeResult:
    from simulate.services.microsoft_agents import (
        COPILOT_STUDIO,
        DATAVERSE_API_PATH,
        MICROSOFT_CLIENT_SECRET,
        endpoint_host,
    )

    aliases = (MICROSOFT_CLIENT_SECRET,)
    agent_id = str(config.get("agent_id") or "").strip()
    base = f"https://{endpoint_host(COPILOT_STUDIO, config)}/{DATAVERSE_API_PATH}"

    def result(ok: bool, message: str, name: str = "") -> ProbeResult:
        return ProbeResult(
            f"{COPILOT_STUDIO}_target",
            "Copilot Studio agent",
            aliases,
            ok,
            message,
            name,
        )

    try:
        bot = _microsoft_get(
            f"{base}/bots({quote(agent_id, safe='')})?$select=name", token
        )
        if bot.status_code in {401, 403}:
            return result(
                False,
                "Dataverse refused the app: add it as an Application user in this "
                "environment with a security role that can read Copilot rows",
            )
        if bot.status_code in {400, 404}:
            return result(
                False,
                f"No Copilot Studio agent with ID {agent_id} in this environment; copy the "
                "Agent ID from Settings > Advanced > Metadata",
            )
        if bot.status_code >= 400:
            return result(
                False,
                f"Copilot Studio agent could not be read (HTTP {bot.status_code})",
            )
        # The build reads the agent's parts, so check that role covers them too.
        components = _microsoft_get(
            f"{base}/botcomponents?$select=componenttype&$top=1"
            f"&$filter=_parentbotid_value%20eq%20{quote(agent_id, safe='')}",
            token,
        )
    except requests.RequestException as exc:
        return result(
            False, f"Dataverse is unreachable ({type(exc).__name__}); check the URL"
        )
    if components.status_code in {401, 403}:
        return result(
            False,
            "The app can read the agent but not its parts; give its security role "
            "read access to Copilot component rows",
        )
    if components.status_code >= 400:
        return result(
            False,
            f"Copilot Studio agent parts could not be read (HTTP {components.status_code})",
        )
    try:
        name = str(bot.json().get("name") or "").strip()[:255]
    except ValueError:
        name = ""
    return result(True, f"Copilot Studio agent '{name or agent_id}' is readable", name)


def _foundry_agent(config: Mapping[str, Any], token: str) -> ProbeResult:
    from simulate.services.microsoft_agents import (
        FOUNDRY,
        FOUNDRY_DEFAULT_API_VERSION,
        MICROSOFT_CLIENT_SECRET,
    )

    aliases = (MICROSOFT_CLIENT_SECRET,)
    agent_name = str(config.get("agent_name") or "").strip()
    endpoint = str(config.get("project_endpoint") or "").strip().rstrip("/")
    api_version = str(config.get("api_version") or "") or FOUNDRY_DEFAULT_API_VERSION

    def result(ok: bool, message: str, name: str = "") -> ProbeResult:
        return ProbeResult(
            f"{FOUNDRY}_target", "Foundry agent", aliases, ok, message, name
        )

    try:
        response = _microsoft_get(
            f"{endpoint}/agents/{quote(agent_name, safe='')}?api-version={api_version}",
            token,
        )
    except requests.RequestException as exc:
        return result(
            False,
            f"Foundry project is unreachable ({type(exc).__name__}); check the endpoint",
        )
    if response.status_code in {401, 403}:
        return result(
            False,
            "Foundry refused the app: give it the Foundry User role on the Foundry resource",
        )
    if response.status_code == 404:
        return result(False, f"No agent named '{agent_name}' in this Foundry project")
    if response.status_code >= 400:
        return result(
            False, f"Foundry agent could not be read (HTTP {response.status_code})"
        )
    try:
        name = str(response.json().get("name") or "").strip()[:255]
    except ValueError:
        name = ""
    return result(True, f"Foundry agent '{name or agent_name}' is readable", name)


def _directline_secret(secret: str) -> ProbeResult:
    from simulate.services.microsoft_agents import (
        COPILOT_DIRECTLINE_SECRET,
        DIRECTLINE_HOST,
    )

    aliases = (COPILOT_DIRECTLINE_SECRET,)
    label = "Copilot Studio Direct Line"
    try:
        response = requests.post(
            f"https://{DIRECTLINE_HOST}/v3/directline/tokens/generate",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return ProbeResult(
            "copilot_directline",
            label,
            aliases,
            False,
            f"Direct Line is unreachable ({type(exc).__name__}); retry",
        )
    if response.status_code >= 400:
        return ProbeResult(
            "copilot_directline",
            label,
            aliases,
            False,
            f"Direct Line rejected {COPILOT_DIRECTLINE_SECRET} "
            f"(HTTP {response.status_code}); copy Secret 1 from Settings > Security > "
            "Web channel security",
        )
    return ProbeResult(
        "copilot_directline",
        label,
        aliases,
        True,
        f"Direct Line accepted {COPILOT_DIRECTLINE_SECRET}",
    )


def probe_microsoft_agent(
    connector: str, config: Mapping[str, Any], values: Mapping[str, str]
) -> list[ProbeResult]:
    """Sign in as the customer's Entra app, then read the agent the way the build will.

    Each step names what to fix: the token step catches a wrong tenant, client ID or secret;
    the read step catches a missing role or a wrong agent ID. The optional Direct Line secret
    is checked only when given, because a trial agent cannot be published.
    """
    from simulate.services.microsoft_agents import (
        COPILOT_DIRECTLINE_SECRET,
        COPILOT_STUDIO,
        MICROSOFT_CLIENT_SECRET,
        microsoft_config_error,
    )

    results: list[ProbeResult] = []
    secret = str(values.get(MICROSOFT_CLIENT_SECRET) or "").strip()
    if secret and microsoft_config_error(connector, config) is None:
        token_result, token = _microsoft_token(connector, config, secret)
        results.append(token_result)
        if token:
            read = (
                _copilot_studio_agent if connector == COPILOT_STUDIO else _foundry_agent
            )
            results.append(read(config, token))
    directline = str(values.get(COPILOT_DIRECTLINE_SECRET) or "").strip()
    if connector == COPILOT_STUDIO and directline:
        results.append(_directline_secret(directline))
    return results
