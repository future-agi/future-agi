"""Platform evals a hosted harness run selects for itself.

The guest returns eval names only; the mapping tables below are the sole mappings this path can
produce, so a model cannot bind a variable to a source that resolves empty. The harness's own
deterministic checkpoints do not come through here.
"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from django.apps import apps
from django.conf import settings
from django.db.models import Q

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import RunTest, SimulateEvalConfig

logger = structlog.get_logger(__name__)

# Stable id so a retried provision cannot double the configs.
_SELECTED_EVAL_NAMESPACE = uuid.UUID("2b0f2f19-2c65-4b1e-9c9a-2f1a3b4c5d6e")

# Each selected eval is one judge call per call in the suite.
MOST_SELECTED_EVALS = 8

# The evals the harness may offer, defined in one file a human owns. Absent means never offered,
# whatever exists in the database; an entry with "visible": false is withheld on purpose and stays
# in the file so the decision is legible. This is not EvalTemplate.visible_ui.
MANIFEST_FILENAME = "harness_evals.json"


def harness_evals_manifest() -> Path:
    """The manifest inside this app, located the way Django locates anything in an app."""
    return Path(apps.get_app_config("simulate").path) / "data" / MANIFEST_FILENAME


@lru_cache(maxsize=1)
def offerable_eval_names() -> frozenset[str]:
    """Manifest names marked offerable. Read once per process; the file ships with the code."""
    body = json.loads(harness_evals_manifest().read_text(encoding="utf-8"))
    return frozenset(
        str(entry["name"]) for entry in body["evals"] if entry.get("visible") is True
    )


# Required key to the source the eval runner resolves.
_SOURCE_BY_KEY_VOICE = {
    # The combined recording; a per-channel mapping resolves empty on combined-only providers.
    "conversation": "voice_recording",
    # Audio-analysing evals ask for the recording itself rather than a transcript of it.
    "input_audio": "voice_recording",
    # A single-output eval on a call is judging the same conversation.
    "output": "voice_recording",
    "agent_prompt": "agent_prompt",
    "system_prompt": "agent_prompt",
}
_SOURCE_BY_KEY_TEXT = {
    # A chat run has no recording, so the conversation is its transcript text.
    "conversation": "transcript",
    "output": "transcript",
    "agent_prompt": "agent_prompt",
    "system_prompt": "agent_prompt",
}


def _sources_for(modality: str) -> dict[str, str]:
    return _SOURCE_BY_KEY_VOICE if modality == "voice" else _SOURCE_BY_KEY_TEXT


def _required_keys(template: EvalTemplate) -> list[str]:
    config = template.config or {}
    keys = config.get("required_keys")
    return [str(key) for key in keys] if isinstance(keys, list) else []


def _visible_templates(organization, workspace):
    """Templates this organization may select: its own plus system-owned, never another tenant's."""
    visible_scope = Q(organization=organization) | Q(organization__isnull=True)
    workspace_scope = Q(workspace=workspace) | Q(workspace__isnull=True)
    return EvalTemplate.no_workspace_objects.filter(
        visible_scope, workspace_scope, deleted=False
    )


def resolve_eval_mapping(
    template: EvalTemplate, modality: str
) -> dict[str, str] | None:
    """The mapping for one template, or None to refuse it when a required key has no source."""
    sources = _sources_for(modality)
    keys = _required_keys(template)
    if not keys:
        return None
    mapping: dict[str, str] = {}
    for key in keys:
        source = sources.get(key)
        if source is None:
            return None
        mapping[key] = source
    return mapping


def offered_evals(organization, workspace, modality: str) -> list[dict[str, Any]]:
    """The catalogue put in front of the guest, already filtered to what this run can run."""
    offered: list[dict[str, Any]] = []
    offerable = offerable_eval_names()
    for template in _visible_templates(organization, workspace).order_by("name"):
        name = str(template.name or "")
        if name not in offerable:
            continue
        mapping = resolve_eval_mapping(template, modality)
        if mapping is None:
            logger.warning(
                "harness_eval_not_offerable",
                template=name,
                required_keys=_required_keys(template),
                modality=modality,
            )
            continue
        offered.append(
            {
                "name": name,
                "description": str(template.description or "")[:500],
                "required_keys": _required_keys(template),
                "modality": modality,
            }
        )
    return offered


class UnknownEvalSelection(Exception):
    """A chosen name is not a template this organization can see."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(", ".join(names))


# Agent templates name no model and the default cannot read audio. Overridable per deployment.
FALLBACK_EVAL_MODEL = "turing_large"


def create_selected_eval_configs(
    run_test: RunTest, chosen: list[str], modality: str
) -> list[SimulateEvalConfig]:
    """Bind the guest's chosen eval names to this run, raising on a name it could not have been offered."""
    wanted = [str(name).strip() for name in (chosen or []) if str(name).strip()]
    if not wanted:
        return []
    seen: set[str] = set()
    ordered = [name for name in wanted if not (name in seen or seen.add(name))]

    # The manifest gates this path too, so the docstring above holds.
    offerable = offerable_eval_names()
    found = {
        str(template.name): template
        for template in _visible_templates(
            run_test.organization, run_test.workspace
        ).filter(name__in=ordered)
        if str(template.name) in offerable
    }
    missing = [name for name in ordered if name not in found]
    if missing:
        raise UnknownEvalSelection(missing)

    configs: list[SimulateEvalConfig] = []
    for name in ordered[:MOST_SELECTED_EVALS]:
        template = found[name]
        mapping = resolve_eval_mapping(template, modality)
        if mapping is None:
            logger.warning(
                "harness_eval_selection_unmappable",
                run_test_id=str(run_test.id),
                template=name,
                required_keys=_required_keys(template),
                modality=modality,
            )
            continue
        config_id = uuid.uuid5(
            _SELECTED_EVAL_NAMESPACE, f"{run_test.id}:{template.id}"
        )
        config, _ = SimulateEvalConfig.objects.get_or_create(
            id=config_id,
            defaults={
                "eval_template": template,
                "name": name,
                "config": template.config or {},
                "mapping": mapping,
                "run_test": run_test,
                "filters": {},
                "model": template.model or getattr(settings, "HARNESS_EVAL_MODEL", FALLBACK_EVAL_MODEL),
            },
        )
        configs.append(config)
    dropped = ordered[MOST_SELECTED_EVALS:]
    if dropped:
        logger.warning(
            "harness_eval_selection_capped",
            run_test_id=str(run_test.id),
            kept=MOST_SELECTED_EVALS,
            dropped=dropped,
        )
    return configs


def runnable_eval_config_ids(run_test_id) -> list[str]:
    """Config ids the evaluator can run: a harness result column carries an empty mapping."""
    return [
        str(config_id)
        for config_id, mapping in SimulateEvalConfig.objects.filter(
            run_test_id=run_test_id, deleted=False
        ).values_list("id", "mapping")
        if mapping
    ]
