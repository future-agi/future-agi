from __future__ import annotations

from typing import Any

import structlog

from model_hub.models.evals_metric import EvalTemplate

logger = structlog.get_logger(__name__)


def stamp_evaluation_axes(evaluation: Any) -> None:
    """Populate typed output columns on ``evaluation`` from ``value``; additive only.

    Delegates to ``tracer/utils/eval.py:_dual_write_eval_value`` (Sarthak's PR
    #618 helper) with ``permissive_secondary_axis=True`` so ``choice_scores``
    rows fill both ``output_float`` and ``output_str_list``. Tracer's own seven
    callers stay on the default ``False`` and behave exactly as before.

    Lifting the helper into the shared ``evaluations/engine/normalize`` module
    is a follow-up; for now both surfaces share the same function object.
    """
    if evaluation.value is None:
        return

    template_config: dict = {}
    template_lookup_failed = False
    try:
        if evaluation.eval_template_id is not None:
            template_config = evaluation.eval_template.config or {}
    except (AttributeError, EvalTemplate.DoesNotExist):
        template_lookup_failed = True

    config_output = template_config.get("output") or evaluation.output_type
    if config_output is None:
        config_output = "score"
        logger.warning(
            "evaluation_axes_output_fallback",
            evaluation_id=str(getattr(evaluation, "id", "")) or None,
            eval_template_id=str(evaluation.eval_template_id or "") or None,
            template_lookup_failed=template_lookup_failed,
        )

    from tracer.utils.eval import _dual_write_eval_value

    projected: dict[str, Any] = {}
    try:
        _dual_write_eval_value(
            evaluation.value,
            config_output,
            projected,
            permissive_secondary_axis=True,
        )
    except Exception:
        logger.warning(
            "evaluation_axes_resolve_failed",
            evaluation_id=str(getattr(evaluation, "id", "")) or None,
            eval_template_id=str(evaluation.eval_template_id or "") or None,
            config_output=config_output,
            exc_info=True,
        )
        if evaluation.output_str is None:
            evaluation.output_str = str(evaluation.value)
        return

    for col in ("output_bool", "output_float", "output_str_list", "output_str"):
        if col in projected and getattr(evaluation, col) is None:
            setattr(evaluation, col, projected[col])

    if (
        evaluation.output_bool is None
        and evaluation.output_float is None
        and evaluation.output_str_list is None
        and evaluation.output_str is None
    ):
        evaluation.output_str = str(evaluation.value)
