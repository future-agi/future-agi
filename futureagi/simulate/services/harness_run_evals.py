"""Grading a finished run's calls with an eval that was just added to it.

The environment-level add binds an eval to the run test, which decides how
*future* calls are graded and deliberately touches nothing that already ran
(frontend contract P11). This module is the other half of the run-level add: it
looks at the calls one finished run already produced and queues one grading job
per call that is eligible for the newly bound eval.

Three reasons a finished call is passed over, in the order the diagram draws
them (``lld-4-add-from-run.puml``, internal-docs repo -- see the citation
below):

* it already holds a verdict for this eval's config -- re-grading it would
  rewrite history nobody asked to rewrite (contract P20, F2);
* its own evaluations have not finished (``call_metadata.eval_completed`` is
  not true) -- no endpoint here starts a grading for such a call (contract
  F3). ``TestExecutor._run_simulate_evaluations``
  (``simulate/services/test_executor.py``) SETS ``eval_started``
  unconditionally when it runs; it is the receipt path,
  ``alk_simulate_ingestion.py::_dispatch_evaluations_once``, that LATCHES on
  that flag -- sees it already set and returns without dispatching its own
  evals. Queueing here before the call's own evaluations finish would trip
  that latch and swallow the receipt's dispatch (design §6);
* it was stamped as queued for this eval inside the last ten minutes -- so a
  second click, or a retried request, queues nothing new (contract P22).

Contract: ``api_contracts/harness/eval-offer-backend-frontend.md`` v1.9 §6
(P18a-P22) and F3 -- this checkout, ``api_contracts/``.

Design and diagrams: internal-docs repo (not this checkout --
``~/Desktop/growth/internal-docs/rl-environment/add-evals/``):
``design.md`` v1.5 §6; ``diagrams/lld-uml/lld-4-add-from-run.puml`` upper
half; ``diagrams/arch/workspace.dsl`` component ``runAdd``.
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
# config" (TH-8045, ``simulate/utils/verdicts.py::has_stored_verdict``).
# Imported at module scope like its other two callers (the import in
# ``simulate/services/test_executor.py`` and in
# ``simulate/temporal/activities/xl.py``); the module is deliberately free of
# any model-layer import, so this costs nothing.
from simulate.utils.verdicts import has_stored_verdict

logger = structlog.get_logger(__name__)

# A call stamped for this config inside this window counts as already queued,
# so a second click minutes later queues nothing new for it (contract P22).
# Ten minutes is the contract's number and it is written exactly once, here;
# the tests import this constant rather than repeating the literal.
EVAL_QUEUE_STAMP_WINDOW = timedelta(minutes=10)

# A stamp up to this far AHEAD of `now` still counts as queued. Two web
# workers' clocks are never perfectly in sync -- ordinary NTP drift, or a
# container host waking from a suspend -- so a stamp a couple of seconds
# ahead of the reading worker's own `now` is not corruption, it is the
# ordinary clock skew between two machines, and the safe reading is still
# "queued": the failure this guards against is a double click dispatching a
# second grading job for the same call. A stamp further ahead than this --
# say, a garbage year-2999 value -- is not skew, it is a corrupt stamp, and
# it reads as *not* queued instead, so it costs at most one ten-minute wait
# rather than locking the call out for however far ahead it claims to be.
EVAL_QUEUE_STAMP_SKEW = timedelta(seconds=60)

# Where the stamp lives: ``call_metadata[EVAL_QUEUED_KEY][<config id>]`` is an
# ISO-8601 timestamp. A dict keyed by config id, not a single timestamp, so
# queueing a second eval on the same call does not erase the first one's stamp.
EVAL_QUEUED_KEY = "eval_queued"

# Set by the eval pipeline once a call's evaluations have finished. The writer
# that matters for this endpoint is
# ``TestExecutor._check_and_update_eval_completion``
# (``simulate/services/test_executor.py``), on both its "no eval config
# expected at all" arm and its "every expected config now holds a
# non-pending row" arm -- and ``TestExecutor._run_simulate_evaluations`` and
# ``TestExecutor._mark_processing_skipped_for_eval_rerun`` set it directly on
# their own completion arms, in the same module. The voice side writes it in
# ``temporal/activities/xl.py``'s ``_check_eval_completion`` and in the
# skipped payload built in ``temporal/activities/small.py``.
#
# ``alk_simulate_ingestion.py::ingest_alk_sim_result`` also writes it, but
# ONLY on the ``"harness_evaluations" in call_metadata and not
# selected_eval_config_ids`` branch -- a receipt that carries its own results
# for a call with no platform eval configs selected. A call that HAS selected
# configs -- which is every call this endpoint is aimed at -- takes the
# ``else`` arm (calls ``_dispatch_evaluations_once``) and gets the flag from
# ``_check_and_update_eval_completion`` instead.
#
# This rule reads the flag whoever set it: when it is not true -- missing,
# ``False``, or any other non-``True`` value -- the call's evaluations have
# not finished (contract P19's own wording), and contract F3 forbids starting
# a grading for it.
EVAL_COMPLETED_KEY = "eval_completed"


def _metadata(call_execution: CallExecution) -> dict[str, Any]:
    """This call's metadata as a dict, whatever the column actually holds.

    ``call_metadata`` defaults to ``{}`` but is a JSONB column several
    ingestion paths write, so a null or a non-dict is possible in old rows and
    must not raise here.

    The ``EVAL_QUEUED_KEY`` sub-dict is copied too; every other nested value
    is still shared with the live ``call_execution.call_metadata``. A caller
    that writes the natural way --
    ``result.setdefault(EVAL_QUEUED_KEY, {})[config_id] = ...`` -- mutates
    only its own copy, never the model instance's own metadata before (or
    regardless of whether) a save happens. This holds even when the column
    already carries a non-dict ``eval_queued`` (a string, a list, ``None``
    from an older ingestion path): that value is coerced to ``{}`` here
    rather than passed through, so ``setdefault`` always finds a dict to
    write into and never raises.
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

    An absent, non-dict, unparseable, or otherwise invalid stamp reads as
    "not queued" -- never raises. This includes a stamp that is well-formed
    but not a real date/time (``"2026-02-30T00:00:00"``): ``parse_datetime``
    raises ``ValueError`` for that shape rather than returning ``None``, and
    that ``ValueError`` is caught here too. The stamp is an optimisation
    against a double click, and a corrupt one must not be able to make an
    eval permanently unqueueable, or raise and 500 the whole run-level add.

    The key is looked up as ``str(eval_config_id)``, the same shape the
    stamp is written and cleared under, so a caller that passes a ``UUID``
    (as ``SimulateEvalConfig.id`` is) still hits the stamp.

    The window is bounded on both sides: ``-EVAL_QUEUE_STAMP_SKEW <= now -
    stamped < window``. A stamp up to a minute *ahead* of ``now`` (clock skew
    between two web workers) still reads as queued, which is the
    conservative direction -- the failure it prevents is double-grading. A
    stamp further ahead than that, or an absurd one
    (``"2999-01-01T00:00:00+00:00"``), reads as *not* queued rather than
    blocking the call for however far ahead it claims to be, so a corrupt
    stamp costs at most one ten-minute wait either way.
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
    """One grading job for one call, in the exact shape the contract pins.

    Contract P18a, verbatim::

        _run_simulate_evaluations_task.apply_async(
            args=(str(call_id),),
            kwargs={"eval_config_ids": [str(config_id)], "skip_existing": True},
        )

    This is the argument shape the bulk task builds today
    (``test_executor.py::run_new_evals_on_call_executions_task``) with the
    flag flipped; the bulk task itself is not used, because it hard-codes
    ``skip_existing: False``. The flag is what makes a second grading
    harmless: TH-8045 made the eval task skip a config that already holds a
    verdict, in the per-eval loop and in the two branches that write a
    "skipped" payload before it.

    The import is inside the function on purpose: ``test_executor`` is a
    very large module that pulls in Temporal and most of ``simulate``, and
    importing it at module scope from a service this small is a cycle
    waiting to happen. (Patching
    ``simulate.services.test_executor._run_simulate_evaluations_task.apply_async``
    patches an attribute on the task object itself, which a module-scope
    import would see exactly as well as this function-local one does -- so
    cycle avoidance above is the only reason this import is local, not
    something the tests require.)
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
    """Undo the stamp for every call in ``call_execution_ids``, in ONE transaction.

    Called once per failed batch, for every call whose job the batch did not
    confirm reached the broker, rather than one transaction and one row lock
    per call. A stamp that outlived a dispatch that never happened would make
    the next ten minutes of retries report the call as already queued while
    nothing at all is grading it -- exactly the state the stamp exists to
    describe honestly, paid for once here, not N times, which is the whole
    point of batching it (see ``_dispatch_batch_after_commit``).

    Best effort: the count already excluded these calls, so a failure to
    unstamp is logged and swallowed rather than turned into a 500 for a
    request whose bind and stamps are already committed.
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
    """The ``transaction.on_commit`` callback: dispatch the whole batch, in
    order, and stop at the first hand-off failure.

    Runs only once the transaction that stamped every call in
    ``call_execution_ids`` has actually committed (TH-8046) -- never before,
    and never while any row lock from that transaction is still held.

    One callback for the whole batch, not one per call, and the loop STOPS
    at the first failure instead of trying every remaining call: a broker
    that is down or unreachable fails the same way for call two as it did
    for call one, so attempting calls three through N is N-2 more blocking
    timeouts for an answer this function already knows. The calls this
    dispatch never reaches -- the one that raised, and everything still
    queued behind it -- are unstamped together in ONE transaction
    (``_clear_queued_stamps``) instead of one transaction and one row lock
    per call, and exactly one exception is logged, with the count of calls
    it left unstamped: a dead broker costs this request one timeout and one
    unstamping transaction, not N of each.

    The caller's ``queued`` count was already fixed by the time this runs,
    and cannot be corrected here: this callback fires at the caller's
    commit, on the request's own thread, inside ``Atomic.__exit__`` and
    before the view builds its response -- but ``queued`` means "stamped
    and scheduled for dispatch", not "reached the broker", so every call
    in this batch stays counted in ``queued`` regardless of where dispatch
    stops -- the calls past the failure are logged and unstamped instead,
    so the next click is free to retry them.

    A dispatch that fails *after* the broker already accepted the message
    (a result-backend error, a serializer error on the return path) is
    indistinguishable here from one that never reached the broker at all --
    either way this clears the stamp, which can let a second click start a
    second grading job for a call that is already being graded. Accepted:
    the task's own ``skip_existing=True`` guard (TH-8045) is what keeps two
    jobs that start together from producing two stored verdicts, so this
    function does not try to tell the two failure modes apart.
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

    Returns the five counts contract P19 defines::

        {queued, skipped_existing, skipped_in_flight, skipped_pending,
         completed_calls}

    ``completed_calls`` is every call of this run with status ``completed`` --
    the finished calls this endpoint looked at -- and the four others always
    partition it exactly: there is no shortfall. ``queued`` means "stamped and
    scheduled for dispatch", counted the moment a call is stamped, not once
    its grading job has actually reached the broker -- dispatch happens after
    commit (below), which is at the exit of the caller's ``atomic()`` block,
    on the request's own thread and before the response is built. The count
    is nonetheless final by then, by choice: ``queued`` describes what was
    stamped, not what reached the broker. A dispatch that then fails clears
    its own stamp, and every stamp behind it in the same batch, and logs once
    (``_dispatch_batch_after_commit``), but that failure is not, and cannot
    be, deducted from this number.

    Must be called from inside the caller's own ``transaction.atomic()``
    block, alongside whatever else has to succeed or fail with it -- for this
    ticket, the endpoint's bind via ``add_selected_eval``. This function
    opens a ``transaction.atomic()`` of its own for the select-and-stamp step
    below, which becomes a savepoint nested inside the caller's transaction
    rather than a commit of its own, and every grading job is scheduled with
    ``transaction.on_commit``, which Django defers until the OUTERMOST
    transaction actually commits -- not until this function's own nested
    block exits. So no grading job can start against a stamp the caller's
    transaction could still roll back, and a later failure elsewhere in the
    same request rolls the stamp and the bind back together, before anything
    was ever dispatched. (Called with no enclosing transaction at all, this
    function's own block simply becomes the outermost one, and dispatch still
    waits for it to commit -- the ordering guarantee degrades safely rather
    than silently. The caller should still wrap this call: that is what ties
    the stamp to whatever else the request must not do halfway.)

    Two clicks against *this function*, a second apart, do not double-queue
    each other -- that is what the row lock below buys, and no more. The
    selection *and* the stamping happen inside one transaction that holds a
    row lock on every completed call of the run, so the second request
    blocks until the first commits and then reads the stamps the first wrote
    -- and counts every call as ``skipped_in_flight``. Locking in ``id``
    order gives two concurrent requests the same lock order, so they queue
    behind each other instead of deadlocking. The lock does not protect the
    stamp from other whole-column writers of ``call_metadata`` -- contract
    F2's accepted TH-8051 gap.

    Only the completed calls, and the two JSONB columns the loop actually
    reads (``call_metadata``, ``eval_outputs``), are fetched -- not the wide
    row ``CallExecution`` otherwise carries (provider payloads, transcripts,
    analysis data, …) -- and every eligible call is stamped in one
    ``bulk_update`` rather than one ``UPDATE`` per row, so the lock is held
    for one SELECT and at most one UPDATE regardless of how many calls the
    run has. Dispatch still happens once per stamped call, each its own
    broker round-trip, after commit -- there is no batching of that part, and
    for a run with many completed calls this is still N broker calls on the
    request path; nothing in this ticket bounds N (a known limit -- see the
    PR body).

    This function's own nested ``transaction.atomic()`` cannot shorten how
    long the locks above are held: Postgres releases a row lock only when the
    OUTERMOST transaction ends, never when an inner savepoint's block exits,
    so when the endpoint (Task 4) wraps this call and ``add_selected_eval``'s
    bind together in one caller-level ``transaction.atomic()`` -- required so
    dispatch waits for both to be durable together -- both this function's
    ``CallExecution`` row locks and ``add_selected_eval``'s own ``RunTest``
    row lock are held for the combined span of the bind and this whole
    select-and-stamp, not for either function's own block alone. That is a
    real latency change from calling the two separately (see the PR body's
    concurrency note); nothing about how this function structures its own
    ``atomic()`` block can reduce it without giving up the atomicity the
    caller depends on.

    ``eval_config`` must be this run's own and must carry a non-empty
    ``mapping``; the endpoint checks the second itself so it can answer 400
    (contract P18b). Both are backstops for any other caller.

    Contract P19, P20, P22, F3; design §6.2-§6.4; ``lld-4-add-from-run.puml``
    loop block.
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
        # Captured only once the lock above is actually held -- the SELECT
        # `list()` triggers is what takes it -- not before: a request that
        # waited on the lock must stamp with a `now` that reflects how much
        # of the ten-minute window is actually left, not the moment it first
        # tried to read it.
        now = timezone.now()
        to_stamp: list[CallExecution] = []
        for call_execution in finished_calls:
            counts["completed_calls"] += 1
            metadata = _metadata(call_execution)
            # 1. A stored verdict is never re-graded from here (P20). The task
            #    would skip it again at run time (TH-8045), but counting it
            #    here is what lets the user see why nothing was queued.
            #    Through TH-8045's shared predicate, never a bare truthy read:
            #    a `{"status": "pending"}` placeholder, a `"skipped"` payload and
            #    a `"Failed"` errored row are NOT verdicts, the eval task will
            #    re-grade them, and counting them as `skipped_existing` here
            #    would make this endpoint's counts describe something the
            #    pipeline does not do (TH-8048).
            if has_stored_verdict(call_execution, eval_config.id):
                counts["skipped_existing"] += 1
                continue
            # 2. The call's own evaluations have not finished, so no grading
            #    starts for it (F3). `_run_simulate_evaluations`
            #    (test_executor.py) SETS `eval_started` unconditionally when
            #    it runs; queueing here before that finishes would trip
            #    `alk_simulate_ingestion.py::_dispatch_evaluations_once`'s own
            #    latch on that flag and swallow the receipt's dispatch.
            if not metadata.get(EVAL_COMPLETED_KEY):
                counts["skipped_pending"] += 1
                continue
            # 3. Already queued minutes ago -- a double click, or a retry (P22).
            if _queued_within_window(metadata, config_id, now=now):
                counts["skipped_in_flight"] += 1
                continue
            stamps = metadata.get(EVAL_QUEUED_KEY)
            stamps = dict(stamps) if isinstance(stamps, dict) else {}
            stamps[config_id] = now.isoformat()
            metadata[EVAL_QUEUED_KEY] = stamps
            call_execution.call_metadata = metadata
            # `bulk_update` does not run `auto_now`'s pre-save logic, so
            # `updated_at` is set by hand here -- the same `now` every stamped
            # call in this batch shares -- rather than left unmoved. The undo
            # path (`_clear_queued_stamps`, the batch clear this function's
            # own dispatch failure runs) already bumps it the same way; a
            # successful stamp doing the same keeps both sides of the
            # stamp/unstamp pair consistent about touching this column.
            call_execution.updated_at = now
            to_stamp.append(call_execution)

        # One UPDATE for every eligible call, not one per row: the lock above
        # is held for this single statement, not N of them.
        if to_stamp:
            CallExecution.objects.bulk_update(to_stamp, ["call_metadata", "updated_at"])

        counts["queued"] = len(to_stamp)

        # Scheduled here, while the stamp is still inside this transaction,
        # but not RUN here: `transaction.on_commit` defers this until the
        # outermost transaction actually commits -- the caller's, when this
        # function is called the way its docstring requires -- so no grading
        # job can start against a stamp that transaction could still roll
        # back. ONE callback for the whole batch, not one per call: a dead
        # broker then costs this request one timeout, not N -- see
        # `_dispatch_batch_after_commit`'s docstring.
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
