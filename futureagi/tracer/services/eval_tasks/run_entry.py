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
    if status == EvalEntryStatus.COMPLETED:
        _reseed_eval_clustering(fresh, config.project_id)
    return status


def _reseed_eval_clustering(entry: EvalLogger, project_id) -> None:
    """Re-trigger eval-result clustering for a completed *failing* eval-task eval.

    Clustering used to be seeded inside the ``evaluate_*_observe`` wrappers, which
    the (now-retired) eval-task cron drove. The per-task workflows that replaced
    the cron call the inner eval cores directly and bypass those wrappers, so the
    trigger has to live here or eval-task failures never cluster — the exact gap
    the cutover opened. ``run_entry`` is the single activity core both the
    historical AND continuous workflows drain every entry through, so hooking it
    covers both (a per-task-completion hook would miss continuous tasks, which
    never finalize).

    Coalesced per project via the fixed ``eval-cluster-{project_id}`` id +
    USE_EXISTING; ``cluster_eval_results_task`` drains the project's backlog in
    one run, so a burst of triggers collapses onto one draining run and loses
    nothing. Fail-open, but at WARNING — never DEBUG: a silently swallowed
    dispatch is exactly what hid the cutover regression. A clustering hiccup must
    not fail an eval that already produced a result, but it must stay visible.
    """
    # Mirror _FAILING_EVAL_Q's failure clause. A failing eval with no explanation
    # has nothing to embed/cluster, so skip the no-op dispatch RPC.
    is_clusterable_failure = (
        entry.output_bool is False
        or (entry.output_float is not None and entry.output_float < 1.0)
    ) and entry.eval_explanation
    if not is_clusterable_failure:
        return
    try:
        # Lazy import: cluster_eval_results_task's module pulls the tracer task
        # graph, so importing at module top risks a cycle (mirrors eval.py).
        from temporalio.common import WorkflowIDConflictPolicy

        from tracer.tasks.eval_clustering import cluster_eval_results_task

        cluster_eval_results_task.apply_async(
            args=(str(project_id),),
            task_id=f"eval-cluster-{project_id}",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
    except Exception:
        logger.warning(
            "eval_clustering_dispatch_failed for project %s", project_id, exc_info=True
        )


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
