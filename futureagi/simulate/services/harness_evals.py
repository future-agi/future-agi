"""Platform evals a hosted harness run selects for itself.

The guest never reaches the eval catalogue directly. The gateway offers a list in the ephemeral
job document, the guest returns names only on the scenario provision call, and the platform owns
the mapping. Deriving a mapping from a model's answer would let it bind a variable to a source
that resolves empty, and an eval scoring an empty string returns a confident verdict about
nothing, so the tables below are the only mappings this path can produce.

Ordering, once, so it is not re-derived:

1. scenario provision creates the ``RunTest`` and, from ``chosen_evals``, one
   ``SimulateEvalConfig`` per selected template
2. calls run, each receipt lands on its ``CallExecution``
3. a row reaching ``COMPLETED`` dispatches the platform evaluator with exactly the config ids
   from step 1 (``runnable_eval_config_ids``)
4. results attach to the call and roll up to the ``TestExecution``

The harness's own deterministic checkpoints are unaffected by all of this; they arrive on the
receipt and are written straight onto the call.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from django.db.models import Q

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import RunTest, SimulateEvalConfig

logger = structlog.get_logger(__name__)

# Selected evals are bound to a run through a stable id so a retried provision cannot double
# them. Distinct from the namespace used for harness-computed result columns.
_SELECTED_EVAL_NAMESPACE = uuid.UUID("2b0f2f19-2c65-4b1e-9c9a-2f1a3b4c5d6e")

# A run cannot select more than this. Every selected eval is one judge call per call in the
# suite, so a two-hundred-scenario run at this cap is already sixteen hundred judge calls.
MOST_SELECTED_EVALS = 8

# Templates offered to a hosted run: the customer-agent family, plus the voice-oriented evals
# numbered from 200. Karthik named 202 onwards; 200 and 201 are included because conversation
# hallucination and dead air are as voice-relevant as the rest of that block.
_OFFERED_NAME_PREFIX = "customer_agent"
_OFFERED_FROM_EVAL_ID = 200

# Evals in the offered set that only mean something on a spoken call. Dead air, voicemail
# detection and voicemail handling have no analogue in a chat transcript.
_VOICE_ONLY_EVALS = frozenset(
    {
        "dead_air_detection",
        "voice_mail_detection",
        "voicemail_handling",
    }
)

# Required key to the source alias the eval runner already resolves. Voice and chat differ in
# one place only, and that place is the point of the split.
_SOURCE_BY_KEY_VOICE = {
    # The whole conversation as audio. `voice_recording` is the combined recording, which is
    # what `assert_recording_slot_available` names as the correct whole-conversation source; a
    # per-channel or stereo mapping resolves empty on combined-only providers.
    "conversation": "voice_recording",
    # A single-output eval on a call is judging the same conversation.
    "output": "voice_recording",
    # Both names mean the target agent's own instructions, resolved from the agent version's
    # configuration snapshot.
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
    """Templates this organization may select, system-owned ones included.

    Scoped the same way `_resolve_harness_eval_template` scopes its lookup: the organization's
    own templates plus the system ones, never another tenant's.
    """
    visible_scope = Q(organization=organization) | Q(organization__isnull=True)
    workspace_scope = Q(workspace=workspace) | Q(workspace__isnull=True)
    return EvalTemplate.no_workspace_objects.filter(
        visible_scope, workspace_scope, deleted=False
    )


def resolve_eval_mapping(
    template: EvalTemplate, modality: str
) -> dict[str, str] | None:
    """The mapping for one template, or None when any required key has no source.

    None is a refusal to run it. Returning a partial mapping would hand the evaluator an empty
    variable, which is the failure this whole module exists to prevent.
    """
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
    for template in _visible_templates(organization, workspace).order_by("name"):
        name = str(template.name or "")
        if not (
            name.startswith(_OFFERED_NAME_PREFIX)
            or (template.eval_id or 0) >= _OFFERED_FROM_EVAL_ID
        ):
            continue
        if modality != "voice" and name in _VOICE_ONLY_EVALS:
            continue
        mapping = resolve_eval_mapping(template, modality)
        if mapping is None:
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


def create_selected_eval_configs(
    run_test: RunTest, chosen: list[str], modality: str
) -> list[SimulateEvalConfig]:
    """Bind the guest's chosen eval names to this run, mapping them here rather than there.

    Raises ``UnknownEvalSelection`` for a name outside the organization's visible templates: a
    hosted guest can only have received names from ``offered_evals``, so an unknown one is a
    real mismatch and must not resolve to some other tenant's template or be dropped quietly.

    The config itself holds no organization column; it is scoped by the ``RunTest`` it points
    at, which is why the template lookup is scoped to that run's organization and workspace.
    """
    wanted = [str(name).strip() for name in (chosen or []) if str(name).strip()]
    if not wanted:
        return []
    seen: set[str] = set()
    ordered = [name for name in wanted if not (name in seen or seen.add(name))]

    found = {
        str(template.name): template
        for template in _visible_templates(
            run_test.organization, run_test.workspace
        ).filter(name__in=ordered)
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
                "model": template.model,
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
    """Config ids on this run that the platform evaluator can actually execute.

    A non-empty mapping is what separates a selected eval from a harness result column: the
    columns created for harness-computed judgements carry ``mapping: {}`` deliberately, and
    running one would feed the evaluator nothing.
    """
    return [
        str(config_id)
        for config_id, mapping in SimulateEvalConfig.objects.filter(
            run_test_id=run_test_id, deleted=False
        ).values_list("id", "mapping")
        if mapping
    ]
