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
from django.db import transaction
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


class EvalSelectionRefused(Exception):
    """One name this run cannot bind, carrying the reason to report.

    Separate from :class:`UnknownEvalSelection`, which the guest raises over a
    whole batch it authored. A person adding a single eval needs to know which
    of the ways it was refused, since two of them are fixable by choosing a
    different eval and one is not.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"{name}: {reason}")


class EvalSelectionFull(EvalSelectionRefused):
    """The run already binds as many evals as it is allowed to."""


# Agent templates name no model and the default cannot read audio. Overridable per deployment.
FALLBACK_EVAL_MODEL = "turing_large"


def bind_eval_config(
    run_test: RunTest, template: EvalTemplate, mapping: dict[str, str]
) -> SimulateEvalConfig:
    """Bind one template to one run, reviving a binding that was removed before.

    The id is derived from the run and the template, so a name chosen again
    after being removed lands on the row that already exists. That row is soft
    deleted and therefore invisible to the default manager, which would create
    rather than find it and collide on the primary key.
    """
    config_id = uuid.uuid5(_SELECTED_EVAL_NAMESPACE, f"{run_test.id}:{template.id}")
    existing = SimulateEvalConfig.all_objects.filter(id=config_id).first()
    if existing is None:
        return SimulateEvalConfig.objects.create(
            id=config_id,
            eval_template=template,
            name=str(template.name),
            config=template.config or {},
            mapping=mapping,
            run_test=run_test,
            filters={},
            error_localizer=True,
            model=template.model
            or getattr(settings, "HARNESS_EVAL_MODEL", FALLBACK_EVAL_MODEL),
        )
    if existing.deleted:
        existing.deleted = False
        existing.deleted_at = None
        # A revived binding restates its mapping rather than trusting the one
        # the first binding stored: the run's modality decides the sources, and
        # a contract authored since then can have changed it.
        existing.mapping = mapping
        existing.error_localizer = True
        existing.save(update_fields=["deleted", "deleted_at", "mapping", "updated_at"])
    return existing


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
        configs.append(bind_eval_config(run_test, template, mapping))
    dropped = ordered[MOST_SELECTED_EVALS:]
    if dropped:
        logger.warning(
            "harness_eval_selection_capped",
            run_test_id=str(run_test.id),
            kept=MOST_SELECTED_EVALS,
            dropped=dropped,
        )
    return configs


def selected_eval_configs(run_test: RunTest) -> list[SimulateEvalConfig]:
    """The evals bound to this run right now, oldest binding first."""
    return list(
        SimulateEvalConfig.objects.filter(run_test=run_test, deleted=False).order_by(
            "created_at"
        )
    )


def addable_evals(run_test: RunTest, modality: str) -> list[dict[str, Any]]:
    """The catalogue a person may still add to this run.

    The same offer the guest was given, minus what is already bound, so the
    picker cannot present a row that would be refused as a duplicate.
    """
    chosen = {str(config.name or "") for config in selected_eval_configs(run_test)}
    return [
        entry
        for entry in offered_evals(run_test.organization, run_test.workspace, modality)
        if str(entry["name"]) not in chosen
    ]


def add_selected_eval(
    run_test: RunTest, name: str, modality: str
) -> SimulateEvalConfig:
    """Bind one more eval to a run, refusing with the reason it cannot be bound.

    The gates are the guest's own: the manifest, the organization's templates,
    and a mapping for every key the template requires. Adding by hand may not
    reach a source the authored selection could not.
    """
    wanted = str(name or "").strip()
    template = (
        _visible_templates(run_test.organization, run_test.workspace)
        .filter(name=wanted)
        .first()
        if wanted
        else None
    )
    if template is None or wanted not in offerable_eval_names():
        raise EvalSelectionRefused(
            wanted, "not an eval this environment can be graded by"
        )
    mapping = resolve_eval_mapping(template, modality)
    if mapping is None:
        needed = ", ".join(_required_keys(template)) or "inputs"
        raise EvalSelectionRefused(
            wanted, f"needs {needed}, which a {modality} run does not produce"
        )
    # The cap is read and then written, so two clicks arriving together would
    # both see room and both bind. The run row is the thing they contend for,
    # so it is the thing to hold.
    with transaction.atomic():
        type(run_test).objects.select_for_update(of=("self",)).filter(
            pk=run_test.pk
        ).first()
        current = selected_eval_configs(run_test)
        for config in current:
            if str(config.name or "") == wanted:
                # Idempotent rather than an error: a second click on one row
                # should leave the environment listing the eval once, not
                # report a failure.
                return config
        if len(current) >= MOST_SELECTED_EVALS:
            raise EvalSelectionFull(
                wanted,
                f"an environment runs at most {MOST_SELECTED_EVALS} evals; "
                "remove one before adding another",
            )
        return bind_eval_config(run_test, template, mapping)


def runnable_eval_config_ids(run_test_id) -> list[str]:
    """Config ids the evaluator can run: a harness result column carries an empty mapping."""
    return [
        str(config_id)
        for config_id, mapping in SimulateEvalConfig.objects.filter(
            run_test_id=run_test_id, deleted=False
        ).values_list("id", "mapping")
        if mapping
    ]
