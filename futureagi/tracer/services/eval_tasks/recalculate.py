"""Recalculate on an eval result, and its recovery when the request dies.

Recalculate retires the span's live result for the config (``previous``) and
computes a new one inside the user's request. Every recalculation in flight is
recorded in ``EvalRecalculation``, written in the transaction that retires the
previous result and removed in the one that finishes the recalculation.

A result an eval task produced is an entry of that task's engine: at most one
live entry per (task, row, config). Recalculate replaces it in three steps:

1. ``begin_recalculation``: in one transaction, retire the previous result,
   insert a ``RUNNING`` replacement for the same row and config, and record
   both. Only a finished (terminal) result is recalculated. A ``PENDING`` or
   ``RUNNING`` one, whatever its task's status, and another Recalculate's
   replacement, even a finished one, are refused (``refuse_unfinished``)
   without writing anything; only the message depends on whether the task
   will still evaluate it. The retirement is fenced on the result still
   being terminal, with the stamp the request read, so a requeue or claim
   that lands in between refuses it.
2. The caller runs the replacement in the request (``run_entry``).
3. ``keep_replacement`` or ``abandon_recalculation`` finishes it and removes
   the ``EvalRecalculation`` row in the same transaction.

A result with no task (an inline Observe eval, an experiment span) is
recomputed by the per-span eval, which writes the new result itself.
``begin_untasked_recalculation`` retires the previous result, only if it is
still live and no other Recalculate of that span and config is open (a
second request is refused), and records it with no replacement;
``close_untasked_recalculation`` keeps whatever live result the span and
config hold, or puts the previous result back when there is none.

A previous result is only put back while its span, trace, session and eval
config still exist: a delete that lands while the recalculation is open
skips the retired result (deletes act on live rows), so the put-back must
not bring it back. The put-back reads the parents without locking them
(row locks there broke the common path), so a delete that has not
committed when the put-back reads them is not seen. Two later re-reads
narrow that window; neither closes it:

* once the put-back commits (``_recheck_after_commit``), which retires the
  result if the delete committed by then;
* by the sweep, on each of its ticks for ``PUT_BACK_RECHECK_SECONDS`` after
  the put-back (``recheck_put_backs``, from the ``EvalPutBack`` row the
  put-back writes). A delete that commits later is caught at the next tick,
  so the result can stay live under a deleted parent until then. A delete
  still uncommitted ``PUT_BACK_RECHECK_SECONDS`` after the put-back is not
  caught at all.

If the process dies during the eval (a deploy's SIGKILL, an OOM), the row is
left behind. ``recover_stale_recalculations`` (scheduled with the eval-task
sweep) finishes it with the same rules the request uses.

Error Feed invariant: an issue never keeps a trace whose eval no longer
exists. The previous result keeps its memberships while it may still come
back; they are retired in the same transaction that makes its retirement
final.
"""

from __future__ import annotations

from datetime import timedelta

import structlog
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from tfc.settings.runtime_setting_specs import LONGEST_RUNNING_ENTRY_SECONDS
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalPutBack,
    EvalRecalculation,
)
from tracer.services.eval_tasks.entries import (
    finish_retirement,
    restore_retired,
    retire_pending_replacement,
    soft_delete_eval_results,
)

logger = structlog.get_logger(__name__)

# The statuses a run leaves behind. Only these are ever put back.
TERMINAL_STATUSES = (
    EvalEntryStatus.COMPLETED,
    EvalEntryStatus.ERRORED,
    EvalEntryStatus.SKIPPED,
)

_UNFINISHED = (EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING)

# How old a recalculation must be before the sweep treats the request's own
# run as dead: a replacement still ``RUNNING`` under the request's claim (no
# engine attempt on it), or a result with no task whose span and config hold
# no live result. It is the bound the engine's own blind reap uses
# (``reaper.MIN_STALE_RUNNING_SECONDS``): the longest an entry's run may
# legitimately last, far longer than any request. A run that is still alive
# past it anyway cannot land a result on a task entry: its writes are fenced
# on the replacement still being ``RUNNING`` and live.
#
# It does not judge the engine's runs. A replacement the engine took over
# (its reaper or a pause requeued it: ``PENDING``, or ``RUNNING`` with
# ``attempts`` > 0) waits for as long as the task drains; the engine's reaper
# and the stranded-task sweep look after that run. Only once the task stops
# draining does the bound apply to it.
RECOVER_AFTER_SECONDS = LONGEST_RUNNING_ENTRY_SECONDS + 1

# How old a recalculation must be before the sweep finishes one that has an
# answer already: a replacement that reached a terminal status, or a result
# with no task whose span and config hold a live result again. Nothing is
# left to wait for, and finishing it twice is harmless (the request and the
# sweep both lock the row and act once), so this only keeps the sweep from
# racing a request that is about to finish it itself.
RECOVER_ANSWERED_AFTER_SECONDS = 120

# How many recalculations one sweep tick finishes at most.
RECOVER_BATCH = 200

# How long after a put-back the sweep keeps re-reading the restored result's
# parents for a delete that committed after the put-back's own checks. The
# sweep ticks every 10 minutes, so this is about six re-reads. A delete
# transaction still open after that is not caught.
PUT_BACK_RECHECK_SECONDS = 3600

# How many put-backs one sweep tick re-reads at most: the least recently read
# first (never-read ones before all others), so the batch rotates through them.
RECHECK_BATCH = 500

ALREADY_RUNNING = "A recalculation is already running for this result."
STILL_EVALUATING = (
    "This result is still being evaluated. Try again when it has finished."
)
NEVER_FINISHED = (
    "This result was never finished because its evaluation task stopped. "
    "Resume or rerun the task, then recalculate."
)
CHANGED_MEANWHILE = "This result has changed. Refresh the page and try again."


class RecalculationRefused(Exception):
    """The result cannot be recalculated now. Nothing was written."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _rows():
    """Eval results on the primary, deleted or not, in no workspace scope.

    The sweep runs without a workspace and the request with one; both act on
    rows they already identified by primary key."""
    return EvalLogger._base_manager.using("default")


def _journal():
    return EvalRecalculation.objects.using("default")


def _put_backs():
    return EvalPutBack.objects.using("default")


def _task_is_draining(task_id) -> bool:
    """Whether the task's engine will claim a ``PENDING`` entry of it."""
    from tracer.models.eval_task import EvalTask
    from tracer.selectors.eval_tasks.stranded import sweepable_statuses

    return (
        EvalTask.no_workspace_objects.filter(
            id=task_id, status__in=sweepable_statuses()
        ).exists()
        if task_id
        else False
    )


def _is_open_replacement(result_id) -> bool:
    """Whether ``result_id`` is the replacement of a Recalculate that has not
    finished yet (its run may still be going, or only its journal row is
    left to close)."""
    return bool(_journal().filter(replacement_id=result_id).exists())


def _refusal_for_unfinished(task_id) -> RecalculationRefused:
    """Why a ``PENDING`` or ``RUNNING`` task result cannot be recalculated.

    It never can: a run of its task may still finish it (a paused task lets
    its in-flight evals complete), and if none does there is no earlier
    result to keep should the recompute fail. Only the message differs."""
    if _task_is_draining(task_id):
        return RecalculationRefused(STILL_EVALUATING)
    return RecalculationRefused(NEVER_FINISHED)


def refuse_unfinished(result: EvalLogger) -> None:
    """Raise ``RecalculationRefused`` if ``result`` may not be replaced now.

    Only a finished (terminal) result may, and not while it is still another
    Recalculate's replacement: finishing that one retires or restores its
    previous result, which a second recalculation would lose track of. A
    ``PENDING`` or ``RUNNING`` result is refused whatever its task's status:
    "still being evaluated" while the task drains, "never finished" when it
    stopped (paused, finished, failed).
    """
    if _is_open_replacement(result.pk):
        raise RecalculationRefused(ALREADY_RUNNING)
    if result.status not in TERMINAL_STATUSES:
        raise _refusal_for_unfinished(result.eval_task_id)


def begin_recalculation(previous: EvalLogger) -> EvalLogger:
    """Retire ``previous`` and insert its ``RUNNING`` replacement, as one unit.

    Only a terminal ``previous`` (``refuse_unfinished``). The retirement is
    conditional on it still being live and terminal, with the status and
    ``updated_at`` stamp the caller read: a request that read it before a
    concurrent Recalculate, the engine's claim or a requeue changed it is
    refused here rather than replacing a run in flight. Returns the
    replacement.
    """
    if previous.status not in TERMINAL_STATUSES:
        raise _refusal_for_unfinished(previous.eval_task_id)
    selection = (
        previous.output_metadata.get("_task_selection")
        if isinstance(previous.output_metadata, dict)
        else None
    )
    now = timezone.now()
    with transaction.atomic(using="default"):
        if _is_open_replacement(previous.pk):
            # Another Recalculate's replacement, finished but not closed yet.
            raise RecalculationRefused(ALREADY_RUNNING)
        retired = retire_pending_replacement(
            _rows().filter(
                pk=previous.pk,
                deleted=False,
                status=previous.status,
                status__in=TERMINAL_STATUSES,
                updated_at=previous.updated_at,
            )
        )
        if not retired:
            raise _refusal_for_race(previous.pk)
        (entry,) = EvalLogger.objects.using("default").bulk_create(
            [
                EvalLogger(
                    target_type=previous.target_type,
                    observation_span_id=previous.observation_span_id,
                    trace_id=previous.trace_id,
                    trace_session_id=previous.trace_session_id,
                    custom_eval_config_id=previous.custom_eval_config_id,
                    eval_task_id=previous.eval_task_id,
                    output_metadata=(
                        {"_task_selection": selection}
                        if isinstance(selection, dict)
                        else None
                    ),
                    status=EvalEntryStatus.RUNNING,
                    config_hash=previous.config_hash,
                )
            ]
        )
        _journal().create(
            replacement_id=entry.pk,
            previous_id=previous.pk,
            eval_task_id=str(previous.eval_task_id or ""),
            started_at=now,
        )
    return entry


def _refusal_for_race(previous_id) -> RecalculationRefused:
    """Why the conditional retirement of ``previous_id`` changed nothing."""
    current = (
        _rows()
        .filter(pk=previous_id)
        .values("deleted", "status", "eval_task_id")
        .first()
    )
    if current is not None and not current["deleted"]:
        if _is_open_replacement(previous_id):
            return RecalculationRefused(ALREADY_RUNNING)
        if current["status"] not in TERMINAL_STATUSES:
            # The engine requeued or claimed it meanwhile.
            return _refusal_for_unfinished(current["eval_task_id"])
        return RecalculationRefused(CHANGED_MEANWHILE)
    if _journal().filter(previous_id=previous_id).exists():
        # Another Recalculate retired it a moment ago and is running.
        return RecalculationRefused(ALREADY_RUNNING)
    return RecalculationRefused(CHANGED_MEANWHILE)


def _lock_journal(replacement_id, *, skip_locked: bool = False):
    return (
        _journal()
        .select_for_update(skip_locked=skip_locked)
        .filter(replacement_id=replacement_id)
        .first()
    )


def _retire_for_good(previous_id) -> None:
    """The previous result stays retired: retire its Error Feed memberships.

    Only while it is retired: a previous result that is live again keeps
    them."""
    finish_retirement(_rows().filter(pk=previous_id))


def _still_there(results):
    """``results`` narrowed to those whose span, trace, session and eval
    config were not deleted (``_rows()`` rows: no soft-delete filter).

    A span or trace that only ClickHouse holds has no Postgres row to be
    deleted, so its absence does not count; a missing eval config does."""
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSession

    def deleted(model, field):
        return Exists(
            model._base_manager.using("default").filter(
                pk=OuterRef(field), deleted=True
            )
        )

    live_config = Exists(
        CustomEvalConfig._base_manager.using("default").filter(
            pk=OuterRef("custom_eval_config_id"), deleted=False
        )
    )
    return (
        results.filter(Q(custom_eval_config_id__isnull=True) | live_config)
        .exclude(deleted(ObservationSpan, "observation_span_id"))
        .exclude(deleted(Trace, "trace_id"))
        .exclude(deleted(TraceSession, "trace_session_id"))
    )


def _retire_if_parents_deleted(previous_id) -> bool:
    """The tombstone check of a put-back: retire ``previous_id`` for good if
    it is live and its span, trace, session or eval config is deleted now.

    A put-back checks its parents inside the restoring update
    (``_still_there``), so a delete that had not committed then is not seen:
    its cascade ran while the result was retired and missed it, which would
    leave the restored result live under a deleted parent. This re-reads the
    parents without locking them (row locks on the parents conflict with the
    reconciler's ``FOR UPDATE`` on configs), so it only sees a delete that
    has committed by the time it runs. It runs once the put-back commits
    (``_recheck_after_commit``) and again on each sweep tick for
    ``PUT_BACK_RECHECK_SECONDS`` (``recheck_put_backs``). Returns whether it
    retired the result."""
    with transaction.atomic(using="default"):
        live = _rows().filter(pk=previous_id, deleted=False)
        if not live.exists() or _still_there(live).exists():
            return False
        soft_delete_eval_results(live)
        _retire_for_good(previous_id)
    logger.warning(
        "recalculate_restored_result_parent_deleted",
        eval_logger_id=str(previous_id),
    )
    return True


def _watch_put_back(previous_id) -> None:
    """Record the put-back of ``previous_id`` for the sweep's later re-reads
    (``recheck_put_backs``), in the put-back's transaction.

    In a savepoint of its own: a failed write is logged and never fails the
    put-back; the after-commit check still runs."""
    try:
        with transaction.atomic(using="default"):
            _put_backs().bulk_create(
                [
                    EvalPutBack(
                        result_id=previous_id,
                        restored_at=timezone.now(),
                        checked_at=None,
                    )
                ],
                update_conflicts=True,
                unique_fields=["result"],
                update_fields=["restored_at", "checked_at"],
            )
    except DatabaseError:
        logger.exception(
            "recalculate_put_back_watch_failed",
            eval_logger_id=str(previous_id),
        )


def _recheck_after_commit(previous_id) -> None:
    """Watch a put-back for a delete that commits after it.

    Records it for the sweep's re-reads (``_watch_put_back``), and runs the
    tombstone check once the caller's transaction commits: the request's
    close, the sweep and the reconciler all put results back in one. Never
    fails the caller: a failed check is logged and the result stays as the
    put-back left it, for the sweep to re-read."""
    _watch_put_back(previous_id)

    def check():
        try:
            _retire_if_parents_deleted(previous_id)
        except Exception:
            logger.exception(
                "recalculate_restored_result_recheck_failed",
                eval_logger_id=str(previous_id),
            )

    transaction.on_commit(check, using="default")


def _put_previous_back(journal: EvalRecalculation) -> bool:
    """Bring the previous result back once its replacement is retired.

    Only a terminal result comes back, only while its span, trace, session
    and eval config still exist (a delete meanwhile skipped it, being
    retired), and not if a newer entry holds the row's live slot (Delete &
    rerun, or the engine, wrote one meanwhile): otherwise its retirement is
    made final. Returns whether it is live again.

    Runs in the caller's transaction, the one that removes the
    ``EvalRecalculation`` row, so everything a put-back does is committed
    with it or retried with it."""
    try:
        # A savepoint: the unique live-slot index refuses the update when a
        # newer entry holds the slot, and the surrounding work must survive.
        with transaction.atomic(using="default"):
            restored = restore_retired(
                _still_there(
                    _rows().filter(pk=journal.previous_id, status__in=TERMINAL_STATUSES)
                )
            )
    except IntegrityError:
        restored = 0
    if not restored:
        _retire_for_good(journal.previous_id)
        logger.warning(
            "recalculate_previous_result_not_restored",
            eval_logger_id=str(journal.previous_id),
            eval_task_id=journal.eval_task_id,
        )
        return False
    # A failed run may have copied its answer over an Instant eval run's
    # answer for this row; copy the restored one back (in a savepoint of its
    # own; it never fails the put-back).
    from tracer.services.eval_tasks.entries import _record_instant_answer

    _record_instant_answer(journal.previous_id, journal.eval_task_id)
    _recheck_after_commit(journal.previous_id)
    return True


def _previous_is_live(previous_id) -> bool:
    return bool(_rows().filter(pk=previous_id, deleted=False).exists())


# What a finished recalculation did with the previous result.
RESTORED = "restored"
LEFT_RETIRED = "left_retired"
HANDED_TO_TASK = "handed_to_task"
KEPT = "kept"


def keep_replacement(entry: EvalLogger, previous: EvalLogger) -> None:
    """The replacement completed: the previous result is retired for good."""
    with transaction.atomic(using="default"):
        journal = _lock_journal(entry.pk)
        _retire_for_good(previous.pk)
        if journal is not None:
            journal.delete()


def abandon_recalculation(
    entry: EvalLogger, previous: EvalLogger, *, left_as: str | None
) -> str:
    """The request's run did not complete the replacement: finish it.

    ``left_as`` is the status this run left the replacement in: ``RUNNING``
    when the run raised, ``ERRORED`` or ``SKIPPED`` when its terminal write
    landed. ``None`` when the run found the replacement taken from it
    (``run_entry`` answered "reclaimed").

    * ``RESTORED``: the replacement, still as this run left it, is retired
      and the previous result is live again;
    * ``LEFT_RETIRED``: something else retired the replacement (the task was
      deleted or rerun), a newer entry holds the row's live slot, or the
      previous result's span, trace or eval config was deleted meanwhile;
      the previous result stays retired;
    * ``HANDED_TO_TASK``: the task's engine took the replacement over (its
      reaper requeued it, a pause requeued it, or Edit -> Save requeued the
      errored entry). It is left to the engine, and the
      ``EvalRecalculation`` row to the sweep, which finishes it once the
      engine's run has: retiring it here would discard the engine's work.
    """
    with transaction.atomic(using="default"):
        journal = _lock_journal(entry.pk)
        if journal is None:
            # The sweep or the reconciler finished this recalculation
            # meanwhile.
            return RESTORED if _previous_is_live(previous.pk) else LEFT_RETIRED
        retired = left_as is not None and soft_delete_eval_results(
            _rows().filter(pk=entry.pk, deleted=False, status=left_as, attempts=0)
        )
        if retired:
            outcome = RESTORED if _put_previous_back(journal) else LEFT_RETIRED
        elif _rows().filter(pk=entry.pk, deleted=False).exists():
            logger.info(
                "recalculate_replacement_handed_to_task",
                replacement_id=str(entry.pk),
                eval_task_id=str(previous.eval_task_id),
            )
            return HANDED_TO_TASK
        else:
            # Already retired by someone else: the task was deleted or rerun
            # while this ran. The previous result goes with it.
            _retire_for_good(journal.previous_id)
            logger.info(
                "recalculate_previous_result_left_retired",
                eval_logger_id=str(previous.pk),
                eval_task_id=str(previous.eval_task_id),
            )
            outcome = LEFT_RETIRED
        journal.delete()
    return outcome


def put_back_dropped_replacements(dropped_ids) -> int:
    """The reconciler is dropping these ``PENDING`` entries: retire each that
    is a Recalculate's replacement, and bring its previous result back.

    The reconciler drops a pending entry whose config left the task or whose
    row left the task's scope. That is not a Delete & rerun: it keeps such a
    row's finished result, so the result the replacement was standing in for
    comes back, as when the replacement fails. It stays retired only when its
    eval config, span, trace or session was deleted, or a newer live entry
    holds its slot (``_put_previous_back``).

    Runs in the reconciler's transaction. Only a replacement still
    ``PENDING`` is retired: one the drain claimed after the reconciler chose
    it is running, so it, its previous result and its ``EvalRecalculation``
    row are left as they are. A row another transaction holds (the request
    or the sweep finishing it) is skipped, not waited for: the reconciler may
    already hold the entry's lock, and they take the row's lock first.
    Returns how many of ``dropped_ids`` it retired."""
    dropped_ids = list(dropped_ids)
    if not dropped_ids:
        return 0
    journals = list(
        _journal()
        .select_for_update(skip_locked=True)
        .filter(replacement_id__in=dropped_ids)
        .order_by("pk")
    )
    retired = 0
    for journal in journals:
        dropped = soft_delete_eval_results(
            _rows().filter(
                pk=journal.replacement_id,
                deleted=False,
                status=EvalEntryStatus.PENDING,
            )
        )
        if not dropped:
            continue
        retired += dropped
        outcome = RESTORED if _put_previous_back(journal) else LEFT_RETIRED
        journal.delete()
        logger.info(
            "recalculate_replacement_dropped_by_reconcile",
            outcome=outcome,
            replacement_id=str(journal.replacement_id),
            eval_logger_id=str(journal.previous_id),
            eval_task_id=journal.eval_task_id,
        )
    return retired


def drop_pending_entries(entries) -> int:
    """Retire ``entries``, the reconciler's drop of pending work, putting back
    the result each dropped Recalculate replacement stood in for.

    Every reconciler drop path goes through here — the delta reconcile's
    out-of-scope drop and the sampling reconcile's policy-version, removed
    config and revalidation drops — so a requeued replacement dropped by any
    of them gets the same put-back rule (``put_back_dropped_replacements``)
    rather than finalizing the previous result's retirement. Runs in the
    caller's transaction. Returns how many entries it retired.

    Only the replacements (a handful: the open recalculations) are read into
    Python; the rest stays one filtered soft delete, so a task-wide drop
    does not become id lists of every entry."""
    # Primary: a lagging replica may not show a just-requeued replacement.
    entries = entries.using("default")
    journaled = list(
        _journal()
        .filter(replacement_id__in=entries.values("pk"))
        .values_list("replacement_id", flat=True)
    )
    retired = put_back_dropped_replacements(journaled) if journaled else 0
    # Replacements are finished by put_back_dropped_replacements alone, even
    # when it skips one (claimed, or its row held elsewhere): each statement
    # leaves out any entry that has an EvalRecalculation row. Still PENDING:
    # an entry the drain claimed since it was chosen runs on.
    rest = entries.filter(deleted=False, status=EvalEntryStatus.PENDING).exclude(
        Exists(_journal().filter(replacement_id=OuterRef("pk")))
    )
    return retired + soft_delete_eval_results(rest)


# -- results with no eval task ------------------------------------------------


def _slot_has_live_result(previous_id) -> bool:
    """Whether the span and config of ``previous_id`` hold a live result
    other than it (the recompute's, or a newer one)."""
    return bool(_other_live_results(previous_id).exists())


def _other_live_results(previous_id):
    slot = _rows().filter(pk=previous_id)
    return (
        _rows()
        .filter(
            observation_span_id__in=slot.values("observation_span_id"),
            custom_eval_config_id__in=slot.values("custom_eval_config_id"),
            deleted=False,
        )
        .exclude(pk=previous_id)
    )


def _put_untasked_previous_back(journal: EvalRecalculation) -> bool:
    """Bring a result with no task back, unless its span and config hold a
    live result again (no unique index guards that slot, so the condition is
    part of the update itself), or its span, trace or eval config was deleted
    while it was retired. Returns whether it is live again; when it is not,
    its retirement is made final."""
    restored = restore_retired(
        _still_there(
            _rows()
            .filter(pk=journal.previous_id)
            .filter(~Exists(_other_live_results(journal.previous_id)))
        )
    )
    if not restored:
        _retire_for_good(journal.previous_id)
    else:
        _recheck_after_commit(journal.previous_id)
    return bool(restored)


def begin_untasked_recalculation(previous: EvalLogger) -> EvalRecalculation:
    """Retire a result with no eval task while the per-span eval recomputes
    it, and record the recalculation, as one unit.

    The retirement is conditional on the result still being live: of two
    requests that both read it, the second is refused here, so they cannot
    both evaluate and leave two live results. It is refused too while
    another Recalculate of the span and config is open: a request that reads
    the first one's new result before that request closes would otherwise
    retire it, and the first would then find the slot empty and put the
    older result back. Returns the record that
    ``close_untasked_recalculation`` takes."""
    with transaction.atomic(using="default"):
        refuse_while_untasked_runs(
            previous.observation_span_id, previous.custom_eval_config_id
        )
        if not retire_pending_replacement(
            _rows().filter(pk=previous.pk, deleted=False)
        ):
            if _journal().filter(previous_id=previous.pk).exists():
                raise RecalculationRefused(ALREADY_RUNNING)
            raise RecalculationRefused(CHANGED_MEANWHILE)
        journal: EvalRecalculation = _journal().create(
            replacement=None,
            previous_id=previous.pk,
            eval_task_id=str(previous.eval_task_id or ""),
            started_at=timezone.now(),
        )
    return journal


def refuse_while_untasked_runs(span_id, config_id) -> None:
    """Raise ``RecalculationRefused`` when the span and config have no live
    result because a Recalculate of a result with no task has retired it and
    is still evaluating (or died, and the sweep has not finished it yet).

    A task result's recalculation leaves its ``RUNNING`` replacement live,
    so a second request finds that and is refused; with no task there is
    nothing live meanwhile, and the request would fail as if the span had no
    eval at all."""
    if (
        _journal()
        .filter(
            replacement__isnull=True,
            previous__observation_span_id=span_id,
            previous__custom_eval_config_id=config_id,
        )
        .exists()
    ):
        raise RecalculationRefused(ALREADY_RUNNING)


def _close_untasked(journal: EvalRecalculation) -> str:
    """Finish ``journal`` (locked by the caller) by what its slot holds."""
    if _slot_has_live_result(journal.previous_id):
        _retire_for_good(journal.previous_id)
        outcome = KEPT
    elif _put_untasked_previous_back(journal):
        outcome = RESTORED
    else:
        outcome = LEFT_RETIRED
    journal.delete()
    return outcome


def close_untasked_recalculation(journal: EvalRecalculation) -> str:
    """The request's per-span eval returned or raised: finish the
    recalculation by what the span and config hold now.

    * ``KEPT``: a live result is there (the recompute wrote it): the previous
      result is retired for good;
    * ``RESTORED``: none is: the previous result is live again;
    * ``LEFT_RETIRED``: the sweep finished it meanwhile and the previous
      result stayed retired.
    """
    with transaction.atomic(using="default"):
        locked = _journal().select_for_update().filter(pk=journal.pk).first()
        if locked is None:
            # The sweep finished it meanwhile: it took this request for dead
            # and put the previous result back, or kept a result it found.
            if not _previous_is_live(journal.previous_id):
                return (
                    KEPT if _slot_has_live_result(journal.previous_id) else LEFT_RETIRED
                )
            if not _slot_has_live_result(journal.previous_id):
                return RESTORED
            # It put the previous result back, and this request's result
            # landed after it: the newer one stays, never both.
            soft_delete_eval_results(_rows().filter(pk=journal.previous_id))
            return KEPT
        return _close_untasked(locked)


# -- the sweep ------------------------------------------------------------------


def _draining_task(task_id_ref):
    """Whether the task named by ``task_id_ref`` (its id as text) drains."""
    from tracer.models.eval_task import EvalTask
    from tracer.selectors.eval_tasks.stranded import sweepable_statuses

    return Exists(
        EvalTask.no_workspace_objects.filter(status__in=sweepable_statuses())
        .annotate(id_text=Cast("id", output_field=TextField()))
        .filter(id_text=task_id_ref)
        .order_by()
    )


def _engine_owned() -> Q:
    """A replacement the task's engine took over from the request."""
    return Q(replacement__status=EvalEntryStatus.PENDING) | Q(
        replacement__status=EvalEntryStatus.RUNNING, replacement__attempts__gt=0
    )


def _actionable(cutoff, answered_cutoff) -> Q:
    """The recalculations a sweep tick can finish now; never one it would
    only answer "waiting" for, so those cannot fill every tick's batch."""
    retired = Q(replacement__deleted=True)
    answered = (
        Q(started_at__lt=answered_cutoff)
        & Q(replacement__deleted=False)
        & Q(replacement__status__in=TERMINAL_STATUSES)
    )
    abandoned = (
        Q(started_at__lt=cutoff)
        & Q(replacement__deleted=False)
        & Q(replacement__status__in=_UNFINISHED)
        & Q(replacement__updated_at__lt=cutoff)
        & ~(_engine_owned() & Q(engine_drains=True))
    )
    untasked = Q(replacement__isnull=True) & (
        Q(started_at__lt=cutoff)
        | (Q(started_at__lt=answered_cutoff) & Q(slot_answered=True))
    )
    return retired | answered | abandoned | untasked


def recover_stale_recalculations(
    *,
    older_than_seconds: int = RECOVER_AFTER_SECONDS,
    answered_after_seconds: int = RECOVER_ANSWERED_AFTER_SECONDS,
    limit: int = RECOVER_BATCH,
) -> dict:
    """Finish recalculations whose request died. Counts only.

    For each ``EvalRecalculation``, by what its replacement shows (or, for a
    result with no task, by what its span and config hold):

    * ``kept``: it completed (the request died after the run, or the task's
      engine ran it), or the span and config hold a live result: the
      previous result is retired for good, as the request would have done.
      Once ``answered_after_seconds`` old;
    * ``restored``: it errored or was skipped (once ``answered_after_seconds``
      old), or is still unfinished ``older_than_seconds`` after its last
      stamp: ``RUNNING`` under the request's claim (the run died with the
      request), or taken over by the engine of a task that is no longer
      draining (paused, finished, failed). A result with no task whose span
      and config are still empty that long counts here too. It is retired
      and the previous result put back;
    * ``left_retired``: as ``restored``, but the previous result could not
      come back (a newer entry holds the row's slot, or its span, trace or
      eval config was deleted meanwhile), or something else already retired
      the replacement (Delete & rerun, the task deleted). The previous
      result's Error Feed memberships are retired;
    * ``waiting``: it changed while the tick read it; a later tick finishes
      it. A replacement the engine of a draining task holds, or one still
      within its bound, is not read at all;
    * ``busy``: the request is finishing it right now.
    """
    now = timezone.now()
    cutoff = now - timedelta(seconds=older_than_seconds)
    answered_cutoff = now - timedelta(seconds=answered_after_seconds)
    counts = {
        "kept": 0,
        "restored": 0,
        "left_retired": 0,
        "waiting": 0,
        "busy": 0,
        "errors": 0,
    }
    journal_ids = list(
        _journal()
        .annotate(
            engine_drains=_draining_task(OuterRef("eval_task_id")),
            slot_answered=Exists(
                EvalLogger._base_manager.filter(
                    observation_span_id=OuterRef("previous__observation_span_id"),
                    custom_eval_config_id=OuterRef("previous__custom_eval_config_id"),
                    deleted=False,
                ).exclude(pk=OuterRef("previous_id"))
            ),
        )
        .filter(_actionable(cutoff, answered_cutoff))
        .order_by("started_at")
        .values_list("pk", flat=True)[:limit]
    )
    for journal_id in journal_ids:
        try:
            outcome = _recover_one(journal_id, cutoff, answered_cutoff)
        except Exception:
            logger.exception("recalculate_recovery_failed", journal_id=journal_id)
            counts["errors"] += 1
            continue
        counts[outcome] += 1
    return counts


def _recover_one(journal_id, cutoff, answered_cutoff) -> str:
    with transaction.atomic(using="default"):
        journal = (
            _journal().select_for_update(skip_locked=True).filter(pk=journal_id).first()
        )
        if journal is None:
            return "busy"
        if journal.replacement_id is None:
            return _recover_untasked(journal, cutoff, answered_cutoff)
        replacement = _rows().filter(pk=journal.replacement_id).first()
        if replacement is None or replacement.deleted:
            _retire_for_good(journal.previous_id)
            outcome = "left_retired"
        elif replacement.status in TERMINAL_STATUSES and (
            journal.started_at >= answered_cutoff
        ):
            return "waiting"
        elif replacement.status == EvalEntryStatus.COMPLETED:
            _retire_for_good(journal.previous_id)
            outcome = "kept"
        elif replacement.status in _UNFINISHED and (
            replacement.updated_at >= cutoff
            or journal.started_at >= cutoff
            or (
                (
                    replacement.status == EvalEntryStatus.PENDING
                    or replacement.attempts > 0
                )
                and _task_is_draining(journal.eval_task_id)
            )
        ):
            return "waiting"
        else:
            # Fenced on what was just read: a run that claims, re-stamps or
            # finishes the replacement in between keeps it, and a later tick
            # decides.
            if not soft_delete_eval_results(
                _rows().filter(
                    pk=replacement.pk,
                    deleted=False,
                    status=replacement.status,
                    updated_at=replacement.updated_at,
                )
            ):
                return "waiting"
            outcome = "restored" if _put_previous_back(journal) else "left_retired"
        journal.delete()
    _log_recovered(journal, outcome)
    return outcome


def _recover_untasked(journal, cutoff, answered_cutoff) -> str:
    """A recalculation of a result with no task, its row locked."""
    if _slot_has_live_result(journal.previous_id):
        if journal.started_at >= answered_cutoff:
            return "waiting"
    elif journal.started_at >= cutoff:
        return "waiting"
    outcome = _close_untasked(journal)
    _log_recovered(journal, outcome)
    return outcome


def recheck_put_backs(
    *,
    window_seconds: int = PUT_BACK_RECHECK_SECONDS,
    limit: int = RECHECK_BATCH,
) -> dict:
    """Re-read the parents of recent put-backs. Counts only.

    For each ``EvalPutBack``, least recently read first (one not read since
    its put-back before any other), up to ``limit``: if the result is live and its
    span, trace, session or eval config is deleted now, retire it for good
    with its Error Feed memberships (``_retire_if_parents_deleted``). The row
    is removed once that retired the result, once the result is no longer
    live (a later Recalculate retired it, and writes its own row if it puts
    it back), or once it is older than ``window_seconds``. Until then each
    tick reads it again, so a delete that commits after the put-back's own
    checks is still caught, at the next tick that reaches it (the next one
    unless more than ``limit`` put-backs are watched: the batch rotates
    through them). A delete that commits after the window is not.

    * ``rechecked``: put-backs whose parents were read;
    * ``retired``: of those, results retired because a parent was deleted;
    * ``released``: rows removed;
    * ``busy``: rows another tick holds right now.
    """
    now = timezone.now()
    expired_before = now - timedelta(seconds=window_seconds)
    counts = {"rechecked": 0, "retired": 0, "released": 0, "busy": 0, "errors": 0}
    watch_ids = list(
        _put_backs()
        .order_by(F("checked_at").asc(nulls_first=True), "restored_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    for watch_id in watch_ids:
        try:
            with transaction.atomic(using="default"):
                watch = (
                    _put_backs()
                    .select_for_update(skip_locked=True)
                    .filter(pk=watch_id)
                    .first()
                )
                if watch is None:
                    counts["busy"] += 1
                    continue
                live = _previous_is_live(watch.result_id)
                retired = live and _retire_if_parents_deleted(watch.result_id)
                counts["rechecked"] += 1
                counts["retired"] += int(retired)
                if not live or retired or watch.restored_at < expired_before:
                    watch.delete()
                    counts["released"] += 1
                else:
                    watch.checked_at = now
                    watch.save(update_fields=["checked_at"])
        except Exception:
            logger.exception("recalculate_put_back_recheck_failed", watch_id=watch_id)
            counts["errors"] += 1
    return counts


def _log_recovered(journal, outcome) -> None:
    logger.info(
        "recalculate_recovered",
        outcome=outcome,
        replacement_id=str(journal.replacement_id),
        eval_logger_id=str(journal.previous_id),
        eval_task_id=journal.eval_task_id,
    )


__all__ = [
    "ALREADY_RUNNING",
    "HANDED_TO_TASK",
    "KEPT",
    "LEFT_RETIRED",
    "NEVER_FINISHED",
    "PUT_BACK_RECHECK_SECONDS",
    "RECOVER_AFTER_SECONDS",
    "RECOVER_ANSWERED_AFTER_SECONDS",
    "RESTORED",
    "RecalculationRefused",
    "STILL_EVALUATING",
    "TERMINAL_STATUSES",
    "abandon_recalculation",
    "begin_recalculation",
    "begin_untasked_recalculation",
    "close_untasked_recalculation",
    "keep_replacement",
    "put_back_dropped_replacements",
    "recheck_put_backs",
    "recover_stale_recalculations",
    "refuse_unfinished",
    "refuse_while_untasked_runs",
]
