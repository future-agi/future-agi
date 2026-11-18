"""Transitive hash of a resolved eval definition.

``resolved_config_hash`` answers one question: would re-running this eval on a
row right now give a different result than the one already stored? It rolls the
config instance, its template, the template's composite children (recursively),
and any pinned child versions into a single sha256 — covering everything that
determines output and nothing cosmetic.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from model_hub.models.evals_metric import CompositeEvalChild

if TYPE_CHECKING:
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

_CONFIG_FIELDS = ("config", "mapping", "model", "error_localizer")

_TEMPLATE_FIELDS = (
    "config",
    "criteria",
    "choices",
    "multi_choice",
    "model",
    "pass_threshold",
    "choice_scores",
    "output_type_normalized",
    "eval_type",
    "template_type",
    "error_localizer_enabled",
    "evaluator_id",
)

_COMPOSITE_FIELDS = (
    "aggregation_enabled",
    "aggregation_function",
    "composite_child_axis",
)


def resolved_config_hash(config: CustomEvalConfig) -> str:
    payload: dict[str, Any] = {
        field: getattr(config, field) for field in _CONFIG_FIELDS
    }
    payload["kb"] = str(config.kb_id_id) if config.kb_id_id else None
    payload["template"] = _template_component(config.eval_template)
    return _sha256_canonical(payload)


def _sha256_canonical(obj: Any) -> str:
    serialized = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _template_component(
    template: EvalTemplate, stack: tuple[UUID, ...] = ()
) -> dict[str, Any]:
    if template.id in stack:
        return {"cycle": str(template.id)}
    component: dict[str, Any] = {
        field: getattr(template, field) for field in _TEMPLATE_FIELDS
    }
    if template.template_type == "composite":
        for field in _COMPOSITE_FIELDS:
            component[field] = getattr(template, field)
        component["children"] = _children_component(template, (*stack, template.id))
    return component


def _children_component(
    template: EvalTemplate, stack: tuple[UUID, ...]
) -> list[dict[str, Any]]:
    links = (
        CompositeEvalChild.objects.filter(parent=template, deleted=False)
        .select_related("child")
        .order_by("order")
    )
    return [
        {
            "order": link.order,
            "weight": link.weight,
            "config": link.config,
            "child": _child_content(link, stack),
        }
        for link in links
    ]


def _child_content(link: CompositeEvalChild, stack: tuple[UUID, ...]) -> dict[str, Any]:
    if link.pinned_version_id:
        # Versions are immutable, so the id is a complete fingerprint of the pin.
        return {"pinned_version": str(link.pinned_version_id)}
    return _template_component(link.child, stack)
