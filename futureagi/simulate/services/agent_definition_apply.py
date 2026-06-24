"""Apply an optimised config to a platform AgentDefinition (TH-5642).

The platform-side apply edge. For agent-definition runs whose provider has NO
writable hosted prompt — self-hosted code agents (LiveKit, Pipecat, Deepgram) and
non-prompt provider configs (Twilio TwiML, Bland pathway) — there is no provider
API to write to, but the platform still owns the AgentDefinition + its versioned
config. "Directly apply the fix" there means persisting the winning config as a
NEW, non-destructive AgentVersion (mirrors optimizer_apply's new-PromptVersion
pattern): the optimisation is recorded and becomes the agent's active version,
which the customer's own deployment / the next simulation adopts.

This makes apply work for ALL providers: provider-hosted agents additionally get
a live provider-API write (see provider_prompt_apply), everything else gets the
platform-version write here. Neither path silently drops the fix.
"""

from __future__ import annotations

import copy
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Keys of the kit's whole-agent candidate config that map onto an AgentDefinition
# version snapshot. (provider/assistant_id identity keys are deliberately ignored.)
_CONFIG_TO_SNAPSHOT = {
    "instructions": "system_prompt",
    "model": "model",
    "temperature": "temperature",
    "first_message": "first_message",
    "voice": "voice",
}


def apply_config_as_new_agent_version(agent_definition, config: dict[str, Any]):
    """Persist ``config`` as a new active AgentVersion of ``agent_definition``.

    Returns the created AgentVersion. The previous version is untouched; the new
    one becomes the latest/active version carrying the optimised config in its
    ``configuration_snapshot`` (plus the raw config under ``optimised_config``).
    """
    from simulate.models import AgentVersion

    latest = (
        AgentVersion.objects.filter(agent_definition=agent_definition)
        .order_by("-version_number")
        .first()
    )
    snapshot = copy.deepcopy(getattr(latest, "configuration_snapshot", None) or {})

    applied_fields: list[str] = []
    for cfg_key, snap_key in _CONFIG_TO_SNAPSHOT.items():
        if config.get(cfg_key) not in (None, ""):
            snapshot[snap_key] = config[cfg_key]
            applied_fields.append(cfg_key)
    # Keep the full winning config addressable for downstream adopt/inspect.
    snapshot["optimised_config"] = {
        k: v for k, v in config.items() if k not in ("type", "name")
    }

    new_version = AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization_id=agent_definition.organization_id,
        workspace_id=agent_definition.workspace_id,
        status=AgentVersion.StatusChoices.ACTIVE
        if hasattr(AgentVersion, "StatusChoices")
        else "active",
        configuration_snapshot=snapshot,
        commit_message="Applied optimised config (TH-5642 agent-learning-kit)",
    )
    logger.info(
        "agent_definition_config_applied",
        agent_definition_id=str(agent_definition.id),
        new_version_id=str(new_version.id),
        version_number=new_version.version_number,
        applied_fields=applied_fields,
    )
    return new_version, applied_fields
