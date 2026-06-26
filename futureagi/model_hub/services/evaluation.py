from __future__ import annotations

from typing import Any

import structlog

from evaluations.engine.normalize import resolve_eval_axes
from model_hub.models.evals_metric import EvalTemplate

logger = structlog.get_logger(__name__)


def stamp_evaluation_axes(evaluation: Any) -> None:
    """Recompute typed output columns from the current ``value`` on every save."""
    if evaluation.value is None:
        return

    template_config: dict = {}
    if evaluation.eval_template_id is not None:
        try:
            template_config = evaluation.eval_template.config or {}
        except (AttributeError, EvalTemplate.DoesNotExist):
            pass

    config_output = template_config.get("output") or evaluation.output_type or "score"

    try:
        projected = resolve_eval_axes(
            evaluation.value, config_output, include_output_str=True
        )
    except (TypeError, ValueError, KeyError, AttributeError):
        logger.warning(
            "evaluation_axes_resolve_failed",
            evaluation_id=str(getattr(evaluation, "id", "")) or None,
            eval_template_id=str(evaluation.eval_template_id or "") or None,
            config_output=config_output,
            exc_info=True,
        )
        evaluation.output_str = str(evaluation.value)
        return

    for col, projected_value in projected.items():
        setattr(evaluation, col, projected_value)

    if (
        evaluation.output_bool is None
        and evaluation.output_float is None
        and evaluation.output_str_list is None
        and evaluation.output_str is None
    ):
        evaluation.output_str = str(evaluation.value)
