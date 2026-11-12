"""A Vapi or Retell agent as its provider holds it: the profile read with the
agent's key, the digest that says whether it changed, and the agent document the
platform maps it into. No model run: the settings are already structured."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
import structlog
from django.conf import settings

from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

VAPI = "vapi"
RETELL = "retell"
PROVIDERS = (VAPI, RETELL)
_BASE_URLS = {VAPI: "https://api.vapi.ai", RETELL: "https://api.retellai.com"}
_TIMEOUT = (5, 30)
_REDACTED = "[redacted]"
_SECRET_WORDS = frozenset(
    {
        "secret",
        "token",
        "password",
        "passwd",
        "credential",
        "credentials",
        "authorization",
        "apikey",
        "cookie",
        "key",
        "auth",
        "bearer",
        "jwt",
        "signature",
    }
)
_VALUE_MAPS = frozenset({"headers", "query_params", "queryparams"})
_SECRET_PAIRS = (("api", "key"), ("private", "key"), ("access", "key"))
_KEY_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
MAIN_UNIT = "main"
SETUP_HASH_PREFIX = "sh1:"
PLATFORM_SDK_VERSION = "platform"
_NAME_MAX = 255


def _secret_key(key: str) -> bool:
    """Whether a key names a secret, read word by word so ``maxTokens`` stays and
    ``X-API-Key`` does not."""
    words = [word.lower() for word in _KEY_WORDS.findall(key)]
    if _SECRET_WORDS.intersection(words):
        return True
    return any(pair in zip(words, words[1:], strict=False) for pair in _SECRET_PAIRS)


def _safe(value: Any, key: str = "") -> Any:
    """Provider configuration safe to keep: secrets, header values and URL queries
    removed, the same way agent-learning-kit's provider import keeps a profile."""
    if _secret_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        if key.lower().replace("_", "") in _VALUE_MAPS:
            return {str(k): _REDACTED for k in value}
        return {str(k): _safe(item, str(k)) for k, item in value.items()}
    if isinstance(value, list):
        return [_safe(item, key) for item in value]
    if isinstance(value, str) and "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.netloc:
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except ValueError:
                port = ""
            return urlunsplit(
                (parsed.scheme, (parsed.hostname or "") + port, parsed.path, "", "")
            )
    return value


def _get(url: str, api_key: str) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "futureagi-harness",
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise HostedHarnessError(
            "provider_unreachable",
            "The provider could not be reached; try again",
            status_code=503,
            retryable=True,
            details={"error": type(exc).__name__},
        ) from exc
    if response.status_code in (401, 403):
        raise HostedHarnessError(
            "provider_key_rejected",
            "The provider refused the agent's API key; update the key first",
            status_code=422,
        )
    if response.status_code == 404:
        raise HostedHarnessError(
            "provider_agent_not_found",
            "The agent no longer exists at the provider",
            status_code=422,
        )
    if response.status_code >= 400:
        raise HostedHarnessError(
            "provider_unreachable",
            f"The provider answered {response.status_code}",
            status_code=503,
            retryable=response.status_code >= 500,
            details={"status": response.status_code},
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise HostedHarnessError(
            "provider_response_invalid",
            "The provider's answer was not JSON",
            status_code=503,
            retryable=True,
        ) from exc
    if not isinstance(body, dict):
        raise HostedHarnessError(
            "provider_response_invalid",
            "The provider's answer was not an object",
            status_code=503,
            retryable=True,
        )
    return body


def read_profile(
    provider: str, *, assistant_id: str, api_key: str, modality: str
) -> dict[str, Any]:
    """The agent's behavioural profile as agent-learning-kit reads it at build
    time: the same endpoints and fields, so both sides see one agent."""
    if provider not in PROVIDERS:
        raise HostedHarnessError(
            "sync_unsupported",
            "Only a Vapi or Retell agent can be synced from its provider",
            status_code=422,
        )
    if not api_key:
        raise HostedHarnessError(
            "provider_key_missing",
            "This agent has no provider API key to read it with",
            status_code=422,
        )
    if not assistant_id:
        raise HostedHarnessError(
            "provider_agent_missing",
            "This agent names no assistant at the provider",
            status_code=422,
        )
    base = str(
        getattr(settings, f"{provider.upper()}_API_BASE_URL", "")
        or _BASE_URLS[provider]
    ).rstrip("/")
    if provider == VAPI:
        assistant = _get(f"{base}/assistant/{assistant_id}", api_key)
        model = assistant.get("model")
        reusable = []
        if isinstance(model, Mapping):
            for tool_id in model.get("toolIds") or []:
                reusable.append(_get(f"{base}/tool/{tool_id}", api_key))
        profile = {
            "provider": VAPI,
            "source_target_id": assistant_id,
            "name": assistant.get("name"),
            "first_message": assistant.get("firstMessage"),
            "first_message_mode": assistant.get("firstMessageMode"),
            "model": model,
            "voice": assistant.get("voice"),
            "transcriber": assistant.get("transcriber"),
            "end_call_message": assistant.get("endCallMessage"),
            "end_call_phrases": assistant.get("endCallPhrases"),
            "reusable_tools": reusable,
        }
        return _safe(profile)
    path = "get-chat-agent" if modality == "chat" else "get-agent"
    agent = _get(f"{base}/{path}/{assistant_id}", api_key)
    engine = agent.get("response_engine")
    engine_type = (
        str((engine or {}).get("type") or "").strip()
        if isinstance(engine, Mapping)
        else ""
    )
    profile = {
        "provider": RETELL,
        "modality": modality,
        "source_target_id": assistant_id,
        "name": agent.get("agent_name"),
        "voice_id": agent.get("voice_id"),
        "language": agent.get("language"),
        "responsiveness": agent.get("responsiveness"),
        "interruption_sensitivity": agent.get("interruption_sensitivity"),
        "enable_backchannel": agent.get("enable_backchannel"),
        "response_engine_type": engine_type,
    }
    if engine_type == "retell-llm":
        llm_id = str(engine.get("llm_id") or "").strip()
        if not llm_id:
            raise _engine_unsupported("the response engine names no llm_id")
        llm = _get(f"{base}/get-retell-llm/{llm_id}", api_key)
        profile.update(
            {
                "begin_message": llm.get("begin_message"),
                "general_prompt": llm.get("general_prompt"),
                "general_tools": llm.get("general_tools"),
                "states": llm.get("states"),
                "model": llm.get("model"),
            }
        )
    elif engine_type == "conversation-flow":
        flow_id = str(engine.get("conversation_flow_id") or "").strip()
        if not flow_id:
            raise _engine_unsupported("the response engine names no conversation flow")
        profile["conversation_flow"] = _get(
            f"{base}/get-conversation-flow/{flow_id}", api_key
        )
    else:
        raise _engine_unsupported(
            f"a {engine_type or 'missing'} response engine can't be read; "
            "retell-llm and conversation-flow can"
        )
    return _safe(profile)


CONNECTOR_PROVIDERS = {"vapi": VAPI, "retell": RETELL, "retell_chat": RETELL}


def profile_for_version(version) -> dict[str, Any] | None:
    """The provider's definition of the agent as a version holds it: the profile its
    stored document carries, else one read now with the version's key. None for a
    version of anything but a Vapi or Retell agent, or when it can't be read."""
    from simulate.models import AgentDefinition, HarnessAgentVersionRuntime
    from simulate.services.agent_definition import resolve_api_key_for_version
    from simulate.services.agent_setup import stored_agent

    document = stored_agent(version.id, with_raw_setup=True, include_stale=True) or {}
    stored = (document.get("extension") or {}).get("profile")
    if isinstance(stored, dict) and stored:
        return stored
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=version)
        .defer("credential_blob", "diff")
        .first()
    )
    if runtime is None or runtime.source_kind != "provider":
        return None
    definition = AgentDefinition.no_workspace_objects.filter(
        id=version.agent_definition_id
    ).first()
    source = runtime.source or {}
    connector = str(source.get("provider") or getattr(definition, "provider", "") or "")
    provider = CONNECTOR_PROVIDERS.get(connector)
    if provider is None or definition is None:
        return None
    modality = (
        "chat"
        if connector == "retell_chat"
        or definition.agent_type != AgentDefinition.AgentTypeChoices.VOICE
        else "voice"
    )
    try:
        return read_profile(
            provider,
            assistant_id=str(
                source.get("assistant_id") or definition.assistant_id or ""
            ),
            api_key=resolve_api_key_for_version(version) or "",
            modality=modality,
        )
    except HostedHarnessError as exc:
        logger.warning(
            "provider_profile_unavailable",
            agent_version_id=str(version.id),
            code=exc.code,
        )
        return None


def profile_bytes_for_version(version) -> bytes | None:
    """``profile_for_version`` as the file agent-learning-kit reads it from."""
    profile = profile_for_version(version)
    if profile is None:
        return None
    return (json.dumps(profile, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _engine_unsupported(detail: str) -> HostedHarnessError:
    return HostedHarnessError(
        "retell_response_engine_unsupported",
        f"This Retell agent can't be synced: {detail}",
        status_code=422,
    )


def profile_digest(profile: Mapping[str, Any]) -> str:
    """What a Sync compares: the profile's content, nothing about when it was read."""
    body = json.dumps(
        profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def compute_setup_hash(agent: Mapping[str, Any]) -> str:
    """agent-learning-kit's setup hash, so a document the platform maps and one the
    SDK sends for the same agent compare equal."""
    hashed = copy.deepcopy(dict(agent))
    hashed.pop("setup_hash", None)
    hashed.pop("direction_answer", None)
    if isinstance(hashed.get("provider_ref"), dict):
        hashed["provider_ref"].pop("version", None)
    body = json.dumps(
        hashed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return SETUP_HASH_PREFIX + hashlib.sha256(body).hexdigest()


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _tool(name: str, description: Any, kind: str, parameters: Any, url: Any):
    return {
        "name": str(name)[:_NAME_MAX],
        "description": _text(description),
        "kind": kind,
        "parameters": parameters if isinstance(parameters, Mapping) else None,
        "endpoint": {"url": str(url)} if url else None,
    }


def _vapi_tools(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    model = profile.get("model") if isinstance(profile.get("model"), Mapping) else {}
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in [*(model.get("tools") or []), *(profile.get("reusable_tools") or [])]:
        if not isinstance(raw, Mapping):
            continue
        function = (
            raw.get("function") if isinstance(raw.get("function"), Mapping) else {}
        )
        name = _text(function.get("name")) or _text(raw.get("name"))
        kind = str(raw.get("type") or "")
        if not name:
            name = kind or None
        if not name or name in seen:
            continue
        seen.add(name)
        server = raw.get("server") if isinstance(raw.get("server"), Mapping) else {}
        url = server.get("url")
        found.append(
            _tool(
                name,
                function.get("description") or raw.get("description"),
                "http" if url else "builtin",
                function.get("parameters"),
                url,
            )
        )
    return found


def _vapi_prompt(profile: Mapping[str, Any]) -> str | None:
    model = profile.get("model") if isinstance(profile.get("model"), Mapping) else {}
    parts = [
        str(message.get("content") or "")
        for message in model.get("messages") or []
        if isinstance(message, Mapping) and message.get("role") == "system"
    ]
    return _text("\n\n".join(part for part in parts if part.strip()))


def _retell_tools(raw_tools: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_tools or []:
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("type") or "")
        name = _text(raw.get("name")) or kind or None
        if not name or name in seen:
            continue
        seen.add(name)
        url = raw.get("url") if kind == "custom" else None
        found.append(
            _tool(
                name,
                raw.get("description"),
                "http" if url else "builtin",
                raw.get("parameters"),
                url,
            )
        )
    return found


def profile_document(
    provider: str,
    assistant_id: str,
    profile: Mapping[str, Any],
    *,
    modality: str,
) -> dict[str, Any]:
    """The agent document for a provider profile, in the shape the SDK stores for a
    hosted agent, so the compatibility check and the Definition tab read it as
    they read any other."""
    agent_type = "voice" if modality == "voice" else "chat"
    gaps: list[str] = []
    if provider == VAPI:
        tools = _vapi_tools(profile)
        prompt = _vapi_prompt(profile)
        model = (
            profile.get("model") if isinstance(profile.get("model"), Mapping) else {}
        )
        voice = (
            profile.get("voice") if isinstance(profile.get("voice"), Mapping) else {}
        )
        transcriber = (
            profile.get("transcriber")
            if isinstance(profile.get("transcriber"), Mapping)
            else {}
        )
        units = [
            {
                "id": MAIN_UNIT,
                "shape": "single_prompt",
                "instructions": prompt,
                "tools": [tool["name"] for tool in tools],
                "transitions": [],
            }
        ]
        model_document = (
            {
                "provider": _text(model.get("provider")),
                "name": _text(model.get("model")),
                "temperature": _number(model.get("temperature")),
                "max_tokens": _integer(model.get("maxTokens")),
            }
            if _text(model.get("model"))
            else None
        )
        voice_document = (
            {
                "provider": _text(voice.get("provider")),
                "voice_id": _text(voice.get("voiceId")),
                "language": _text(transcriber.get("language")),
                "speed": _number(voice.get("speed")),
            }
            if _text(voice.get("voiceId"))
            else None
        )
    else:
        engine = str(profile.get("response_engine_type") or "")
        tools = _retell_tools(profile.get("general_tools"))
        units = [
            {
                "id": MAIN_UNIT,
                "shape": "single_prompt" if engine == "retell-llm" else "external",
                "instructions": _text(profile.get("general_prompt")),
                "tools": [tool["name"] for tool in tools],
                "transitions": [],
            }
        ]
        for state in profile.get("states") or []:
            if not isinstance(state, Mapping) or not _text(state.get("name")):
                continue
            state_tools = _retell_tools(state.get("tools"))
            for tool in state_tools:
                if tool["name"] not in {t["name"] for t in tools}:
                    tools.append(tool)
            unit_id = _unit_id(str(state["name"]))
            units.append(
                {
                    "id": unit_id,
                    "shape": "state",
                    "instructions": _text(state.get("state_prompt")),
                    "tools": [tool["name"] for tool in state_tools],
                    "transitions": [
                        _unit_id(str(edge.get("destination_state_name")))
                        for edge in state.get("edges") or []
                        if isinstance(edge, Mapping)
                        and _text(edge.get("destination_state_name"))
                    ],
                }
            )
        if engine == "conversation-flow":
            gaps.append("flow_not_described")
            flow = (
                profile.get("conversation_flow")
                if isinstance(profile.get("conversation_flow"), Mapping)
                else {}
            )
            units[0]["instructions"] = _text(flow.get("global_prompt"))
            tools = _retell_tools(flow.get("tools"))
            units[0]["tools"] = [tool["name"] for tool in tools]
        model_document = (
            {
                "provider": None,
                "name": _text(profile.get("model")),
                "temperature": None,
                "max_tokens": None,
            }
            if _text(profile.get("model"))
            else None
        )
        voice_document = (
            {
                "provider": None,
                "voice_id": _text(profile.get("voice_id")),
                "language": _text(profile.get("language")),
                "speed": None,
            }
            if agent_type == "voice" and _text(profile.get("voice_id"))
            else None
        )
    seen: set[str] = set()
    units = [unit for unit in units if not (unit["id"] in seen or seen.add(unit["id"]))]
    agent = {
        "schema_version": "1.0",
        "type": agent_type,
        "kind": "hosted",
        "name": (_text(profile.get("name")) or assistant_id)[:_NAME_MAX],
        "provider_ref": {"provider": provider, "id": assistant_id, "version": None},
        "behaviour": units,
        "tools": tools,
        "model": model_document,
        "voice": voice_document,
        "telephony": [],
        "supports": [],
        "direction_answer": None,
        "gaps": gaps,
        "instrumentation": "none",
        "extension": {"profile": copy.deepcopy(dict(profile))},
    }
    agent["setup_hash"] = compute_setup_hash(agent)
    return agent


def _unit_id(name: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in "_.:-" else "_" for char in name.lower()
    ).strip("_")
    return (cleaned or "state")[:128]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
