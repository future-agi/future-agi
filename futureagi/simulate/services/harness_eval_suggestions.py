"""The evals a build suggests, and whether each added eval is required and the score it must reach."""

from __future__ import annotations

import math
from typing import Any, TypedDict
from uuid import UUID

from django.db import transaction
from django.db.models import Q

from evaluations.engine.instance import resolve_pass_threshold
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import HostedHarnessJob, RunTest, SimulateEvalConfig
from simulate.services.harness_evals import (
    MOST_SELECTED_EVALS,
    add_selected_eval,
    bound_eval_names,
    eligible_selections,
    eval_entry,
    offered_templates,
)

SUGGESTED_EVALS_KEY = "suggested_evals"
REQUIRED_KEY = "required"
PASS_THRESHOLD_KEY = "pass_threshold"
ENGINE_DEFAULT_THRESHOLD = 0.5


class EvalRequirement(TypedDict):
    required: bool
    pass_threshold: float


def _threshold(template: EvalTemplate, runtime: dict[str, Any] | None) -> float:
    for source in (runtime, None):
        try:
            value = resolve_pass_threshold(template, source)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value) and 0 <= value <= 1:
            return value
    return ENGINE_DEFAULT_THRESHOLD


def _runtime(config: SimulateEvalConfig) -> dict[str, Any]:
    return config.config if isinstance(config.config, dict) else {}


def is_required(config: SimulateEvalConfig) -> bool:
    return _runtime(config).get(REQUIRED_KEY) is True


def eval_requirement(config: SimulateEvalConfig) -> EvalRequirement:
    runtime = _runtime(config)
    return {
        "required": is_required(config),
        "pass_threshold": _threshold(config.eval_template, runtime),
    }


def _with_requirement(
    runtime: dict[str, Any], *, required: bool | None, pass_threshold: float | None
) -> dict[str, Any]:
    updated = dict(runtime)
    if required is not None:
        updated[REQUIRED_KEY] = required
    if pass_threshold is not None:
        run_config = updated.get("run_config")
        if (
            isinstance(run_config, dict)
            and run_config.get(PASS_THRESHOLD_KEY) is not None
        ):
            updated["run_config"] = {**run_config, PASS_THRESHOLD_KEY: pass_threshold}
        else:
            updated[PASS_THRESHOLD_KEY] = pass_threshold
    return updated


def _apply_requirement(
    config: SimulateEvalConfig,
    *,
    required: bool | None,
    pass_threshold: float | None,
) -> None:
    if required is None and pass_threshold is None:
        return
    config.config = _with_requirement(
        _runtime(config), required=required, pass_threshold=pass_threshold
    )
    config.save(update_fields=["config", "updated_at"])


def add_eval_with_requirement(
    run_test: RunTest,
    name: str,
    modality: str,
    *,
    required: bool | None = None,
    pass_threshold: float | None = None,
) -> SimulateEvalConfig:
    with transaction.atomic():
        config = add_selected_eval(run_test, name, modality)
        _apply_requirement(config, required=required, pass_threshold=pass_threshold)
    return config


def set_eval_requirement(
    run_test: RunTest,
    config_id: UUID,
    *,
    required: bool | None = None,
    pass_threshold: float | None = None,
) -> SimulateEvalConfig | None:
    with transaction.atomic():
        config = (
            SimulateEvalConfig.objects.select_for_update(of=("self",))
            .select_related("eval_template")
            .filter(id=config_id, run_test=run_test, deleted=False)
            .exclude(Q(mapping__isnull=True) | Q(mapping={}))
            .first()
        )
        if config is None:
            return None
        _apply_requirement(config, required=required, pass_threshold=pass_threshold)
    return config


def store_suggested_evals(
    job: HostedHarnessJob, run_test: RunTest, chosen: list[str], modality: str
) -> None:
    names = [
        name
        for name, _template, _mapping in eligible_selections(run_test, chosen, modality)
    ]
    payload = dict(job.payload or {})
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        SUGGESTED_EVALS_KEY: names[:MOST_SELECTED_EVALS],
    }
    job.payload = payload
    job.save(update_fields=["payload", "updated_at"])


def suggested_evals(
    job: HostedHarnessJob, run_test: RunTest | None, modality: str
) -> list[dict[str, Any]]:
    stored = ((job.payload or {}).get("metadata") or {}).get(SUGGESTED_EVALS_KEY)
    if run_test is None or not isinstance(stored, list) or not stored:
        return []
    bound = bound_eval_names(run_test)
    wanted = [str(name) for name in stored if str(name) not in bound]
    if not wanted:
        return []
    offered = {
        str(template.name): (template, mapping)
        for template, mapping in offered_templates(
            job.organization, job.workspace, modality
        )
    }
    rows: list[dict[str, Any]] = []
    for name in wanted:
        if name not in offered:
            continue
        template, mapping = offered[name]
        rows.append(
            {
                **eval_entry(template, mapping, modality),
                "required": False,
                "pass_threshold": _threshold(template, template.config),
            }
        )
    return rows
