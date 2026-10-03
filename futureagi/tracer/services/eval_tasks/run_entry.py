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

from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, EvalTargetType
from tracer.services.clickhouse.v2.eval_loader import EvalTelemetryReadError
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import (
    mark_terminal,
    running_entry_epoch,
    writing_onto_entry,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# The run produced nothing for this entry: the row left RUNNING, or the claim
# the run was writing under is no longer the row's, so every write it made was
# refused. Distinct from a terminal status because it is not one -- the entry
# belongs to whoever holds it now, and the drain must not report a verdict, a
# spent attempt or a clustering seed for a row it does not own.
RECLAIMED = "reclaimed"


def run_entry(entry: EvalLogger) -> str:
    """Run the eval for one entry and record its terminal status; returns it.

    No-op (returns ``"deleted"``) if the entry was soft-deleted mid-run — a
    Delete & rerun landing while it ran, or ``"reclaimed"`` if the row is no
    longer under the claim this run took: refused before the eval when the row
    has left RUNNING, and after it when the terminal write is fenced out by a
    claim taken in the meantime. Eval/data failures converge to a terminal
    state; infrastructure read failures propagate to the activity's bounded
    retry policy.
    """
    fresh = EvalLogger.objects.filter(id=entry.id).first()
    if fresh is None:
        return "deleted"

    # Open a write fence before doing anything that costs money, and re-stamp
    # the claim so staleness means "running for this long" rather than "claimed
    # this long ago": ``claim_pending_batch`` marks a whole batch RUNNING at
    # once and the drain runs it a few at a time, so a batch tail can sit
    # claimed for hours before its run starts. Every write this run makes is
    # then fenced on the new stamp, and a run whose entry was requeued and
    # re-claimed while it worked has its result refused rather than landing on
    # somebody else's claim.
    #
    # What this cannot see is a claim that was superseded *before* the run
    # began: the activity is handed an entry id and nothing else, so a row that
    # is RUNNING again under a newer claim is indistinguishable from one still
    # under ours, and the compare-and-set takes it. The "reclaimed" branch below
    # catches only a row that has left RUNNING. Reaching the blind case needs a
    # reaper to requeue an entry whose activity is still queued, which is why
    # the scheduled sweep asks Temporal before it reaps and leaves a progressing
    # workflow alone (``tracer.services.eval_tasks.recovery.recover_task``).
    # Carrying the claim epoch through ClaimBatchOutput -> RunEntryInput would
    # close it at the fence itself; that is a wire-format change and its own
    # decision.
    if fresh.status != EvalEntryStatus.RUNNING:
        return RECLAIMED
    run_epoch = timezone.now()
    if not EvalLogger.objects.filter(
        id=fresh.id,
        status=EvalEntryStatus.RUNNING,
        updated_at=fresh.updated_at,
    ).update(updated_at=run_epoch):
        return RECLAIMED
    fresh.updated_at = run_epoch

    config = CustomEvalConfig.objects.select_related("project").get(
        id=fresh.custom_eval_config_id
    )
    # Infrastructure reads happen before the terminalizing evaluator boundary:
    # a transient PG failure loading task ownership must be retried by Temporal,
    # not recorded as a permanent eval-entry error on its first attempt.
    task_project = (
        EvalTask.objects.select_related("project").get(id=fresh.eval_task_id).project
    )
    config_hash = resolved_config_hash(config)

    # Nothing here bounds how long the evaluation itself may take. The only
    # bound on one entry is the run-entry activity's start-to-close ceiling,
    # applied by Temporal outside this process; see
    # ``tfc.temporal.eval_tasks.workflows._RUN_ENTRY_TIMEOUT`` for what that
    # does and does not stop.
    with running_entry_epoch(run_epoch):
        try:
            _run_for_target(fresh, config, task_project=task_project)
        except EvalTelemetryReadError:
            # CH transport/query pressure is infrastructure, not an eval result.
            # Bubble it to the activity so its bounded retry policy can retry the
            # same still-RUNNING entry instead of freezing a transient miss.
            raise
        except Exception as e:  # Every failure becomes a terminal state.
            skipped_reason = getattr(e, "skipped_reason", None)
            if skipped_reason:
                landed = mark_terminal(
                    fresh,
                    EvalEntryStatus.SKIPPED,
                    config_hash=config_hash,
                    error=False,
                    skipped_reason=skipped_reason,
                    epoch=run_epoch,
                )
                return EvalEntryStatus.SKIPPED if landed else RECLAIMED
            logger.warning("run_entry failed for %s: %s", fresh.id, e, exc_info=True)
            landed = mark_terminal(
                fresh,
                EvalEntryStatus.ERRORED,
                config_hash=config_hash,
                error=True,
                error_message=str(e),
                epoch=run_epoch,
            )
            return EvalEntryStatus.ERRORED if landed else RECLAIMED

    # The evaluator wrote the result onto the entry; read its error flag to pick
    # the terminal status, then stamp status + hash.
    fresh.refresh_from_db()
    status = EvalEntryStatus.ERRORED if fresh.error else EvalEntryStatus.COMPLETED
    # Explicit rather than scoped: this call is outside ``running_entry_epoch``,
    # and the refresh above has already replaced ``fresh.updated_at`` with
    # whatever the row now carries — which, after a reclaim, is someone else's.
    if not mark_terminal(fresh, status, config_hash=config_hash, epoch=run_epoch):
        # The status was read off a row this run no longer owns, and the
        # refreshed instance carries the re-claimer's state — so it is not this
        # run's verdict to report, and seeding clustering from it would cluster
        # another claim's result.
        return RECLAIMED
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

    The dispatch itself (per-project coalescing, fail-open logging) lives in
    ``dispatch_eval_clustering`` — shared with the span-eval wrapper, which is
    the trigger for feedback-driven re-evals that never reach ``run_entry``.
    A coalesced trigger is dropped by design; the drain it folds into keeps
    re-fetching until empty, so the rows behind that trigger are still picked up.
    """
    # Keep the trigger gate aligned with the fetch query. This also handles
    # choice-scored templates whose score is stored in output_str/list and
    # resolved through the template config.
    from tracer.queries.eval_clustering import is_clusterable_eval_failure

    if not is_clusterable_eval_failure(entry):
        return
    # Lazy import: the tasks module pulls the tracer task graph, so importing at
    # module top risks a cycle (mirrors eval.py).
    from tracer.tasks.eval_clustering import dispatch_eval_clustering

    dispatch_eval_clustering(project_id)


def _run_for_target(
    entry: EvalLogger, config: CustomEvalConfig, *, task_project
) -> None:
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

    with (
        eval_read_source("clickhouse"),
        writing_onto_entry(
            entry.id,
            output_metadata=entry.output_metadata,
        ),
    ):
        if entry.target_type == EvalTargetType.SPAN:
            span = get_observation_span(
                entry.observation_span_id,
                select_related=(
                    "project",
                    "project__organization",
                    "project__workspace",
                ),
                project_id=task_project.id,
            )
            run_params = _process_mapping(config.mapping, span, template_id)
            result = _execute_evaluation(
                observation_span_id=entry.observation_span_id,
                custom_eval_config_id=config.id,
                eval_task_id=task_id,
                run_params=run_params,
                type=OBSERVE,
                observation_span=span,
                project_id=task_project.id,
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
                project_id=task_project.id,
            )
            task_selection = (
                entry.output_metadata.get("_task_selection")
                if isinstance(entry.output_metadata, dict)
                else None
            )
            filter_witnesses = (
                task_selection.get("filter_witnesses")
                if isinstance(task_selection, dict)
                else None
            )
            run_params = resolve_trace_mapping_lean_first(
                config.mapping,
                trace,
                template_id,
                filter_witnesses=filter_witnesses,
            )
            _execute_evaluation_for_trace(
                trace=trace,
                anchor_span=_find_anchor_span(trace),
                custom_eval_config=config,
                eval_task_id=task_id,
                run_params=run_params,
            )
        elif entry.target_type == EvalTargetType.SESSION:
            session = get_trace_session(entry.trace_session_id, project=task_project)
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
