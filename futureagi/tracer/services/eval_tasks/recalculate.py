"""Recalculate on an eval-task result, and its recovery when the request dies.

A result an eval task produced is an entry of that task's engine: at most one
live entry per (task, row, config). Recalculate replaces it in three steps:

1. ``begin_recalculation``: in one transaction, retire the previous result,
   insert a ``RUNNING`` replacement for the same row and config, and record
   both in ``EvalRecalculation``. Only a finished (terminal) result is
   replaced. A ``PENDING`` or ``RUNNING`` one belongs to a run in flight, the
   task's or another Recalculate's, and is refused without writing anything.
2. The caller runs the replacement in the request (``run_entry``).
3. ``keep_replacement`` or ``abandon_recalculation`` finishes it and removes
   the ``EvalRecalculation`` row in the same transaction.

If the process dies during step 2 (a deploy's SIGKILL, an OOM), the row is
left behind. ``recover_stale_recalculations`` (scheduled with the eval-task
sweep) finishes such a row once it is older than ``RECOVER_AFTER_SECONDS``,
with the same logic the request uses.

Error Feed invariant: an issue never keeps a trace whose eval no longer
exists. The previous result keeps its memberships while it may still come
back; they are retired in the same transaction that makes its retirement
final.
"""

from __future__ import annotations

from datetime import timedelta

import structlog
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from tfc.settings.runtime_setting_specs import LONGEST_RUNNING_ENTRY_SECONDS
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalRecalculation,
)
from tracer.services.eval_tasks.entries import (
    finish_retirement,
    restore_retired,
    retire_pending_replacement,
    soft_delete_eval_results,
)

logger = structlog.get_logger(__name__)

# The statuses a run leaves behind. Only these may be recalculated or put back.
TERMINAL_STATUSES = (
    EvalEntryStatus.COMPLETED,
    EvalEntryStatus.ERRORED,
    EvalEntryStatus.SKIPPED,
)

_UNFINISHED = (EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING)

# How old a recalculation must be before the sweep finishes it, and how long
# its replacement must have been ``RUNNING`` without a new stamp before the
# sweep treats that run as dead. It is the bound the engine's own blind reap
# uses (``reaper.MIN_STALE_RUNNING_SECONDS``): the longest an entry's run may
# legitimately last. That covers the request that started the run, which ends
# long before, and the engine, which may run the replacement itself after its
# reaper requeued it. A run that is still alive past it anyway cannot land a
# result: its writes are fenced on the replacement still being ``RUNNING``
# and live.
RECOVER_AFTER_SECONDS = LONGEST_RUNNING_ENTRY_SECONDS + 1

# How many recalculations one sweep tick finishes at most.
RECOVER_BATCH = 200

ALREADY_RUNNING = "A recalculation is already running for this result."
STILL_EVALUATING = (
    "This result is still being evaluated. Try again when it has finished."
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


def _refusal_for_unfinished(result_id) -> RecalculationRefused:
    if _journal().filter(replacement_id=result_id).exists():
        return RecalculationRefused(ALREADY_RUNNING)
    return RecalculationRefused(STILL_EVALUATING)


def refuse_unfinished(result: EvalLogger) -> None:
    """Raise ``RecalculationRefused`` unless ``result`` is terminal."""
    if result.status not in TERMINAL_STATUSES:
        raise _refusal_for_unfinished(result.pk)


def begin_recalculation(previous: EvalLogger) -> EvalLogger:
    """Retire ``previous`` and insert its ``RUNNING`` replacement, as one unit.

    The retirement is conditional on ``previous`` still being live and
    terminal, so a request that read it before a concurrent Recalculate (or
    the engine) took it over is refused here rather than replacing a run in
    flight. Returns the replacement.
    """
    selection = (
        previous.output_metadata.get("_task_selection")
        if isinstance(previous.output_metadata, dict)
        else None
    )
    now = timezone.now()
    with transaction.atomic(using="default"):
        retired = retire_pending_replacement(
            _rows().filter(pk=previous.pk, deleted=False, status__in=TERMINAL_STATUSES)
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
    current = _rows().filter(pk=previous_id).values("deleted", "status").first()
    if current is not None and not current["deleted"]:
        # Live but no longer terminal: the engine requeued it meanwhile.
        return _refusal_for_unfinished(previous_id)
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


def _put_previous_back(journal: EvalRecalculation) -> bool:
    """Bring the previous result back once its replacement is retired.

    Only a terminal result comes back, and not if a newer entry holds the
    row's live slot (Delete & rerun, or the engine, wrote one meanwhile): then
    its retirement is made final. Returns whether it is live again.

    Runs in the caller's transaction, the one that removes the
    ``EvalRecalculation`` row, so everything a put-back does is committed
    with it or retried with it."""
    try:
        # A savepoint: the unique live-slot index refuses the update when a
        # newer entry holds the slot, and the surrounding work must survive.
        with transaction.atomic(using="default"):
            restored = restore_retired(
                _rows().filter(pk=journal.previous_id, status__in=TERMINAL_STATUSES)
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
    return True


def _previous_is_live(previous_id) -> bool:
    return _rows().filter(pk=previous_id, deleted=False).exists()


# What ``abandon_recalculation`` did with the previous result.
RESTORED = "restored"
LEFT_RETIRED = "left_retired"
HANDED_TO_TASK = "handed_to_task"


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
      deleted or rerun), or a newer entry holds the row's live slot; the
      previous result stays retired;
    * ``HANDED_TO_TASK``: the task's engine took the replacement over (its
      reaper requeued it, a pause requeued it, or Edit -> Save requeued the
      errored entry). It is left to the engine, and the
      ``EvalRecalculation`` row to the sweep, which finishes it once the
      engine's run has: retiring it here would discard the engine's work.
    """
    with transaction.atomic(using="default"):
        journal = _lock_journal(entry.pk)
        if journal is None:
            # The sweep finished this recalculation meanwhile.
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


def recover_stale_recalculations(
    *,
    older_than_seconds: int = RECOVER_AFTER_SECONDS,
    limit: int = RECOVER_BATCH,
) -> dict:
    """Finish recalculations whose request died. Counts only.

    For each ``EvalRecalculation`` older than ``older_than_seconds`` (or of
    any age once its replacement was retired), by what its replacement
    shows:

    * ``kept``: it completed (the request died after the run, or the task's
      engine ran it): the previous result is retired for good, as the request
      would have done;
    * ``restored``: it errored or was skipped, or is still ``RUNNING`` with a
      stamp older than ``older_than_seconds`` (the run died with the
      request), or is ``PENDING`` that long in a task that is not draining
      (paused, finished, failed): it is retired and the previous result put
      back;
    * ``left_retired``: as ``restored``, but the previous result could not
      come back (a newer entry holds the row's slot), or something else
      already retired the replacement (Delete & rerun, the task deleted). The
      previous result's Error Feed memberships are retired;
    * ``waiting``: the task's engine holds it (``PENDING`` in a draining
      task), or it changed while the tick read it; a later tick finishes it.
      A replacement ``RUNNING`` under a recent stamp is not read at all;
    * ``busy``: the request is finishing it right now.
    """
    cutoff = timezone.now() - timedelta(seconds=older_than_seconds)
    counts = {
        "kept": 0,
        "restored": 0,
        "left_retired": 0,
        "waiting": 0,
        "busy": 0,
        "errors": 0,
    }
    # Only rows there is something to do for, so a replacement a run holds
    # cannot fill every tick's batch. A retired replacement needs no wait:
    # nothing can bring it back, and the previous result's memberships must
    # not outlive it.
    stale = Q(started_at__lt=cutoff) & (
        ~Q(replacement__status__in=_UNFINISHED) | Q(replacement__updated_at__lt=cutoff)
    )
    replacement_ids = list(
        _journal()
        .filter(Q(replacement__deleted=True) | stale)
        .order_by("started_at")
        .values_list("replacement_id", flat=True)[:limit]
    )
    for replacement_id in replacement_ids:
        try:
            outcome = _recover_one(replacement_id, cutoff)
        except Exception:
            logger.exception(
                "recalculate_recovery_failed", replacement_id=str(replacement_id)
            )
            counts["errors"] += 1
            continue
        counts[outcome] += 1
    return counts


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


def _recover_one(replacement_id, cutoff) -> str:
    with transaction.atomic(using="default"):
        journal = _lock_journal(replacement_id, skip_locked=True)
        if journal is None:
            return "busy"
        replacement = _rows().filter(pk=replacement_id).first()
        if replacement is None or replacement.deleted:
            _retire_for_good(journal.previous_id)
            outcome = "left_retired"
        elif replacement.status == EvalEntryStatus.COMPLETED:
            _retire_for_good(journal.previous_id)
            outcome = "kept"
        elif replacement.status in _UNFINISHED and (
            replacement.updated_at >= cutoff
            or (
                replacement.status == EvalEntryStatus.PENDING
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
    logger.info(
        "recalculate_recovered",
        outcome=outcome,
        replacement_id=str(replacement_id),
        eval_logger_id=str(journal.previous_id),
        eval_task_id=journal.eval_task_id,
    )
    return outcome


__all__ = [
    "ALREADY_RUNNING",
    "HANDED_TO_TASK",
    "LEFT_RETIRED",
    "RECOVER_AFTER_SECONDS",
    "RESTORED",
    "RecalculationRefused",
    "STILL_EVALUATING",
    "TERMINAL_STATUSES",
    "abandon_recalculation",
    "begin_recalculation",
    "keep_replacement",
    "recover_stale_recalculations",
    "refuse_unfinished",
]
