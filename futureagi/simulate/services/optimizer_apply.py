"""Apply an optimised prompt trial as a new PromptVersion (TH-5642).

Product decision (2026-06-05): "directly apply the fix" = create a NEW prompt
version the user confirms via the apply request — non-destructive, the baseline
row is never overwritten or deleted.

The new version clones the base PromptVersion with its system prompt replaced by
the trial's optimised prompt inside ``prompt_config_snapshot`` (what the runtime
adapter reads), takes the next ``template_version`` ("vN"), and — since the POST is
the user's confirmation — becomes the active default for the template.
"""

from __future__ import annotations

import copy
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


def _replace_system_message(messages: list, optimized_prompt: str) -> list:
    """Deep-copy ``messages`` with the first system message's content set to the
    optimised prompt (prepending one if none exists)."""
    messages = copy.deepcopy(messages) if messages else []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            msg["content"] = optimized_prompt
            return messages
    messages.insert(0, {"role": "system", "content": optimized_prompt})
    return messages


def snapshot_with_prompt(snapshot, optimized_prompt):
    """Return a deep-copied prompt_config_snapshot with its system prompt replaced.

    The snapshot is a list ``[{"messages": [...], "configuration": {...}}]`` (the
    shape PromptBasedAgentAdapter reads); we only rewrite the system message.
    """
    snapshot = copy.deepcopy(snapshot)
    cfg = snapshot[0] if isinstance(snapshot, list) and snapshot else snapshot
    if isinstance(cfg, dict):
        cfg["messages"] = _replace_system_message(cfg.get("messages", []), optimized_prompt)
    return snapshot


def apply_optimized_prompt_as_new_version(
    base_version, optimized_prompt: str, *, make_default: bool = True
):
    """Clone ``base_version`` into a new PromptVersion carrying ``optimized_prompt``.

    Args:
        base_version: the PromptVersion the optimiser ran against (the baseline).
        optimized_prompt: the winning trial's prompt text.
        make_default: make the new version the template's active default (the apply
            request is the user's confirmation that it should go live).

    Returns:
        The newly-created PromptVersion. The base row is untouched.
    """
    from model_hub.models.run_prompt import PromptVersion
    from model_hub.services.prompt_service import get_next_version_number

    # Fresh load so we never mutate the caller's instance / the base row in place.
    base = PromptVersion.objects.get(pk=base_version.pk)

    new_snapshot = snapshot_with_prompt(base.prompt_config_snapshot, optimized_prompt)
    org_id = base.original_template.organization_id if base.original_template_id else None
    next_number = get_next_version_number(base.original_template_id, org_id)

    # Clone: detach the PK so save() inserts a new row, copying every scalar field;
    # reset run-specific outputs so the new version starts clean.
    base.pk = None
    base.id = uuid4()
    base._state.adding = True
    base.prompt_config_snapshot = new_snapshot
    base.template_version = f"v{next_number}"
    base.is_default = make_default
    base.is_draft = False
    base.output = []
    base.evaluation_results = {}
    base.commit_message = "Applied optimised prompt (TH-5642)"
    base.save()

    logger.info(
        "optimizer_prompt_applied_as_new_version",
        new_version_id=str(base.id),
        template_version=base.template_version,
        original_template_id=str(base.original_template_id),
        is_default=make_default,
    )
    return base
