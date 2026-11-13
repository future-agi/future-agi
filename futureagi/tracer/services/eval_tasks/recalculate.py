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
    its retirement is made final. Returns whether it is live again."""
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
    return bool(restored)


def _previous_is_live(previous_id) -> bool:
    return _rows().filter(pk=previous_id, deleted=False).exists()


def _copy_instant_answer(previous_id, task_id) -> None:
    # A failed run may have copied its answer over an Instant eval run's
    # answer for this row; copy the restored one back.
    from tracer.services.eval_tasks.entries import _record_instant_answer

    _record_instant_answer(previous_id, task_id)


def keep_replacement(entry: EvalLogger, previous: EvalLogger) -> None:
    """The replacement completed: the previous result is retired for good."""
    with transaction.atomic(using="default"):
        journal = _lock_journal(entry.pk)
        _retire_for_good(previous.pk)
        if journal is not None:
            journal.delete()


def abandon_recalculation(entry: EvalLogger, previous: EvalLogger) -> bool:
    """The replacement did not complete: retire it and put ``previous`` back.

    Returns whether the previous result is live afterwards. It is not when
    the task was deleted or rerun while the replacement ran (that retired the
    replacement, and the previous result goes with it), or when a newer entry
    holds the row's live slot.
    """
    with transaction.atomic(using="default"):
        journal = _lock_journal(entry.pk)
        if journal is None:
            # The sweep finished this recalculation meanwhile.
            return _previous_is_live(previous.pk)
        replacement_retired = soft_delete_eval_results(
            _rows().filter(pk=entry.pk, deleted=False)
        )
        if replacement_retired:
            restored = _put_previous_back(journal)
        else:
            # Already retired by someone else: the task was deleted or rerun
            # while this ran. The previous result goes with it.
            _retire_for_good(journal.previous_id)
            logger.info(
                "recalculate_previous_result_left_retired",
                eval_logger_id=str(previous.pk),
                eval_task_id=str(previous.eval_task_id),
            )
            restored = False
        journal.delete()
    if restored:
        _copy_instant_answer(previous.pk, previous.eval_task_id)
    return restored


def recover_stale_recalculations(
    *,
    older_than_seconds: int = RECOVER_AFTER_SECONDS,
    limit: int = RECOVER_BATCH,
) -> dict:
    """Finish recalculations whose request died. Counts only.

    For each ``EvalRecalculation`` older than ``older_than_seconds``, by what
    its replacement shows:

    * ``kept``: it completed (the request died after the run): the previous
      result is retired for good, as the request would have done;
    * ``restored``: it errored or was skipped, or is still ``RUNNING`` with a
      stamp older than ``older_than_seconds`` (the run died with the
      request): it is retired and the previous result put back;
    * ``left_retired``: as ``restored``, but the previous result could not
      come back (a newer entry holds the row's slot), or something else
      already retired the replacement (Delete & rerun, the task deleted);
    * ``waiting``: it changed while the tick read it. A replacement that is
      ``PENDING``, or ``RUNNING`` under a recent stamp, is not read at all:
      the task's engine requeued it and runs it, so a later tick finishes it
      once that run has;
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
    # Only rows there is something to do for, so a replacement the engine
    # holds (``PENDING`` in a paused task, say) cannot fill every tick's batch.
    actionable = (
        Q(replacement__deleted=True)
        | ~Q(replacement__status__in=_UNFINISHED)
        | Q(
            replacement__status=EvalEntryStatus.RUNNING,
            replacement__updated_at__lt=cutoff,
        )
    )
    replacement_ids = list(
        _journal()
        .filter(actionable, started_at__lt=cutoff)
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


def _recover_one(replacement_id, cutoff) -> str:
    restored = False
    with transaction.atomic(using="default"):
        journal = _lock_journal(replacement_id, skip_locked=True)
        if journal is None:
            return "busy"
        replacement = _rows().filter(pk=replacement_id).first()
        if replacement is None or replacement.deleted:
            _retire_for_good(journal.previous_id)
            journal.delete()
            outcome = "left_retired"
        elif replacement.status == EvalEntryStatus.COMPLETED:
            _retire_for_good(journal.previous_id)
            journal.delete()
            outcome = "kept"
        elif replacement.status == EvalEntryStatus.PENDING or (
            replacement.status == EvalEntryStatus.RUNNING
            and replacement.updated_at >= cutoff
        ):
            return "waiting"
        else:
            # Fenced on what was just read: a run that re-stamps or finishes
            # the replacement in between keeps it, and a later tick decides.
            if not soft_delete_eval_results(
                _rows().filter(
                    pk=replacement.pk,
                    deleted=False,
                    status=replacement.status,
                    updated_at=replacement.updated_at,
                )
            ):
                return "waiting"
            restored = _put_previous_back(journal)
            journal.delete()
            outcome = "restored" if restored else "left_retired"
    logger.info(
        "recalculate_recovered",
        outcome=outcome,
        replacement_id=str(replacement_id),
        eval_logger_id=str(journal.previous_id),
        eval_task_id=journal.eval_task_id,
    )
    if restored:
        _copy_instant_answer(journal.previous_id, journal.eval_task_id)
    return outcome


__all__ = [
    "ALREADY_RUNNING",
    "RECOVER_AFTER_SECONDS",
    "RecalculationRefused",
    "STILL_EVALUATING",
    "TERMINAL_STATUSES",
    "abandon_recalculation",
    "begin_recalculation",
    "keep_replacement",
    "recover_stale_recalculations",
    "refuse_unfinished",
]
