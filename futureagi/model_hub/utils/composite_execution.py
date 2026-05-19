"""
Shared composite eval execution helper (Phase 7 wiring — Phase B).

Runs all children of a composite `EvalTemplate` against a single set of
resolved inputs and returns aggregated results. Both the one-shot
`CompositeEvalExecuteView` endpoint and the dataset/experiment runner
(`CompositeEvaluationRunner`) delegate to `execute_composite_children_sync`
so aggregation semantics stay consistent across surfaces.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
from model_hub.types import CompositeChildResult
from model_hub.utils.composite_aggregation import (
    aggregate_error_localizers,
    aggregate_scores,
    aggregate_summaries,
)
from model_hub.utils.scoring import determine_pass_fail, normalize_score

logger = logging.getLogger(__name__)


@dataclass
class CompositeRunOutcome:
    """Shape returned by `execute_composite_children_sync`."""

    child_results: list[CompositeChildResult]
    aggregate_score: float | None
    aggregate_pass: bool | None
    summary: str | None
    error_localizer_results: dict | None
    log_id: str | None = field(default=None)


def resolve_child_weights(
    child_links: list[CompositeEvalChild],
    weight_overrides: dict[str, float] | None,
) -> dict[str, float]:
    """Merge per-binding weight overrides with the template's global weights.

    `weight_overrides` is the `UserEvalMetric.composite_weight_overrides`
    map: `{child_template_id: weight}`. Any child not present in the
    overrides falls back to its `CompositeEvalChild.weight`.
    """
    overrides = weight_overrides or {}
    resolved: dict[str, float] = {}
    for link in child_links:
        key = str(link.child_id)
        value = overrides.get(key)
        resolved[key] = float(value) if isinstance(value, int | float) else link.weight
    return resolved


def _execute_child(
    *,
    child_template: EvalTemplate,
    link: CompositeEvalChild,
    weight: float,
    mapping: dict[str, Any],
    config: dict[str, Any],
    model: str | None,
    org,
    workspace,
    input_data_types: dict[str, str],
    row_context: dict | None,
    span_context: dict | None,
    trace_context: dict | None,
    session_context: dict | None,
    call_context: dict | None,
    error_localizer: bool,
    source: str,
) -> CompositeChildResult:
    """Run a single child eval and return a normalized result row.

    Any per-child failure is caught and returned as a `failed` result so
    the composite as a whole keeps running across the remaining children.
    """
    from model_hub.views.utils.evals import run_eval_func

    try:
        runtime_config = dict(config) if config else {}

        # Version pinning: if this child is pinned, snapshot overrides the
        # template's live config/criteria/model for this run only.
        if link.pinned_version:
            version = link.pinned_version
            if version.config_snapshot:
                runtime_config.update(version.config_snapshot)
            if version.criteria:
                runtime_config["criteria"] = version.criteria
            if version.model:
                runtime_config["model"] = version.model

        effective_model = model or child_template.model or None

        result = run_eval_func(
            runtime_config,
            mapping,
            child_template,
            org,
            model=effective_model,
            error_localizer=error_localizer,
            source=source,
            workspace=workspace,
            input_data_types=input_data_types or {},
            row_context=row_context,
            span_context=span_context,
            trace_context=trace_context,
            session_context=session_context,
            call_context=call_context,
        )

        score: float | None = None
        _output_type = child_template.output_type_normalized
        if not _output_type:
            # Older / code-created templates may not have output_type_normalized
            # set. Derive it from config["output"] before falling back to a
            # dumb numeric cast so "Passed"/"Failed" strings score correctly.
            _config_output = (getattr(child_template, "config", None) or {}).get("output", "")
            _output_type = {
                "Pass/Fail": "pass_fail",
                "score": "percentage",
                "choices": "choices",
            }.get(_config_output)

        if _output_type:
            score = normalize_score(
                result.get("output"),
                _output_type,
                child_template.choice_scores,
            )
        else:
            # Last resort — best-effort numeric fallback.
            try:
                raw = result.get("output")
                if raw is not None:
                    score = max(0.0, min(1.0, float(raw)))
            except (ValueError, TypeError):
                logger.warning(
                    "Child %s has no output_type_normalized and a non-numeric "
                    "output — skipping in aggregation",
                    child_template.name,
                )

        return CompositeChildResult(
            child_id=str(child_template.id),
            child_name=child_template.name,
            order=link.order,
            score=score,
            output=result.get("output"),
            reason=result.get("reason"),
            output_type=result.get("output_type"),
            status="completed",
            log_id=result.get("log_id"),
            weight=weight,
        )

    except Exception as e:  # noqa: BLE001
        logger.warning("Child eval %s failed: %s", child_template.name, str(e))
        return CompositeChildResult(
            child_id=str(child_template.id),
            child_name=child_template.name,
            order=link.order,
            status="failed",
            error=str(e),
            weight=weight,
        )


def _log_composite_usage(
    *,
    parent: EvalTemplate,
    org,
    workspace,
    model: str | None,
    source: str,
    mapping: dict[str, Any],
    child_results: list[CompositeChildResult],
    aggregate_score: float | None,
    aggregate_pass: bool | None,
    summary: str | None,
    duration: float,
) -> str | None:
    """Create a zero-cost APICallLog for the composite parent template.

    This makes the composite visible on its usage page. Children already
    created their own logs (with billing) via ``run_eval_func``, so this
    record carries ``cost=0`` to avoid double-charging.
    """
    try:
        from sdk.utils.helpers import _get_api_call_type
        try:
            from ee.usage.models.usage import APICallLog, APICallStatusChoices, APICallType
        except ImportError:
            APICallLog = None
            from tfc.oss_stubs.usage import APICallStatusChoices
            APICallType = None

        api_call_type_name = _get_api_call_type(model)
        api_call_type_obj = APICallType.objects.get(name=api_call_type_name)

        completed = sum(1 for cr in child_results if cr.status == "completed")
        failed = sum(1 for cr in child_results if cr.status == "failed")

        # Ensure all mapping values are JSON-serializable strings
        safe_mappings = {}
        for k, v in (mapping or {}).items():
            if isinstance(v, (dict, list)):
                safe_mappings[k] = v
            else:
                safe_mappings[k] = str(v)[:200]

        config_payload = {
            "composite": True,
            "source": source,
            "reference_id": str(parent.id),
            "aggregation_enabled": parent.aggregation_enabled,
            "aggregation_function": parent.aggregation_function,
            "mappings": safe_mappings,
            "output": {
                "output": aggregate_score,
                "aggregate_pass": aggregate_pass,
                "reason": summary or "",
            },
            "children": [
                {
                    "child_id": cr.child_id,
                    "child_name": cr.child_name,
                    "score": cr.score,
                    "status": cr.status,
                    "weight": cr.weight,
                    "output": (str(cr.output)[:200] if cr.output is not None else None),
                    "reason": (cr.reason or "")[:200],
                }
                for cr in child_results
            ],
            "total_children": len(child_results),
            "completed_children": completed,
            "failed_children": failed,
            "duration": duration,
        }

        status = (
            APICallStatusChoices.SUCCESS.value
            if completed > 0
            else APICallStatusChoices.ERROR.value
        )

        # Pass dict directly — config is a JSONField, so Django handles
        # serialization. Using json.dumps() here would double-encode.
        log_row = APICallLog.objects.create(
            api_call_type=api_call_type_obj,
            organization=org,
            workspace=workspace,
            cost=0,
            deducted_cost=0,
            status=status,
            config=config_payload,
            reference_id=str(parent.id),
            source=source,
            source_id=str(parent.id),
        )
        return str(log_row.log_id)
    except Exception:
        logger.exception("Failed to create composite-level APICallLog")
        return None


def execute_composite_children_sync(
    *,
    parent: EvalTemplate,
    child_links: list[CompositeEvalChild],
    mapping: dict[str, Any],
    config: dict[str, Any] | None,
    org,
    workspace=None,
    model: str | None = None,
    input_data_types: dict[str, str] | None = None,
    row_context: dict | None = None,
    span_context: dict | None = None,
    trace_context: dict | None = None,
    session_context: dict | None = None,
    call_context: dict | None = None,
    error_localizer: bool = False,
    source: str = "composite_eval",
    weight_overrides: dict[str, float] | None = None,
) -> CompositeRunOutcome:
    """Run every child of a composite against shared inputs and aggregate.

    Responsibilities:
    - Execute children in `order` (`child_links` is assumed pre-sorted).
    - Apply per-binding weight overrides if provided.
    - Aggregate only when `parent.aggregation_enabled`; otherwise return
      raw child results with a null aggregate.
    - Defer pass/fail until a numeric aggregate is actually available.

    The caller is responsible for:
    - Filtering / validating children against `composite_child_axis` if the
      parent declares one. This helper trusts what it is handed.
    - Providing resolved (non-template) mapping values. For dataset-row
      callers that means inputs already pulled from cells.
    """
    _start_time = time.time()
    weights = resolve_child_weights(child_links, weight_overrides)

    child_results: list[CompositeChildResult] = []
    for link in child_links:
        child_results.append(
            _execute_child(
                child_template=link.child,
                link=link,
                weight=weights[str(link.child_id)],
                mapping=mapping,
                config=config or {},
                model=model,
                org=org,
                workspace=workspace,
                input_data_types=input_data_types or {},
                row_context=row_context,
                span_context=span_context,
                trace_context=trace_context,
                session_context=session_context,
                call_context=call_context,
                error_localizer=error_localizer,
                source=source,
            )
        )

    aggregate_score: float | None = None
    aggregate_pass: bool | None = None
    summary: str | None = None

    if parent.aggregation_enabled:
        threshold_map = {
            str(link.child_id): (
                link.child.pass_threshold
                if link.child.pass_threshold is not None
                else 0.5
            )
            for link in child_links
        }
        scores_and_weights: list[tuple[float, float]] = []
        child_thresholds: list[float] = []
        for cr in child_results:
            if cr.status == "completed" and cr.score is not None:
                scores_and_weights.append((cr.score, cr.weight))
                child_thresholds.append(threshold_map.get(cr.child_id, 0.5))
            elif cr.status == "completed" and cr.score is None:
                logger.warning(
                    "Child %s completed but has no score — excluded from aggregation",
                    cr.child_name,
                )

        aggregate_score = aggregate_scores(
            scores_and_weights,
            parent.aggregation_function,
            child_thresholds=child_thresholds,
        )

        if aggregate_score is not None:
            parent_threshold = (
                parent.pass_threshold if parent.pass_threshold is not None else 0.5
            )
            aggregate_pass = determine_pass_fail(aggregate_score, parent_threshold)

        summary = aggregate_summaries(child_results)

    # Error localizer dict is cheap to compute either way — it is consumed
    # regardless of aggregation mode so the UI can drill into failing
    # children.
    error_localizer_results = aggregate_error_localizers(child_results) or None

    # Create a composite-level APICallLog so the composite template's
    # usage page shows runs. Children already created their own logs via
    # `run_eval_func`; this one is zero-cost (tracking only, no billing).
    log_id = _log_composite_usage(
        parent=parent,
        org=org,
        workspace=workspace,
        model=model,
        source=source,
        mapping=mapping,
        child_results=child_results,
        aggregate_score=aggregate_score,
        aggregate_pass=aggregate_pass,
        summary=summary,
        duration=time.time() - _start_time,
    )

    return CompositeRunOutcome(
        child_results=child_results,
        aggregate_score=aggregate_score,
        aggregate_pass=aggregate_pass,
        summary=summary,
        error_localizer_results=error_localizer_results,
        log_id=log_id,
    )
