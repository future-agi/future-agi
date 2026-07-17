"""run_entry — execute one claimed entry's eval and record its terminal state.

Reuses the existing per-target_type evaluation core (the same inner functions
the old ``evaluate_*_observe`` wrappers call), minus their existence-check, so
the result lands on the already-materialized entry rather than creating a new
row. Maps the outcome to a terminal status and stamps the config hash;
the temporary overlap with the wrappers goes away when they're retired at
cutover.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, EvalTargetType
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import mark_terminal, writing_onto_entry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def run_entry(entry: EvalLogger) -> str:
    """Run the eval for one entry and record its terminal status; returns it.

    No-op (returns ``"deleted"``) if the entry was soft-deleted mid-run — a
    Delete & rerun landing while it ran. Every failure converges to a terminal
    state here so one bad item never aborts the drain.
    """
    fresh = EvalLogger.objects.filter(id=entry.id).first()
    if fresh is None:
        return "deleted"

    config = CustomEvalConfig.objects.select_related("project").get(
        id=fresh.custom_eval_config_id
    )
    config_hash = resolved_config_hash(config)

    try:
        _run_for_target(fresh, config)
    except Exception as e:  # Every failure becomes a terminal state.
        skipped_reason = getattr(e, "skipped_reason", None)
        if skipped_reason:
            mark_terminal(
                fresh,
                EvalEntryStatus.SKIPPED,
                config_hash=config_hash,
                error=False,
                skipped_reason=skipped_reason,
            )
            return EvalEntryStatus.SKIPPED
        logger.warning("run_entry failed for %s: %s", fresh.id, e, exc_info=True)
        mark_terminal(
            fresh,
            EvalEntryStatus.ERRORED,
            config_hash=config_hash,
            error=True,
            error_message=str(e),
        )
        return EvalEntryStatus.ERRORED

    # The evaluator wrote the result onto the entry; read its error flag to pick
    # the terminal status, then stamp status + hash.
    fresh.refresh_from_db()
    status = EvalEntryStatus.ERRORED if fresh.error else EvalEntryStatus.COMPLETED
    mark_terminal(fresh, status, config_hash=config_hash)
    return status


def _run_for_target(entry: EvalLogger, config: CustomEvalConfig) -> None:
    """Dispatch to the per-target_type evaluation core (reused from eval.py),
    forcing eval input to load from ClickHouse for the duration."""
    from tracer.services.clickhouse.v2.eval_loader import (
        eval_read_source,
        get_observation_span,
        get_trace,
        get_trace_session,
    )
    from tracer.utils.eval import (
        OBSERVE,
        _execute_evaluation,
        _execute_evaluation_for_session,
        _execute_evaluation_for_trace,
        _find_anchor_span,
        _process_mapping,
        _write_eval_logger,
        resolve_session_mapping_lean_first,
        resolve_trace_mapping_lean_first,
    )

    task_id = entry.eval_task_id
    template_id = config.eval_template_id

    with eval_read_source("clickhouse"), writing_onto_entry(entry.id):
        if entry.target_type == EvalTargetType.SPAN:
            span = get_observation_span(
                entry.observation_span_id,
                select_related=(
                    "project",
                    "project__organization",
                    "project__workspace",
                ),
                project_id=config.project_id,
            )
            run_params = _process_mapping(config.mapping, span, template_id)
            result = _execute_evaluation(
                observation_span_id=entry.observation_span_id,
                custom_eval_config_id=config.id,
                eval_task_id=task_id,
                run_params=run_params,
                type=OBSERVE,
            )
            # Single evals write inside _execute_evaluation; composites return the
            # logger kwargs for the caller to persist (mirrors the span wrapper).
            if isinstance(result, dict) and "trace" in result:
                _write_eval_logger(result, span, config, task_id)
        elif entry.target_type == EvalTargetType.TRACE:
            trace = get_trace(
                entry.trace_id,
                select_related=(
                    "project",
                    "project__organization",
                    "project__workspace",
                ),
                project_id=config.project_id,
            )
            run_params = resolve_trace_mapping_lean_first(
                config.mapping, trace, template_id
            )
            _execute_evaluation_for_trace(
                trace=trace,
                anchor_span=_find_anchor_span(trace),
                custom_eval_config=config,
                eval_task_id=task_id,
                run_params=run_params,
            )
        elif entry.target_type == EvalTargetType.SESSION:
            session = get_trace_session(entry.trace_session_id, project=config.project)
            run_params = resolve_session_mapping_lean_first(
                config.mapping, session, template_id
            )
            _execute_evaluation_for_session(
                trace_session=session,
                custom_eval_config=config,
                eval_task_id=task_id,
                run_params=run_params,
            )
        else:
            raise ValueError(f"Unsupported target_type: {entry.target_type!r}")
