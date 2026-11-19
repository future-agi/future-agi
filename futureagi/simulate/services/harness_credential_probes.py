"""Live, read-only checks that a target-provider credential is accepted by its provider.

Presence of an alias says nothing about whether the key is right; a wrong key otherwise
surfaces from the agent's own startup or first call, minutes and one sandbox later. Each
provider here is one table row: the aliases it needs and the single cheapest request that
only succeeds with a valid key. Adding a provider means adding a row, nothing else.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlparse

import requests
import structlog
from google.protobuf import json_format

from tfc.utils.lazy_extras import load_extra
from tfc.utils.ssrf_guard import SsrfBlocked, assert_url_host_public

logger = structlog.get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 8
LIVEKIT_AGENT_JOIN_TIMEOUT_SECONDS = 8
LIVEKIT_AGENT_POLL_SECONDS = 1
LIVEKIT_ALIASES = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
LIVEKIT_PROBE_BUDGET_SECONDS = 20
LIVEKIT_MAX_RESPONSE_BYTES = 1024 * 1024
LIVEKIT_TOKEN_TTL = timedelta(seconds=60)


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
        return ProbeRequest(
            "GET", url, {header: values[alias].strip(), **extra_headers}
        )

    return build


class LiveKitUnreadable(ValueError):
    pass


def _livekit_base_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if (
        parsed.scheme not in ("https", "wss")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise SsrfBlocked(
            "LIVEKIT_URL must be an https:// or wss:// address with a host"
        )
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    base = f"https://{host}" + (f":{parsed.port}" if parsed.port else "")
    assert_url_host_public(base)
    return base


def _livekit_call(
    base: str,
    headers: Mapping[str, str],
    service: str,
    method: str,
    body: Mapping[str, Any],
    deadline: float,
) -> dict[str, Any]:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise requests.Timeout("the LiveKit check ran out of time")
    response = requests.post(
        f"{base}/twirp/livekit.{service}/{method}",
        headers=dict(headers),
        json=dict(body),
        timeout=min(PROBE_TIMEOUT_SECONDS, remaining),
        allow_redirects=False,
        stream=True,
    )
    try:
        if 300 <= response.status_code < 400:
            raise requests.HTTPError(
                f"LiveKit redirected (HTTP {response.status_code})", response=response
            )
        response.raise_for_status()
        content = (
            response.raw.read(LIVEKIT_MAX_RESPONSE_BYTES + 1, decode_content=True)
            or b""
        )
        if len(content) > LIVEKIT_MAX_RESPONSE_BYTES:
            raise LiveKitUnreadable("the response is larger than 1 MiB")
        listing = json.loads(content) if content else {}
        if not isinstance(listing, dict):
            raise LiveKitUnreadable("the response is not a JSON object")
        return listing
    finally:
        response.close()


def _livekit_token(livekit_api: Any, values: Mapping[str, str]) -> Any:
    return livekit_api.AccessToken(
        values["LIVEKIT_API_KEY"].strip(), values["LIVEKIT_API_SECRET"].strip()
    ).with_ttl(LIVEKIT_TOKEN_TTL)


def _livekit(values: Mapping[str, str]) -> ProbeRequest:
    # LiveKit is the customer's own server: sign a room-list grant and call RoomService.
    from livekit import api as livekit_api

    token = (
        _livekit_token(livekit_api, values)
        .with_grants(livekit_api.VideoGrants(room_list=True))
        .to_jwt()
    )
    return ProbeRequest(
        "POST",
        f"{_livekit_base_url(values['LIVEKIT_URL'])}/twirp/livekit.RoomService/ListRooms",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


# Transport providers first (they decide how the simulator reaches the agent), then the
# model/speech vendors an agent commonly needs at runtime.
PROVIDER_PROBES: dict[str, ProviderProbe] = {
    "livekit": ProviderProbe(
        "LiveKit", ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"), _livekit
    ),
    "vapi": ProviderProbe(
        "Vapi",
        ("VAPI_API_KEY",),
        _bearer("https://api.vapi.ai/assistant?limit=1", "VAPI_API_KEY"),
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
        _header(
            "https://api.elevenlabs.io/v1/user", "ELEVENLABS_API_KEY", "xi-api-key"
        ),
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
            allow_redirects=False,
        )
    except SsrfBlocked as exc:
        return ProbeResult(
            provider,
            spec.label,
            spec.aliases,
            False,
            f"{spec.label} URL refused: {exc}",
        )
    except requests.RequestException as exc:
        # The run's own connect stage would hit the same wall, so this is a failure too.
        return ProbeResult(
            provider,
            spec.label,
            spec.aliases,
            False,
            f"{spec.label} is unreachable ({type(exc).__name__}); check {named}",
        )
    except Exception as exc:  # noqa: BLE001 - a malformed URL/secret must not 500 preflight
        return ProbeResult(
            provider,
            spec.label,
            spec.aliases,
            False,
            f"{spec.label} check could not run ({type(exc).__name__}); check {named}",
        )
    if 300 <= response.status_code < 400:
        return ProbeResult(
            provider,
            spec.label,
            spec.aliases,
            False,
            f"{spec.label} redirected the check (HTTP {response.status_code});"
            f" check {named}",
        )
    if response.status_code in spec.rejected_statuses:
        return ProbeResult(
            provider,
            spec.label,
            spec.aliases,
            False,
            f"{spec.label} rejected {named} (HTTP {response.status_code})",
        )
    if response.status_code >= 400:
        return ProbeResult(
            provider,
            spec.label,
            spec.aliases,
            False,
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
    try:
        base = _livekit_base_url(values["LIVEKIT_URL"])
    except (SsrfBlocked, ValueError) as exc:
        return result(False, f"LiveKit URL refused: {exc}")
    token = (
        _livekit_token(livekit_api, values)
        .with_grants(
            livekit_api.VideoGrants(
                room_create=True, room_admin=True, room_list=True, room=room
            )
        )
        .to_jwt()
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    budget = time.perf_counter() + LIVEKIT_PROBE_BUDGET_SECONDS

    def call(
        service: str, method: str, body: dict[str, Any], deadline: float = 0.0
    ) -> dict[str, Any]:
        return _livekit_call(base, headers, service, method, body, deadline or budget)

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
                call(
                    "RoomService",
                    "DeleteRoom",
                    {"room": room},
                    time.perf_counter() + PROBE_TIMEOUT_SECONDS,
                )
            except (requests.RequestException, ValueError):
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
    except (ValueError, json_format.ParseError) as exc:
        logger.warning("livekit_agent_probe_bad_response", exc_info=True)
        return result(
            False,
            f"LiveKit returned an unreadable response ({type(exc).__name__}); "
            "retry Preflight",
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


LIVEKIT_ROUTING_ALIASES = (*LIVEKIT_ALIASES, "LIVEKIT_AGENT_NAME")
# Twirp answers a service the server does not expose with 404 (bad_route) or 501
# (unimplemented); self-hosted LiveKit has no PhoneNumberService.
_LIVEKIT_NO_PHONE_NUMBER_SERVICE = frozenset({404, 501})


@dataclass(frozen=True)
class CloudPhoneNumber:
    e164: str
    rule_ids: frozenset[str]


def _cloud_phone_numbers(items: list[dict[str, Any]]) -> list[CloudPhoneNumber]:
    numbers = []
    for item in items:
        rule_ids = [
            item.get("sip_dispatch_rule_id"),
            *(item.get("sip_dispatch_rule_ids") or []),
        ]
        numbers.append(
            CloudPhoneNumber(
                str(item.get("e164_format") or "").strip(),
                frozenset(str(rule_id) for rule_id in rule_ids if rule_id),
            )
        )
    return numbers


def _accepts_caller(allowed: Iterable[str], caller_number: str) -> bool:
    allowed = list(allowed)
    return not caller_number or not allowed or caller_number in allowed


def _rule_pin(rule: Any) -> str:
    kind = rule.rule.WhichOneof("rule")
    return str(getattr(rule.rule, kind).pin) if kind else ""


def _phone_routing_verdict(
    phone_number: str,
    agent_name: str,
    caller_number: str,
    cloud_numbers: Sequence[CloudPhoneNumber],
    trunks: Sequence[Any],
    rules: Sequence[Any],
) -> tuple[bool, str]:
    """Decide, from a LiveKit project's listings, whether a number reaches one agent.

    ``trunks`` and ``rules`` are LiveKit ``SIPInboundTrunkInfo`` and
    ``SIPDispatchRuleInfo`` messages. Trunk selection follows LiveKit's own order: a
    trunk that lists the number wins; otherwise the project's default trunk (one that
    lists no numbers) takes every call.
    """
    owned = [item for item in cloud_numbers if item.e164 == phone_number]
    listing = [trunk for trunk in trunks if phone_number in trunk.numbers]
    holding = listing or [trunk for trunk in trunks if not trunk.numbers]
    if not owned and not holding:
        return False, (
            f"{phone_number} is not on this LiveKit project: no LiveKit phone "
            "number or inbound trunk lists or accepts it"
        )
    if len(holding) > 1:
        names = ", ".join(sorted(trunk.name or trunk.sip_trunk_id for trunk in holding))
        what = "list" if listing else "accept any number, including"
        return False, (
            f"{len(holding)} inbound trunks {what} {phone_number} ({names}); keep "
            "one so LiveKit knows where to send our call"
        )
    for trunk in holding:
        if not _accepts_caller(trunk.allowed_numbers, caller_number):
            return False, (
                f"inbound trunk {trunk.name or trunk.sip_trunk_id} only accepts "
                f"calls from its allowed numbers; add {caller_number}, the number "
                "we call from"
            )
    trunk_ids = {trunk.sip_trunk_id for trunk in holding}
    assigned = (
        frozenset().union(*(item.rule_ids for item in owned)) if owned else frozenset()
    )
    candidates = [
        rule for rule in rules if _accepts_caller(rule.inbound_numbers, caller_number)
    ]
    specific = [
        rule
        for rule in candidates
        if rule.sip_dispatch_rule_id in assigned
        or phone_number in rule.numbers
        or trunk_ids & set(rule.trunk_ids)
    ]
    catch_all = [rule for rule in candidates if not rule.trunk_ids and not rule.numbers]
    applying = specific or catch_all
    if not applying:
        return False, (
            f"no dispatch rule routes calls to {phone_number}, so our call would "
            "ring into an empty room"
        )
    if any(_rule_pin(rule) for rule in applying):
        return False, (
            f"the dispatch rule for {phone_number} asks callers for a PIN, which "
            "our simulator cannot enter; use a rule without a PIN"
        )
    agents = {
        agent.agent_name.strip()
        for rule in applying
        for agent in rule.room_config.agents
        if agent.agent_name.strip()
    }
    if not agents:
        return False, (
            f"the dispatch rule for {phone_number} names no agent; add "
            f"'{agent_name}' to the rule's agents"
        )
    if agent_name not in agents:
        return False, (
            f"calls to {phone_number} go to {', '.join(sorted(agents))}, "
            f"not '{agent_name}'"
        )
    if len(agents) > 1:
        others = ", ".join(sorted(agents - {agent_name}))
        return False, (
            f"calls to {phone_number} also go to {others}; route this number "
            f"to '{agent_name}' only"
        )
    message = f"calls to {phone_number} are dispatched to '{agent_name}' only"
    if holding:
        message += (
            "; your carrier must also forward this number to LiveKit, which only "
            "a call can prove"
        )
    return True, message


def probe_livekit_phone_routing(
    phone_number: str, values: Mapping[str, str], caller_number: str = ""
) -> ProbeResult | None:
    phone_number = str(phone_number or "").strip()
    agent_name = str(values.get("LIVEKIT_AGENT_NAME") or "").strip()
    if (
        not phone_number
        or not agent_name
        or not all(str(values.get(alias) or "").strip() for alias in LIVEKIT_ALIASES)
    ):
        return None

    def result(ok: bool, message: str) -> ProbeResult:
        return ProbeResult(
            "livekit_routing",
            "LiveKit phone routing",
            LIVEKIT_ROUTING_ALIASES,
            ok,
            message,
            agent_name if ok else "",
        )

    try:
        livekit_api = load_extra("livekit.api", "voice")
    except ImportError as exc:
        logger.warning("livekit_routing_probe_unavailable", exc_info=True)
        return result(False, str(exc))

    try:
        base = _livekit_base_url(values["LIVEKIT_URL"])
    except (SsrfBlocked, ValueError) as exc:
        return result(False, f"LiveKit URL refused: {exc}")
    token = (
        _livekit_token(livekit_api, values)
        .with_sip_grants(livekit_api.SIPGrants(admin=True))
        .to_jwt()
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    budget = time.perf_counter() + LIVEKIT_PROBE_BUDGET_SECONDS

    def listing(service: str, method: str) -> dict[str, Any]:
        return _livekit_call(base, headers, service, method, {}, budget)

    try:
        trunks = json_format.ParseDict(
            listing("SIP", "ListSIPInboundTrunk"),
            livekit_api.ListSIPInboundTrunkResponse(),
            ignore_unknown_fields=True,
        ).items
        rules = json_format.ParseDict(
            listing("SIP", "ListSIPDispatchRule"),
            livekit_api.ListSIPDispatchRuleResponse(),
            ignore_unknown_fields=True,
        ).items
        try:
            cloud_numbers = _cloud_phone_numbers(
                list(
                    listing("PhoneNumberService", "ListPhoneNumbers").get("items") or []
                )
            )
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            # The key already listed trunks and rules, so a 401/403 here means it
            # lacks phone-number access, not that it is wrong.
            if status not in _LIVEKIT_NO_PHONE_NUMBER_SERVICE | {401, 403}:
                raise
            cloud_numbers = []
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in {401, 403}:
            return result(
                False,
                f"LiveKit rejected LIVEKIT_API_KEY + LIVEKIT_API_SECRET (HTTP {status})",
            )
        logger.warning("livekit_routing_probe_http_error", status=status, exc_info=True)
        return result(
            False, f"LiveKit returned HTTP {status} while checking {phone_number}"
        )
    except (ValueError, json_format.ParseError) as exc:
        logger.warning("livekit_routing_probe_bad_response", exc_info=True)
        return result(
            False,
            f"LiveKit returned an unreadable response ({type(exc).__name__}); "
            "retry Preflight",
        )
    except requests.RequestException as exc:
        logger.warning("livekit_routing_probe_unreachable", exc_info=True)
        return result(
            False, f"LiveKit is unreachable ({type(exc).__name__}); retry Preflight"
        )
    ok, message = _phone_routing_verdict(
        phone_number, agent_name, caller_number, cloud_numbers, trunks, rules
    )
    return result(ok, message)


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
