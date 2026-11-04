"""The agent document of a target the platform can only call, a phone number or a
LiveKit worker: what its version's configuration says, since nothing can be read."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import structlog

from simulate.models import HarnessAgentVersionRuntime
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services.agent_setup import AgentStoreError, store_agent
from simulate.services.provider_profile import (
    MAIN_UNIT,
    PLATFORM_SDK_VERSION,
    compute_setup_hash,
)

logger = structlog.get_logger(__name__)

SourceKind = HarnessAgentVersionRuntime.SourceKind
CONFIGURED_KINDS = (SourceKind.PHONE, SourceKind.LIVEKIT)
_NAME_MAX = 255
_NUMBER_MAX = 32


def configured_document(
    source_kind: str, agent_payload: Mapping[str, Any], *, name: str, prompt: str
) -> dict[str, Any]:
    """The document for a phone or LiveKit version: one unit holding its prompt, and
    the number the platform dials for a phone agent."""
    config = agent_payload.get("config") or {}
    number = str(config.get("phone_number") or "").strip()
    speaks_first = config.get("target_speaks_first")
    agent: dict[str, Any] = {
        "schema_version": "1.0",
        "type": "voice",
        "kind": "endpoint",
        "name": (str(config.get("agent_name") or "").strip() or name)[:_NAME_MAX],
        "provider_ref": None,
        "behaviour": [
            {
                "id": MAIN_UNIT,
                "shape": "single_prompt",
                "instructions": prompt or None,
                "tools": [],
                "transitions": [],
            }
        ],
        "tools": [],
        "model": None,
        "voice": None,
        "telephony": (
            [
                {
                    "number": number[:_NUMBER_MAX],
                    "provider": None,
                    "direction_supported": ["inbound"],
                }
            ]
            if number
            else []
        ),
        "supports": ["inbound"],
        "direction_answer": None,
        "gaps": [],
        "instrumentation": "none",
        "extension": {
            "connector": str(source_kind),
            **(
                {"target_speaks_first": speaks_first}
                if isinstance(speaks_first, bool)
                else {}
            ),
        },
    }
    agent["setup_hash"] = compute_setup_hash(agent)
    return agent


def ensure_configured_document(agent_version_id: UUID | str) -> bool:
    """Store the configured document of a phone or LiveKit version that has none, so
    its contract has a place to be kept. Returns whether one was stored."""
    from simulate.services.harness_versioning.assembly import legacy_runtime

    if AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    ).exists():
        return False
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.select_related(
            "agent_version__agent_definition"
        )
        .defer("credential_blob", "diff")
        .filter(agent_version_id=agent_version_id)
        .first()
    )
    if (
        runtime is None
        or runtime.source_kind not in CONFIGURED_KINDS
        or legacy_runtime(runtime)
    ):
        return False
    version = runtime.agent_version
    payload = runtime.agent_payload or {}
    prompt = str(
        (payload.get("config") or {}).get("target_system_prompt")
        or version.description
        or ""
    ).strip()
    document = configured_document(
        runtime.source_kind,
        payload,
        name=version.agent_definition.agent_name or "",
        prompt=prompt,
    )
    try:
        store_agent(
            agent_version_id=version.id,
            agent=document,
            sdk_version=PLATFORM_SDK_VERSION,
            origin=AgentVersionSetup.Origin.READING,
        )
    except AgentStoreError as exc:
        logger.warning(
            "configured_agent_not_stored",
            agent_version_id=str(version.id),
            code=exc.code,
        )
        return False
    return True
