from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from django.db import IntegrityError, transaction

from simulate.models import AgentDefinition, AgentVersion, ProviderCredentials
from simulate.models.agent_setup import (
    AgentBehaviourUnit,
    AgentTelephony,
    AgentTool,
    AgentVersionSetup,
)

logger = structlog.get_logger(__name__)

AGENT_MAX_BYTES = 1_048_576
LEGACY_TYPE = {"livekit": "livekit", "livekit_bridge": "livekit", "retell": "retell"}

_FAILURE_MESSAGE_MAX = 2000
_SETUP_HASH = re.compile(r"sh1:[0-9a-f]{64}")
_ESCAPED_NUL = re.compile(r"(?<!\\)(?:\\\\)*\\u0000")
_INT_MAX = 2**31 - 1
_UNIT_ID = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")
_TYPES = ("voice", "chat", "coding", "cua", "browser", "crawler")
_KINDS = ("hosted", "source", "endpoint")
_SHAPES = ("single_prompt", "state", "flow_node", "external")
_TOOL_KINDS = ("builtin", "http", "mcp", "unknown")
_GAPS = ("states_not_described", "flow_not_described", "engine_not_readable")
_DIRECTIONS = ("inbound", "outbound")
_INSTRUMENTATION = ("traceai", "opentelemetry", "none")
_AGENT_FIELDS = (
    "schema_version",
    "type",
    "kind",
    "name",
    "provider_ref",
    "behaviour",
    "tools",
    "model",
    "voice",
    "telephony",
    "supports",
    "direction_answer",
    "gaps",
    "extension",
    "setup_hash",
)


@dataclass(frozen=True)
class AgentSummary:
    agent_definition_id: UUID
    agent_version_id: UUID | None
    agent_stored: bool
    setup_stale: bool
    provider: str
    provider_agent_id: str
    credential_id: UUID | None
    key_present: bool
    type: str
    kind: str | None
    unit_ids: tuple[str, ...]
    instrumentation: str
    setup_hash: str | None
    provider_info_complete: bool
    missing: tuple[str, ...]


@dataclass(frozen=True)
class StoredAgentAnswer:
    agent: dict[str, Any]
    sdk_version: str
    stored_at: datetime
    setup_stale: bool


@dataclass(frozen=True)
class SetupOutcome:
    status: str
    failure_code: str
    failure_message: str


class AgentNotFound(Exception):
    pass


class AgentVersionNotFound(Exception):
    pass


class AgentStoreError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Invalid(Exception):
    pass


def agent_summary(
    agent_definition_id: UUID, *, agent_version_id: UUID | None = None
) -> AgentSummary:
    agent = AgentDefinition.no_workspace_objects.filter(id=agent_definition_id).first()
    if agent is None:
        raise AgentNotFound("agent definition not found")
    versions = AgentVersion.no_workspace_objects.filter(agent_definition=agent)
    active = versions.filter(status="active").order_by("-version_number").first()
    version = (
        versions.filter(id=agent_version_id).first()
        if agent_version_id is not None
        else None
    )
    if version is None:
        version = active or versions.order_by("-version_number").first()
    rows = AgentVersionSetup.no_workspace_objects.filter(
        agent_version__agent_definition=agent,
        status=AgentVersionSetup.Status.STORED,
        agent_version__deleted=False,
    )
    setup = (
        rows.filter(agent_version=version).first()
        if agent_version_id is not None and version
        else None
    ) or rows.order_by("-agent_version__version_number").first()
    column_provider = (agent.provider or "").strip()
    column_agent_id = (agent.assistant_id or "").strip()
    stale = setup is not None and _setup_is_stale(setup, agent)
    if setup is not None and stale:
        logger.warning(
            "agent_summary_setup_stale",
            agent_definition_id=str(agent.id),
            agent_version_id=str(setup.agent_version_id),
        )
    if setup and not stale:
        if setup.agent_kind == "hosted":
            provider, provider_agent_id = setup.provider, setup.provider_agent_id
        else:
            provider, provider_agent_id = column_provider, column_agent_id
        type_, kind = setup.agent_type, setup.agent_kind
        instrumentation = setup.instrumentation
        unit_ids = tuple(
            setup.behaviour_units.order_by("position").values_list("unit_id", flat=True)
        )
        setup_hash = setup.setup_hash
    else:
        provider, provider_agent_id = column_provider, column_agent_id
        type_ = "chat" if agent.agent_type == "text" else "voice"
        kind, unit_ids, setup_hash = None, ("main",), None
        instrumentation = "none"
    keyed = ProviderCredentials.no_workspace_objects.exclude(api_key="")
    if provider:
        keyed = keyed.filter(provider_type=LEGACY_TYPE.get(provider, "vapi"))
    candidates = (
        keyed.filter(agent_version=active).first() if active else None,
        keyed.filter(agent_version__agent_definition=agent)
        .order_by("-agent_version__version_number")
        .first(),
        keyed.filter(agent_definition=agent).first(),
    )
    credential = next((row for row in candidates if row is not None), None)
    key_present = credential is not None or bool((agent.api_key or "").strip())
    missing = tuple(
        word
        for word, value in (
            ("provider", provider),
            ("provider_agent_id", provider_agent_id),
            ("credential", key_present),
        )
        if not value
    )
    return AgentSummary(
        agent_definition_id=agent.id,
        agent_version_id=version.id if version else None,
        agent_stored=bool(setup and not stale),
        setup_stale=stale,
        provider=provider,
        provider_agent_id=provider_agent_id,
        credential_id=credential.id if credential else None,
        key_present=key_present,
        type=type_,
        kind=kind,
        unit_ids=unit_ids,
        instrumentation=instrumentation,
        setup_hash=setup_hash,
        provider_info_complete=not missing,
        missing=missing,
    )


def _setup_is_stale(setup: AgentVersionSetup, agent: AgentDefinition) -> bool:
    return setup.agent_kind == "hosted" and (
        (agent.provider or "").strip() != setup.provider
        or (agent.assistant_id or "").strip() != setup.provider_agent_id
    )


def stored_agent(
    agent_version_id: UUID,
    *,
    with_raw_setup: bool = False,
    include_stale: bool = False,
) -> dict | None:
    version = (
        AgentVersion.no_workspace_objects.select_related("agent_definition")
        .filter(id=agent_version_id)
        .first()
    )
    if version is None:
        return None
    setup = AgentVersionSetup.no_workspace_objects.filter(
        agent_version=version, status=AgentVersionSetup.Status.STORED
    ).first()
    if setup is None:
        return None
    if not include_stale and _setup_is_stale(setup, version.agent_definition):
        return None
    return _rebuilt_agent(setup, with_raw_setup=with_raw_setup)


def stored_agent_answer(agent_version: AgentVersion) -> StoredAgentAnswer | None:
    setup = AgentVersionSetup.no_workspace_objects.filter(
        agent_version=agent_version, status=AgentVersionSetup.Status.STORED
    ).first()
    if setup is None:
        return None
    return StoredAgentAnswer(
        agent=_rebuilt_agent(setup, with_raw_setup=False),
        sdk_version=setup.sdk_version,
        stored_at=setup.created_at,
        setup_stale=_setup_is_stale(setup, agent_version.agent_definition),
    )


def _rebuilt_agent(setup: AgentVersionSetup, *, with_raw_setup: bool) -> dict[str, Any]:
    agent: dict[str, Any] = {
        "schema_version": setup.schema_version,
        "type": setup.agent_type,
        "kind": setup.agent_kind,
        "name": setup.name,
        "provider_ref": (
            {
                "provider": setup.provider,
                "id": setup.provider_agent_id,
                "version": setup.provider_version,
            }
            if setup.provider
            else None
        ),
        "behaviour": [
            {
                "id": unit.unit_id,
                "shape": unit.shape,
                "instructions": unit.instructions,
                "tools": list(unit.tool_names),
                "transitions": list(unit.transitions),
            }
            for unit in setup.behaviour_units.order_by("position")
        ],
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "kind": tool.kind,
                "parameters": tool.parameters,
                "endpoint": (
                    {"url": tool.endpoint_url}
                    if tool.endpoint_url is not None
                    else None
                ),
            }
            for tool in setup.tools.order_by("position")
        ],
        "model": (
            {
                "provider": setup.model_provider,
                "name": setup.model_name,
                "temperature": setup.model_temperature,
                "max_tokens": setup.model_max_tokens,
            }
            if setup.model_name is not None
            else None
        ),
        "voice": (
            {
                "provider": setup.voice_provider,
                "voice_id": setup.voice_id,
                "language": setup.voice_language,
                "speed": setup.voice_speed,
            }
            if setup.voice_id is not None
            else None
        ),
        "telephony": [
            {
                "number": entry.number,
                "provider": entry.provider,
                "direction_supported": _directions(entry.inbound, entry.outbound),
            }
            for entry in setup.telephony.order_by("position")
        ],
        "supports": _directions(setup.supports_inbound, setup.supports_outbound),
        "direction_answer": setup.direction_answer,
        "gaps": list(setup.gaps),
        "instrumentation": setup.instrumentation,
        "setup_hash": setup.setup_hash,
    }
    if with_raw_setup:
        agent["extension"] = setup.raw_setup
    return agent


def store_agent(
    *, agent_version_id: UUID, agent: dict, sdk_version: str
) -> Literal["stored", "unchanged"]:
    try:
        version = _checked_version(agent_version_id, agent, sdk_version)
        existing = _first_write(
            agent_version_id, lambda: _insert_agent(version, agent, sdk_version)
        )
        if existing is None:
            outcome: Literal["stored", "unchanged"] = "stored"
        elif (
            existing.status == AgentVersionSetup.Status.STORED
            and existing.setup_hash == agent["setup_hash"]
        ):
            outcome = "unchanged"
        else:
            raise AgentStoreError("setup_outcome_already_stored")
    except AgentStoreError as error:
        logger.warning(
            "agent_setup_rejected",
            agent_version_id=str(agent_version_id),
            code=error.code,
        )
        raise
    logger.info(
        "agent_setup_stored",
        agent_version_id=str(agent_version_id),
        provider=(agent["provider_ref"] or {}).get("provider", ""),
        schema_version=agent["schema_version"],
        sdk_version=sdk_version,
        setup_hash=agent["setup_hash"],
        outcome=outcome,
    )
    return outcome


def store_setup_failure(
    *, agent_version_id: UUID, code: str, message: str
) -> Literal["failed", "ignored"]:
    existing = _first_write(
        agent_version_id,
        lambda: AgentVersionSetup.objects.create(
            agent_version_id=agent_version_id,
            status=AgentVersionSetup.Status.FAILED,
            failure_code=code[:64],
            failure_message=message[:_FAILURE_MESSAGE_MAX],
        ),
    )
    outcome: Literal["failed", "ignored"] = "failed" if existing is None else "ignored"
    logger.warning(
        "agent_setup_failed",
        agent_version_id=str(agent_version_id),
        failure_code=code,
        outcome=outcome,
    )
    return outcome


def setup_outcome(agent_version_id: UUID) -> SetupOutcome | None:
    setup = AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    ).first()
    if setup is None:
        return None
    return SetupOutcome(
        status=setup.status,
        failure_code=setup.failure_code,
        failure_message=setup.failure_message,
    )


def _checked_version(
    agent_version_id: UUID, agent: dict, sdk_version: str
) -> AgentVersion:
    try:
        text = json.dumps(agent, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise AgentStoreError("agent_invalid") from None
    if len(text.encode()) > AGENT_MAX_BYTES:
        raise AgentStoreError("agent_too_large")
    if _ESCAPED_NUL.search(text):
        raise AgentStoreError("agent_invalid")
    version = AgentVersion.no_workspace_objects.filter(id=agent_version_id).first()
    if version is None:
        raise AgentStoreError("agent_version_not_found")
    try:
        _check_agent(agent, sdk_version)
    except _Invalid:
        raise AgentStoreError("agent_invalid") from None
    if agent["kind"] == "hosted":
        _check_hosted(version, agent["provider_ref"], sdk_version)
    return version


def _check_hosted(
    version: AgentVersion, provider_ref: dict[str, Any], sdk_version: str
) -> None:
    from simulate.services.provider_list import (
        ProviderListNotPublished,
        ProviderListVersionNotFound,
        read_provider_list,
    )

    try:
        snapshot = read_provider_list(sdk_version=sdk_version)
    except (ProviderListNotPublished, ProviderListVersionNotFound):
        raise AgentStoreError("agent_schema_unavailable") from None
    if provider_ref["provider"] not in snapshot.providers:
        raise AgentStoreError("provider_not_listed")
    credentials = ProviderCredentials.no_workspace_objects.filter(
        agent_version=version
    ).first()
    if credentials is not None and credentials.assistant_id != provider_ref["id"]:
        raise AgentStoreError("provider_ref_mismatch")


def _check_agent(agent: Any, sdk_version: Any) -> None:
    _object(agent, _AGENT_FIELDS, ("instrumentation",))
    _text(sdk_version, 32)
    if not sdk_version or agent["schema_version"] != "1.0":
        raise _Invalid
    _word(agent["type"], _TYPES)
    _word(agent["kind"], _KINDS)
    _text(agent["name"], 255)
    _check_provider_ref(agent["kind"], agent["provider_ref"])
    tool_names = _check_tools(agent["tools"])
    _check_behaviour(agent["behaviour"], tool_names)
    if agent["model"] is not None:
        _object(agent["model"], ("name",), ("provider", "temperature", "max_tokens"))
        _text(agent["model"]["name"], 255)
        _text(agent["model"].get("provider"), 255, nullable=True)
        _number(agent["model"].get("temperature"))
        _number(agent["model"].get("max_tokens"), integer=True)
    if agent["voice"] is not None:
        _object(agent["voice"], ("voice_id",), ("provider", "language", "speed"))
        _text(agent["voice"]["voice_id"], 255)
        _text(agent["voice"].get("provider"), 255, nullable=True)
        _text(agent["voice"].get("language"), 255, nullable=True)
        _number(agent["voice"].get("speed"))
    _list(agent["telephony"])
    for entry in agent["telephony"]:
        _object(entry, ("number",), ("provider", "direction_supported"))
        _text(entry["number"], 32)
        _text(entry.get("provider"), 64, nullable=True)
        _check_directions(entry.get("direction_supported", []))
    _check_directions(agent["supports"])
    if agent["direction_answer"] is not None:
        _word(agent["direction_answer"], _DIRECTIONS)
    _list(agent["gaps"])
    for gap in agent["gaps"]:
        _word(gap, _GAPS)
    _word(agent.get("instrumentation", "none"), _INSTRUMENTATION)
    if not isinstance(agent["extension"], dict):
        raise _Invalid
    if not isinstance(agent["setup_hash"], str) or not _SETUP_HASH.fullmatch(
        agent["setup_hash"]
    ):
        raise _Invalid


def _check_provider_ref(kind: str, provider_ref: Any) -> None:
    if kind != "hosted":
        if provider_ref is not None:
            raise _Invalid
        return
    _object(provider_ref, ("provider", "id"), ("version",))
    _text(provider_ref["provider"], 64)
    _text(provider_ref["id"], 255)
    _text(provider_ref.get("version"), 64, nullable=True)
    if not provider_ref["provider"] or not provider_ref["id"]:
        raise _Invalid


def _check_tools(tools: Any) -> set[str]:
    _list(tools)
    for tool in tools:
        _object(tool, ("name", "kind"), ("description", "parameters", "endpoint"))
        _text(tool["name"], 255)
        _word(tool["kind"], _TOOL_KINDS)
        _text(tool.get("description"), None, nullable=True)
        parameters = tool.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise _Invalid
        endpoint = tool.get("endpoint")
        if endpoint is not None:
            _object(endpoint, ("url",))
            _text(endpoint["url"], None)
    return {tool["name"] for tool in tools}


def _check_behaviour(units: Any, tool_names: set[str]) -> None:
    _list(units)
    if not units:
        raise _Invalid
    for unit in units:
        _object(unit, ("id", "shape"), ("instructions", "tools", "transitions"))
        if not isinstance(unit["id"], str) or not _UNIT_ID.fullmatch(unit["id"]):
            raise _Invalid
        _word(unit["shape"], _SHAPES)
        _text(unit.get("instructions"), None, nullable=True)
        names = unit.get("tools", [])
        _list(names)
        for name in names:
            _text(name, 255)
            if name not in tool_names:
                raise _Invalid
        transitions = unit.get("transitions", [])
        _list(transitions)
        for transition in transitions:
            _text(transition, 128)
    unit_ids = [unit["id"] for unit in units]
    if len(unit_ids) != len(set(unit_ids)):
        raise _Invalid


def _check_directions(listed: Any) -> None:
    _list(listed)
    if listed != [direction for direction in _DIRECTIONS if direction in listed]:
        raise _Invalid


def _object(
    value: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> None:
    if not isinstance(value, dict):
        raise _Invalid
    keys = set(value)
    if not keys.issuperset(required) or not keys.issubset({*required, *optional}):
        raise _Invalid


def _list(value: Any) -> None:
    if not isinstance(value, list):
        raise _Invalid


def _text(value: Any, max_length: int | None, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or (
        max_length is not None and len(value) > max_length
    ):
        raise _Invalid


def _number(value: Any, *, integer: bool = False) -> None:
    if value is None:
        return
    accepted = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, accepted):
        raise _Invalid
    if integer and abs(value) > _INT_MAX:
        raise _Invalid


def _word(value: Any, words: tuple[str, ...]) -> None:
    if not isinstance(value, str) or value not in words:
        raise _Invalid


def _directions(inbound: bool, outbound: bool) -> list[str]:
    return [
        direction
        for direction, present in zip(_DIRECTIONS, (inbound, outbound))
        if present
    ]


def _first_write(
    agent_version_id: UUID, write: Callable[[], object]
) -> AgentVersionSetup | None:
    existing = AgentVersionSetup.all_objects.filter(
        agent_version_id=agent_version_id
    ).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            write()
    except IntegrityError:
        existing = AgentVersionSetup.all_objects.filter(
            agent_version_id=agent_version_id
        ).first()
        if existing is None:
            raise
        return existing
    return None


def _insert_agent(version: AgentVersion, agent: dict, sdk_version: str) -> None:
    provider_ref = agent["provider_ref"] or {}
    model = agent["model"] or {}
    voice = agent["voice"] or {}
    setup = AgentVersionSetup.objects.create(
        agent_version=version,
        status=AgentVersionSetup.Status.STORED,
        setup_hash=agent["setup_hash"],
        schema_version=agent["schema_version"],
        sdk_version=sdk_version,
        agent_type=agent["type"],
        agent_kind=agent["kind"],
        name=agent["name"],
        provider=provider_ref.get("provider", ""),
        provider_agent_id=provider_ref.get("id", ""),
        provider_version=provider_ref.get("version"),
        model_provider=model.get("provider"),
        model_name=model.get("name"),
        model_temperature=model.get("temperature"),
        model_max_tokens=model.get("max_tokens"),
        voice_provider=voice.get("provider"),
        voice_id=voice.get("voice_id"),
        voice_language=voice.get("language"),
        voice_speed=voice.get("speed"),
        supports_inbound="inbound" in agent["supports"],
        supports_outbound="outbound" in agent["supports"],
        direction_answer=agent["direction_answer"],
        gaps=list(agent["gaps"]),
        instrumentation=agent.get("instrumentation", "none"),
        raw_setup=agent["extension"],
    )
    AgentBehaviourUnit.objects.bulk_create(
        AgentBehaviourUnit(
            setup=setup,
            position=position,
            unit_id=unit["id"],
            shape=unit["shape"],
            instructions=unit.get("instructions"),
            tool_names=list(unit.get("tools", [])),
            transitions=list(unit.get("transitions", [])),
        )
        for position, unit in enumerate(agent["behaviour"])
    )
    AgentTool.objects.bulk_create(
        AgentTool(
            setup=setup,
            position=position,
            name=tool["name"],
            description=tool.get("description"),
            kind=tool["kind"],
            parameters=tool.get("parameters"),
            endpoint_url=(tool.get("endpoint") or {}).get("url"),
        )
        for position, tool in enumerate(agent["tools"])
    )
    AgentTelephony.objects.bulk_create(
        AgentTelephony(
            setup=setup,
            position=position,
            number=entry["number"],
            provider=entry.get("provider"),
            inbound="inbound" in entry.get("direction_supported", []),
            outbound="outbound" in entry.get("direction_supported", []),
        )
        for position, entry in enumerate(agent["telephony"])
    )
