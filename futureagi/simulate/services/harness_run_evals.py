"""Grading a finished run's calls with an eval that was just added to it.

The environment-level add binds an eval to the run test, which decides how
*future* calls are graded and deliberately touches nothing that already ran.
This module is the other half of the run-level add: it looks at the calls one
finished run already produced and queues one grading job per call that is
eligible for the newly bound eval.

A finished call is passed over when it already holds a verdict for this
eval's config (re-grading it would rewrite history nobody asked to rewrite),
when its own evaluations have not finished (``call_metadata.eval_completed``
is not true -- queueing before that finishes would trip
``alk_simulate_ingestion.py::_dispatch_evaluations_once``'s latch on
``eval_started`` and swallow the receipt's own dispatch), or when it was
stamped as queued for this eval inside the last ten minutes, so a second
click or a retried request queues nothing new.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import CallExecution, SimulateEvalConfig, TestExecution

# The one predicate for "this call already holds a sealed verdict for this
# config" (``simulate/utils/verdicts.py::has_stored_verdict``).
from simulate.utils.verdicts import has_stored_verdict

logger = structlog.get_logger(__name__)

# A call stamped for this config inside this window counts as already
# queued, so a second click minutes later queues nothing new for it. Written
# once, here; tests import this constant rather than repeating the literal.
EVAL_QUEUE_STAMP_WINDOW = timedelta(minutes=10)

# A stamp up to this far AHEAD of `now` still counts as queued -- ordinary
# clock skew between two web workers, not corruption; the failure this
# guards against is a double click dispatching a second job for the same
# call. A stamp further ahead than this reads as *not* queued instead,
# costing at most one ten-minute wait rather than locking the call out
# indefinitely.
EVAL_QUEUE_STAMP_SKEW = timedelta(seconds=60)

# Where the stamp lives: `call_metadata[EVAL_QUEUED_KEY][<config id>]` is an
# ISO-8601 timestamp, keyed by config id so queueing a second eval on the
# same call does not erase the first one's stamp.
EVAL_QUEUED_KEY = "eval_queued"

# Set by the eval pipeline once a call's evaluations have finished (see
# `TestExecutor._check_and_update_eval_completion` in
# `simulate/services/test_executor.py`, and the voice equivalents in
# `temporal/activities/xl.py` and `small.py`). Read as whoever set it: an
# absent, `False`, or otherwise non-`True` value all mean "not finished."
EVAL_COMPLETED_KEY = "eval_completed"


def _metadata(call_execution: CallExecution) -> dict[str, Any]:
    """This call's metadata as a dict, whatever the column actually holds.

    ``call_metadata`` defaults to ``{}`` but is a JSONB column several
    ingestion paths write, so a null or non-dict value is possible in old
    rows and must not raise here. The ``EVAL_QUEUED_KEY`` sub-dict is copied
    too -- and coerced to ``{}`` if it is not already a dict -- so a caller
    that writes ``result.setdefault(EVAL_QUEUED_KEY, {})[config_id] = ...``
    always finds a dict to write into, mutating only its own copy.
    """
    metadata = call_execution.call_metadata
    if not isinstance(metadata, dict):
        return {}
    result = dict(metadata)
    stamps = result.get(EVAL_QUEUED_KEY)
    result[EVAL_QUEUED_KEY] = dict(stamps) if isinstance(stamps, dict) else {}
    return result


def _queued_within_window(
    metadata: dict[str, Any], eval_config_id: str, *, now: datetime
) -> bool:
    """Whether this call was stamped for this config inside the window.

    An absent, non-dict, unparseable, or invalid stamp reads as "not queued"
    rather than raising -- including a well-formed but impossible date like
    ``"2026-02-30T00:00:00"``, where ``parse_datetime`` raises
    ``ValueError`` instead of returning ``None``. The config id is looked up
    as ``str(eval_config_id)``, matching how the stamp is written and
    cleared, so a raw ``UUID`` still hits it.

    The window is bounded on both sides:
    ``-EVAL_QUEUE_STAMP_SKEW <= now - stamped < window`` -- a stamp up to a
    minute ahead of ``now`` still reads as queued (clock skew), while a
    stamp further ahead than that reads as *not* queued rather than locking
    the call out for however far ahead it claims to be.
    """
    eval_config_id = str(eval_config_id)
    stamps = metadata.get(EVAL_QUEUED_KEY)
    if not isinstance(stamps, dict):
        return False
    try:
        stamped = parse_datetime(str(stamps.get(eval_config_id) or ""))
    except ValueError:
        return False
    if stamped is None:
        return False
    if timezone.is_naive(stamped):
        stamped = timezone.make_aware(stamped, timezone.get_default_timezone())
    elapsed = now - stamped
    return -EVAL_QUEUE_STAMP_SKEW <= elapsed < EVAL_QUEUE_STAMP_WINDOW


def _dispatch_one(call_execution_id: str, eval_config_id: str) -> None:
    """One grading job for one call, in the exact argument shape callers rely on::

        _run_simulate_evaluations_task.apply_async(
            args=(str(call_id),),
            kwargs={"eval_config_ids": [str(config_id)], "skip_existing": True},
        )

    Not the bulk task (``test_executor.py::run_new_evals_on_call_executions_task``),
    which hard-codes ``skip_existing: False``. ``skip_existing=True`` is what
    makes a second grading of the same call harmless -- the eval task skips a
    config that already holds a verdict.

    The import is local to avoid a cycle: ``test_executor`` is a large module
    that pulls in Temporal and most of ``simulate``.
    """
    from simulate.services.test_executor import _run_simulate_evaluations_task

    _run_simulate_evaluations_task.apply_async(
        args=(str(call_execution_id),),
        kwargs={
            "eval_config_ids": [str(eval_config_id)],
            "skip_existing": True,
        },
    )


def _clear_queued_stamps(call_execution_ids: list[str], eval_config_id: str) -> None:
    """Undo the stamp for every call in ``call_execution_ids``, in one transaction.

    Called once per failed dispatch batch, for every call whose job the batch
    did not confirm reached the broker -- one transaction and one row lock
    for the whole batch, not one per call. Best effort: a failure to unstamp
    is logged and swallowed rather than turned into a 500, since the
    request's bind and stamps are already committed.
    """
    eval_config_id = str(eval_config_id)
    if not call_execution_ids:
        return
    try:
        with transaction.atomic():
            # No `of=` needed: CallExecution has no workspace or organization
            # FK, so the manager adds no join to lock.
            calls = list(
                CallExecution.objects.select_for_update()
                .filter(id__in=call_execution_ids)
                .only("id", "call_metadata")
                .order_by("id")
            )
            now = timezone.now()
            to_update: list[CallExecution] = []
            for call_execution in calls:
                metadata = _metadata(call_execution)
                stamps = metadata.get(EVAL_QUEUED_KEY)
                if not isinstance(stamps, dict) or eval_config_id not in stamps:
                    continue
                stamps = {
                    key: value for key, value in stamps.items() if key != eval_config_id
                }
                metadata[EVAL_QUEUED_KEY] = stamps
                call_execution.call_metadata = metadata
                call_execution.updated_at = now
                to_update.append(call_execution)
            if to_update:
                CallExecution.objects.bulk_update(
                    to_update, ["call_metadata", "updated_at"]
                )
    except Exception:  # noqa: BLE001 - the count already excluded these calls
        logger.exception(
            "harness_run_eval_unstamp_batch_failed",
            call_execution_count=len(call_execution_ids),
            first_call_execution_id=str(call_execution_ids[0]),
            eval_config_id=eval_config_id,
        )


def _dispatch_batch_after_commit(
    call_execution_ids: list[str], eval_config_id: str, test_execution_id: str
) -> None:
    """The ``transaction.on_commit`` callback: dispatch the whole batch in
    order, stopping at the first hand-off failure.

    Runs only once the transaction that stamped every call in
    ``call_execution_ids`` has committed -- never before, and never while any
    row lock from that transaction is still held.

    One callback for the whole batch, not one per call, and the loop stops at
    the first failure instead of trying every remaining call: a broker that
    is down fails the same way for call two as for call one, so attempting
    the rest is just more blocking timeouts for an answer this function
    already knows. The calls this dispatch never reaches are unstamped
    together in one transaction (``_clear_queued_stamps``) instead of one
    per call, and exactly one exception is logged, with the count of calls it
    left unstamped.

    The caller's ``queued`` count was already fixed by the time this runs,
    and cannot be corrected here: this callback fires at the caller's
    commit, on the request's own thread, before the view builds its
    response -- but ``queued`` means "stamped and scheduled for dispatch",
    not "reached the broker", so every call in this batch stays counted in
    ``queued`` regardless of where dispatch stops -- the calls past the
    failure are logged and unstamped instead, so the next click is free to
    retry them.

    A dispatch that fails *after* the broker already accepted the message is
    indistinguishable here from one that never reached the broker at all --
    either way this clears the stamp, which can let a second click start a
    second grading job for a call that is already being graded. Accepted:
    the task's own ``skip_existing=True`` guard is what keeps two jobs that
    start together from producing two stored verdicts, so this function does
    not try to tell the two failure modes apart.
    """
    for index, call_execution_id in enumerate(call_execution_ids):
        try:
            _dispatch_one(call_execution_id, eval_config_id)
        except Exception as exc:  # noqa: BLE001 - stop the batch, not the request
            remaining = call_execution_ids[index:]
            logger.exception(
                "harness_run_eval_dispatch_failed",
                call_execution_id=call_execution_id,
                eval_config_id=eval_config_id,
                test_execution_id=test_execution_id,
                remaining_count=len(remaining),
                error=str(exc),
            )
            _clear_queued_stamps(remaining, eval_config_id)
            return


def queue_eval_for_finished_calls(
    test_execution: TestExecution, eval_config: SimulateEvalConfig
) -> dict[str, int]:
    """Queue one grading job per finished call of this run that is eligible.

    Returns the five counts::

        {queued, skipped_existing, skipped_in_flight, skipped_pending,
         completed_calls}

    ``completed_calls`` is every call of this run with status ``completed``,
    and the four others always partition it exactly: there is no shortfall.
    ``queued`` means "stamped and scheduled for dispatch", counted the moment
    a call is stamped, not once its grading job has actually reached the
    broker -- dispatch happens after commit (below). A dispatch that then
    fails clears its own stamp, and every stamp behind it in the same batch
    (``_dispatch_batch_after_commit``), but that failure is not, and cannot
    be, deducted from this number.

    Must be called from inside the caller's own ``transaction.atomic()``
    block, alongside whatever else has to succeed or fail with it. This
    function opens a ``transaction.atomic()`` of its own for the
    select-and-stamp step below, which becomes a savepoint nested inside the
    caller's transaction rather than a commit of its own, and every grading
    job is scheduled with ``transaction.on_commit``, which Django defers
    until the OUTERMOST transaction actually commits -- not until this
    function's own nested block exits. So no grading job can start against a
    stamp the caller's transaction could still roll back, and a later
    failure elsewhere in the same request rolls the stamp and the bind back
    together, before anything was ever dispatched. (Called with no enclosing
    transaction at all, this function's own block simply becomes the
    outermost one, and dispatch still waits for it to commit -- the ordering
    guarantee degrades safely rather than silently. The caller should still
    wrap this call: that is what ties the stamp to whatever else the request
    must not do halfway.)

    Two clicks against *this function*, a second apart, do not double-queue
    each other -- that is what the row lock below buys, and no more. The
    selection *and* the stamping happen inside one transaction that holds a
    row lock on every completed call of the run, so the second request
    blocks until the first commits and then reads the stamps the first
    wrote -- and counts every call as ``skipped_in_flight``. Locking in
    ``id`` order gives two concurrent requests the same lock order, so they
    queue behind each other instead of deadlocking. The lock does not
    protect the stamp from other whole-column writers of ``call_metadata``.

    Only the completed calls, and the two JSONB columns the loop actually
    reads (``call_metadata``, ``eval_outputs``), are fetched -- not the wide
    row ``CallExecution`` otherwise carries -- and every eligible call is
    stamped in one ``bulk_update`` rather than one ``UPDATE`` per row, so the
    lock is held for one SELECT and at most one UPDATE regardless of how many
    calls the run has. Dispatch still happens once per stamped call, each its
    own broker round-trip, after commit; there is no batching of that part,
    so for a run with many completed calls this is still N broker calls on
    the request path.

    This function's own nested ``transaction.atomic()`` cannot shorten how
    long the locks above are held: Postgres releases a row lock only when the
    OUTERMOST transaction ends, never when an inner savepoint's block exits,
    so when the endpoint wraps this call and ``add_selected_eval``'s bind
    together in one caller-level ``transaction.atomic()`` -- required so
    dispatch waits for both to be durable together -- both this function's
    ``CallExecution`` row locks and ``add_selected_eval``'s own ``RunTest``
    row lock are held for the combined span of the bind and this whole
    select-and-stamp, not for either function's own block alone.

    ``eval_config`` must be this run's own and must carry a non-empty
    ``mapping``; the endpoint checks the second itself so it can answer 400.
    The run must not be cancelled or cancelling: the worker skips those, so
    a stamp against one would be a promise nothing keeps. The endpoint
    refuses that case itself with a 409; this is the backstop for any other
    caller.
    """
    if eval_config.run_test_id != test_execution.run_test_id:
        raise ValueError(
            f"eval_config {eval_config.id} is bound to run test "
            f"{eval_config.run_test_id!r}, not test_execution "
            f"{test_execution.id}'s run test {test_execution.run_test_id!r}"
        )
    if not eval_config.mapping:
        raise ValueError(
            f"eval_config {eval_config.id} has an empty mapping -- it is a "
            "harness result column ingestion bound, not a selected eval, "
            "and cannot be queued for grading"
        )
    if test_execution.status in (
        TestExecution.ExecutionStatus.CANCELLED,
        TestExecution.ExecutionStatus.CANCELLING,
    ):
        raise ValueError(
            f"test_execution {test_execution.id} is {test_execution.status}; "
            "the eval worker does not grade a cancelled run, so nothing can "
            "be queued for it"
        )
    config_id = str(eval_config.id)
    counts = {
        "queued": 0,
        "skipped_existing": 0,
        "skipped_in_flight": 0,
        "skipped_pending": 0,
        "completed_calls": 0,
    }

    with transaction.atomic():
        finished_calls = list(
            CallExecution.objects.select_for_update(of=("self",))
            .filter(
                test_execution_id=test_execution.id,
                status=CallExecution.CallStatus.COMPLETED,
            )
            .only("id", "call_metadata", "eval_outputs", "status")
            .order_by("id")
        )
        # Captured only once the lock above is held -- the SELECT `list()`
        # triggers is what takes it -- so a request that waited on the lock
        # stamps with a `now` that reflects how much of the window is
        # actually left.
        now = timezone.now()
        to_stamp: list[CallExecution] = []
        for call_execution in finished_calls:
            counts["completed_calls"] += 1
            metadata = _metadata(call_execution)
            # 1. A stored verdict is never re-graded from here. The task
            #    would skip it again at run time, but counting it here is
            #    what lets the user see why nothing was queued. Uses the
            #    shared predicate, never a bare truthy read: a
            #    `{"status": "pending"}` placeholder, a `"skipped"` payload,
            #    and a `"Failed"` errored row are NOT verdicts, the eval task
            #    will re-grade them, and counting them as `skipped_existing`
            #    here would misstate what the pipeline actually does.
            if has_stored_verdict(call_execution, eval_config.id):
                counts["skipped_existing"] += 1
                continue
            # 2. The call's own evaluations have not finished, so no grading
            #    starts for it. `_run_simulate_evaluations` (test_executor.py)
            #    sets `eval_started` unconditionally when it runs; queueing
            #    here before that finishes would trip
            #    `alk_simulate_ingestion.py::_dispatch_evaluations_once`'s own
            #    latch on that flag and swallow the receipt's dispatch.
            if not metadata.get(EVAL_COMPLETED_KEY):
                counts["skipped_pending"] += 1
                continue
            # 3. Already queued minutes ago -- a double click, or a retry.
            if _queued_within_window(metadata, config_id, now=now):
                counts["skipped_in_flight"] += 1
                continue
            stamps = metadata.get(EVAL_QUEUED_KEY)
            stamps = dict(stamps) if isinstance(stamps, dict) else {}
            stamps[config_id] = now.isoformat()
            metadata[EVAL_QUEUED_KEY] = stamps
            call_execution.call_metadata = metadata
            # `bulk_update` does not run `auto_now`'s pre-save logic, so
            # `updated_at` is set by hand here, the same `now` every stamped
            # call in this batch shares. `_clear_queued_stamps` bumps it the
            # same way, so both sides of the stamp/unstamp pair stay
            # consistent about touching this column.
            call_execution.updated_at = now
            to_stamp.append(call_execution)

        # One UPDATE for every eligible call, not one per row: the lock above
        # is held for this single statement, not N of them.
        if to_stamp:
            CallExecution.objects.bulk_update(to_stamp, ["call_metadata", "updated_at"])

        counts["queued"] = len(to_stamp)

        # Scheduled here, while the stamp is still inside this transaction,
        # but not RUN here: `transaction.on_commit` defers this until the
        # outermost transaction actually commits, so no grading job can
        # start against a stamp that transaction could still roll back. One
        # callback for the whole batch, not one per call.
        if to_stamp:
            # `robust=True` so a raise here cannot drop other callbacks
            # queued behind it in the same commit. A closure, not
            # `functools.partial`: Django's robust handler formats its log
            # line with `func.__qualname__`, which a partial does not have.
            stamped_ids = [str(call_execution.id) for call_execution in to_stamp]
            execution_id = str(test_execution.id)

            def _dispatch_this_batch():
                _dispatch_batch_after_commit(stamped_ids, config_id, execution_id)

            transaction.on_commit(_dispatch_this_batch, robust=True)

    logger.info(
        "harness_run_eval_queued",
        test_execution_id=str(test_execution.id),
        eval_config_id=config_id,
        **counts,
    )
    return counts
