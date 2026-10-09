"""Scoring state of simulate calls: the stamps, the one clock and the one derivation.

A call's scoring is open while its eval side (``eval_completed``) or its CSAT
(``csat_status``) has not reached a terminal state. The run gate, the sweeper,
add-eval and the v3 calls API all read that state through this module, so they
apply one rule for whether a call is still being scored.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog
from django.db import transaction
from django.db.models import Exists, JSONField, OuterRef, Q, QuerySet
from django.db.models.expressions import RawSQL
from django.utils import timezone

from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.services.harness_run_evals import (
    EVAL_COMPLETED_KEY,
    EVAL_QUEUE_STAMP_SKEW,
    EVAL_QUEUED_KEY,
    parse_stamp,
)
from simulate.utils.processing_outcomes import build_skipped_eval_output_payload

logger = structlog.get_logger(__name__)

# A started job that makes no progress for this long is stuck.
SCORING_JOB_TIMEOUT = timedelta(minutes=10)
# A dispatched job that has not started after this long is stuck: long enough
# that a big run's queue wait is not mistaken for a dead worker.
SCORING_START_TIMEOUT = timedelta(minutes=30)
# The sweeper visits only runs still scoring or being stopped, of any age.
SWEEP_RUN_STATUSES = ("evaluating", "cancelling")
TRANSPORT_RUN_STATUSES = ("pending", "running")
CALL_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
SETTLE_FROM_STATUSES = ("pending", "running", "evaluating")
CSAT_OPEN_STATUSES = ("pending", "running")
SCORING_SWEEP_BATCH_SIZE = 200
SCORING_SWEEP_TIME_LIMIT_SECONDS = 300

EVAL_PROGRESS_KEY = "eval_progress_at"
CSAT_STAMP_KEY = "csat_stamped_at"
EVAL_DISPATCH_FAILED_KEY = "eval_dispatch_failed"

ScoringStatus = Literal["not_applicable", "pending", "succeeded", "failed", "timed_out"]
EvalStatus = Literal["pending", "succeeded", "failed", "timed_out", "skipped"]
CsatStatus = Literal[
    "not_applicable", "pending", "succeeded", "failed", "timed_out", "skipped"
]
SCORING_STATUSES = ("not_applicable", "pending", "succeeded", "failed", "timed_out")
EVAL_STATUSES = ("pending", "succeeded", "failed", "timed_out", "skipped")
CSAT_STATUSES = (
    "not_applicable",
    "pending",
    "succeeded",
    "failed",
    "timed_out",
    "skipped",
)

# Shown to people verbatim, so they are fixed strings and never interpolated.
REASON_EVAL_TIMED_OUT_RUNNING = "Scoring timed out: no progress for 10 minutes."
REASON_EVAL_TIMED_OUT_NOT_STARTED = (
    "Scoring timed out: no progress within 30 minutes of dispatch."
)
REASON_EVAL_EXPIRED = "Scoring did not finish."
REASON_EVAL_RUN_CANCELLED = "Not scored: the run was cancelled."
REASON_EVAL_NO_RESULT = "The evaluator returned no result."
REASON_CSAT_TIMED_OUT_RUNNING = "CSAT timed out: no result within 10 minutes."
REASON_CSAT_TIMED_OUT_NOT_STARTED = (
    "CSAT timed out: scoring did not start within 30 minutes."
)
REASON_CSAT_EXPIRED = "CSAT did not finish."
REASON_CSAT_NO_EVIDENCE = "Nothing to score: the call has no transcript or recording."
REASON_CSAT_RUN_CANCELLED = "Not scored: the run was cancelled."
REASON_CSAT_FAILED = "CSAT could not be scored."
REASON_CSAT_SKIPPED = "CSAT was not scored."
CSAT_SKIPPED_REASONS = frozenset({REASON_CSAT_NO_EVIDENCE, REASON_CSAT_RUN_CANCELLED})
CSAT_TIMED_OUT_REASONS = frozenset(
    {
        REASON_CSAT_TIMED_OUT_RUNNING,
        REASON_CSAT_TIMED_OUT_NOT_STARTED,
        REASON_CSAT_EXPIRED,
    }
)

# "Still scoring" for a completed call: its eval side is open or its CSAT is
# still pending or running. The run gate and the sweeper both read this.
CALL_SCORING_OPEN_Q = (
    Q(call_metadata__isnull=True)
    | Q(call_metadata__eval_completed__isnull=True)
    | Q(call_metadata__eval_completed=False)
    | Q(call_metadata__csat_status__in=CSAT_OPEN_STATUSES)
)


def locked_call_metadata_update(
    call_execution_id: Any, mutate: Callable[[dict[str, Any]], bool | None]
) -> dict[str, Any] | None:
    """Apply ``mutate`` to the call's stored metadata under a row lock.

    A caller's in-memory copy can be stale, and saving it whole would drop a
    stamp or CSAT state another worker wrote meanwhile. ``mutate`` returning
    ``False`` declines the write (a latch already closed). Returns the
    metadata as written, or ``None`` when declined.
    """
    with transaction.atomic():
        locked = (
            CallExecution.objects.select_for_update()
            .only("id", "call_metadata")
            .get(id=call_execution_id)
        )
        metadata = (
            dict(locked.call_metadata) if isinstance(locked.call_metadata, dict) else {}
        )
        if mutate(metadata) is False:
            return None
        locked.call_metadata = metadata
        locked.save(update_fields=["call_metadata"])
    return metadata


def pending_eval_ids(
    metadata: Mapping[str, Any] | None,
    eval_outputs: Mapping[str, Any] | None,
    fallback_ids: Iterable[Any] = (),
    live_ids: Iterable[Any] | None = None,
) -> set[str]:
    """The configs this call still expects a verdict for.

    A ``{"status": "pending"}`` placeholder, or a stamped config with no
    stored entry. A call with no stamps that started grading, or whose
    dispatch failed, and is not closed expects every ``fallback_ids`` config
    with no entry, so a failed unstamped dispatch reads as pending rather
    than as nothing to score. ``live_ids`` drops configs removed since they
    were stamped.
    """
    metadata = metadata if isinstance(metadata, Mapping) else {}
    outputs = eval_outputs if isinstance(eval_outputs, Mapping) else {}
    ids = {
        str(key)
        for key, entry in outputs.items()
        if isinstance(entry, Mapping)
        and str(entry.get("status") or "").strip().lower() == "pending"
    }
    stamps = metadata.get(EVAL_QUEUED_KEY)
    if isinstance(stamps, Mapping) and stamps:
        ids |= {
            str(key) for key in stamps if not isinstance(outputs.get(str(key)), Mapping)
        }
    elif (
        metadata.get("eval_started") is True or EVAL_DISPATCH_FAILED_KEY in metadata
    ) and metadata.get(EVAL_COMPLETED_KEY) is not True:
        ids |= {
            str(key)
            for key in fallback_ids
            if not isinstance(outputs.get(str(key)), Mapping)
        }
    if live_ids is None:
        return ids
    return ids & {str(key) for key in live_ids}


@dataclass(frozen=True)
class ScoringInput:
    """The scoring columns of one call, however they were read."""

    call_status: str
    metadata: Mapping[str, Any]  # SCORING_META_KEYS only
    eval_entries: Mapping[str, Mapping[str, Any]]
    csat_scored: bool
    has_csat_value: bool
    anchor: datetime  # completed_at, else ended_at, else created_at


@dataclass(frozen=True)
class Due:
    """What the clocks say about one call at one moment."""

    pending: frozenset[str]
    evals: Mapping[str, tuple[str, str, str]]  # id -> (status, reason, code)
    eval_side: bool  # the eval side may be closed
    csat: tuple[str, str | None, str] | None  # (stored status, error, code)


SCORING_META_KEYS = (
    "eval_started",
    EVAL_COMPLETED_KEY,
    EVAL_QUEUED_KEY,
    EVAL_PROGRESS_KEY,
    "csat_status",
    "csat_error",
    CSAT_STAMP_KEY,
    EVAL_DISPATCH_FAILED_KEY,
)


def normalize_eval_entry(entry: Mapping[str, Any]) -> EvalStatus:
    """One stored ``eval_outputs`` entry as an API eval status."""
    status = str(entry.get("status") or "").strip().lower()
    if status == "pending":
        return "pending"
    if status == "timed_out":
        return "timed_out"
    if status == "skipped" or entry.get("skipped") is True:
        return "skipped"
    if status in {"failed", "error"} or entry.get("error") in (True, "error"):
        return "failed"
    # "Completed", the harness's "completed", and xl rows that carry no status.
    return "succeeded"


def _eval_clock(
    queued: datetime, progress: datetime | None, now: datetime
) -> tuple[bool, str, str]:
    if progress is not None and progress >= queued:
        return (
            now >= progress + SCORING_JOB_TIMEOUT,
            REASON_EVAL_TIMED_OUT_RUNNING,
            "job_timeout",
        )
    return (
        now >= queued + SCORING_START_TIMEOUT,
        REASON_EVAL_TIMED_OUT_NOT_STARTED,
        "start_timeout",
    )


def _bounded(stamp: datetime | None, now: datetime) -> datetime | None:
    # A stamp far in the future would hold a call open for however far ahead
    # it claims to be, so it reads as absent.
    if stamp is None or stamp > now + EVAL_QUEUE_STAMP_SKEW:
        return None
    return stamp


def scoring_due(
    inp: ScoringInput, *, runnable_ids: Iterable[Any], cancelled: bool, now: datetime
) -> Due:
    """What the 10- and 30-minute clocks say about one finished call at ``now``.

    The only place those clocks live: the sweeper uses it to choose and write
    calls, the read side to show what the sweeper would write in runs it
    never visits, and add-eval to stop waiting on a job the clocks count as
    lost. Callers pass only calls that have left transport and ``analyzing``:
    for any other call the answer still carries clocks, and they mean nothing.
    """
    meta, outputs = inp.metadata, inp.eval_entries
    runnable_ids = {str(key) for key in runnable_ids}
    if inp.call_status == "completed":
        pending = pending_eval_ids(meta, outputs, runnable_ids, live_ids=runnable_ids)
    elif inp.call_status in ("failed", "cancelled"):
        # A call that never completed only expects the placeholders a rerun wrote.
        pending = {
            key
            for key, entry in outputs.items()
            if normalize_eval_entry(entry) == "pending"
        }
    else:
        pending = set()
    stamps = meta.get(EVAL_QUEUED_KEY)
    stamps = stamps if isinstance(stamps, Mapping) else {}
    progress = _bounded(parse_stamp(meta.get(EVAL_PROGRESS_KEY)), now)
    evals: dict[str, tuple[str, str, str]] = {}
    eval_side = True
    for key in sorted(pending):
        if cancelled:
            evals[key] = ("skipped", REASON_EVAL_RUN_CANCELLED, "run_cancelled")
            continue
        queued = _bounded(parse_stamp(stamps.get(key)), now) or inp.anchor
        due, reason, code = _eval_clock(queued, progress, now)
        if due:
            evals[key] = ("timed_out", reason, code)
        else:
            eval_side = False
    if not pending and not cancelled:
        # Open with nothing pending (a legacy call whose job never wrote):
        # close it once its newest dispatch is past the clock.
        newest = max(
            filter(None, (_bounded(parse_stamp(v), now) for v in stamps.values())),
            default=inp.anchor,
        )
        eval_side = _eval_clock(newest, progress, now)[0]
    csat: tuple[str, str | None, str] | None = None
    if meta.get("csat_status") in CSAT_OPEN_STATUSES:
        running = meta.get("csat_status") == "running"
        stamped = _bounded(parse_stamp(meta.get(CSAT_STAMP_KEY)), now) or inp.anchor
        limit = SCORING_JOB_TIMEOUT if running else SCORING_START_TIMEOUT
        if inp.csat_scored:
            # A stale whole-column save reopened a CSAT that already scored.
            csat = ("completed", None, "csat_scored")
        elif cancelled:
            csat = ("skipped", REASON_CSAT_RUN_CANCELLED, "run_cancelled")
        elif now >= stamped + limit:
            csat = (
                ("timed_out", REASON_CSAT_TIMED_OUT_RUNNING, "job_timeout")
                if running
                else ("timed_out", REASON_CSAT_TIMED_OUT_NOT_STARTED, "start_timeout")
            )
    return Due(frozenset(pending), evals, eval_side, csat)


def _scoring_meta(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    meta = {key: raw[key] for key in SCORING_META_KEYS if raw.get(key) is not None}
    if EVAL_DISPATCH_FAILED_KEY in raw:
        # Only its presence matters; its text is an exception message.
        meta[EVAL_DISPATCH_FAILED_KEY] = True
    return meta


def _eval_entries(raw: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): entry for key, entry in raw.items() if isinstance(entry, dict)}


def scoring_input_from_call(call: CallExecution) -> ScoringInput:
    metrics = call.conversation_metrics_data
    metrics = metrics if isinstance(metrics, dict) else {}
    csat_scored = metrics.get("csat_score") is not None
    return ScoringInput(
        call_status=str(call.status),
        metadata=_scoring_meta(call.call_metadata),
        eval_entries=_eval_entries(call.eval_outputs),
        csat_scored=csat_scored,
        has_csat_value=csat_scored or call.overall_score is not None,
        anchor=call.completed_at or call.ended_at or call.created_at,
    )


def mark_eval_progress(call_execution: CallExecution) -> None:
    """Record that the eval job on this call just started a step.

    The job clock times a call out 10 minutes after its last progress, so a
    job that is still working must refresh this before every long step. The
    task's in-memory copy is rebound so its later whole-column saves keep the
    stamp. Never raises: a missed stamp costs at most an early timeout that
    the job's own verdict then replaces.
    """
    try:
        stamped_at = timezone.now().isoformat()

        def _stamp(metadata: dict[str, Any]) -> None:
            metadata[EVAL_PROGRESS_KEY] = stamped_at

        written = locked_call_metadata_update(call_execution.id, _stamp)
        if written is not None:
            call_execution.call_metadata = written
    except Exception:  # noqa: BLE001 - a lost stamp must never stop grading
        logger.warning(
            "simulate_eval_progress_stamp_failed",
            call_execution_id=str(call_execution.id),
            exc_info=True,
        )


def settle_run(test_execution_id: Any) -> None:
    """Run the native settle for this run now; it alone decides the run is done."""
    from simulate.services.test_executor import TestExecutor

    TestExecutor(
        initialize_voice_service=False
    )._check_and_update_test_execution_completion(test_execution_id)


def settle_run_after_commit(test_execution_id: Any) -> None:
    """Run the native settle for this run once the caller's transaction commits.

    Transport writers only move a run into ``evaluating``; the settle alone
    decides it is done, and it must see the writer's own commit to do so.
    """
    transaction.on_commit(lambda: settle_run(test_execution_id), robust=True)


SWEEP_COUNT_KEYS = (
    "scanned",
    "due",
    "timed_out",
    "skipped_cancelled",
    "closed",
    "settled",
    "errors",
)


def needs_write(inp: ScoringInput, due: Due) -> bool:
    """Whether the sweeper has anything to write for this call now."""
    return bool(
        due.evals
        or due.csat
        or (inp.metadata.get(EVAL_COMPLETED_KEY) is not True and due.eval_side)
    )


def close_due_scoring(
    call: CallExecution, runnable_names: Mapping[str, str], now: datetime
) -> str | None:
    """Write what the clocks say for one locked, completed call.

    Returns the sweep count key for what was written, or ``None`` when the
    locked copy turned out to have nothing due.
    """
    due = scoring_due(
        scoring_input_from_call(call),
        runnable_ids=runnable_names.keys(),
        cancelled=call.test_execution.status == "cancelling",
        now=now,
    )
    meta = dict(call.call_metadata) if isinstance(call.call_metadata, dict) else {}
    outputs = dict(call.eval_outputs) if isinstance(call.eval_outputs, dict) else {}
    for eval_id, (status, reason, _code) in due.evals.items():
        stored = outputs.get(eval_id)
        name = (
            runnable_names.get(eval_id)
            or (stored.get("name") if isinstance(stored, dict) else None)
            or eval_id
        )
        if status == "skipped":
            entry = build_skipped_eval_output_payload(eval_name=name, reason=reason)
        else:
            entry = {
                "output": None,
                "reason": reason,
                "output_type": None,
                "name": name,
                "status": "timed_out",
            }
        outputs[eval_id] = {**entry, "timestamp": now.isoformat()}
    closed = meta.get(EVAL_COMPLETED_KEY) is not True and due.eval_side
    if closed:
        meta[EVAL_COMPLETED_KEY] = True
    if due.csat:
        csat_status, csat_error, _code = due.csat
        meta["csat_status"] = csat_status
        if csat_error is None:
            meta.pop("csat_error", None)
        else:
            meta["csat_error"] = csat_error
    if not (due.evals or due.csat or closed):
        return None
    call.call_metadata = meta
    call.eval_outputs = outputs
    # `updated_at` too, as every other scoring write does: the calls list is
    # ordered by it.
    call.save(update_fields=["call_metadata", "eval_outputs", "updated_at"])
    codes = {code for *_rest, code in due.evals.values()}
    if due.csat:
        codes.add(due.csat[2])
    _log_closed(call, due)
    if "run_cancelled" in codes:
        return "skipped_cancelled"
    if codes & {"job_timeout", "start_timeout"}:
        return "timed_out"
    return "closed"


def _log_closed(call: CallExecution, due: Due) -> None:
    ids = {
        "call_execution_id": str(call.id),
        "test_execution_id": str(call.test_execution_id),
    }
    eval_codes = {code for *_rest, code in due.evals.values()}
    if "run_cancelled" in eval_codes:
        logger.info(
            "simulate_scoring_skipped_cancelled",
            component="evals",
            eval_count=len(due.evals),
            **ids,
        )
    elif eval_codes:
        # One event per component: a started config outranks a queued one.
        reason_code = "job_timeout" if "job_timeout" in eval_codes else "start_timeout"
        logger.warning(
            "simulate_scoring_timed_out",
            component="evals",
            reason_code=reason_code,
            eval_count=len(due.evals),
            **ids,
        )
    csat_code = due.csat[2] if due.csat else None
    if csat_code == "run_cancelled":
        logger.info(
            "simulate_scoring_skipped_cancelled", component="csat", eval_count=0, **ids
        )
    elif csat_code in ("job_timeout", "start_timeout"):
        logger.warning(
            "simulate_scoring_timed_out",
            component="csat",
            reason_code=csat_code,
            eval_count=0,
            **ids,
        )


def settleable_run_ids() -> QuerySet:
    """Runs the native settle would move right now.

    The settle's own conditions as one query, so a settle that was missed
    (two calls closing in parallel, a swallowed error, a sweeper that died
    after its commit) is retried on the next sweep.
    """
    calls = CallExecution.objects.filter(
        test_execution_id=OuterRef("pk"), deleted=False
    )
    return (
        TestExecution.objects.filter(status__in=SWEEP_RUN_STATUSES)
        .exclude(Exists(calls.exclude(status__in=CALL_TERMINAL_STATUSES)))
        .exclude(Exists(calls.filter(CALL_SCORING_OPEN_Q, status="completed")))
        .filter(Q(status="cancelling") | Exists(calls.filter(status="completed")))
        .order_by("id")
        .values_list("id", flat=True)
    )


def runnable_eval_names(run_test_ids: Iterable[Any]) -> dict[Any, dict[str, str]]:
    """``{run_test_id: {config_id: name}}`` for the configs the evaluator can run."""
    names: dict[Any, dict[str, str]] = defaultdict(dict)
    for run_test_id, config_id, name, mapping in SimulateEvalConfig.objects.filter(
        run_test_id__in=list(run_test_ids), deleted=False
    ).values_list("run_test_id", "id", "name", "mapping"):
        if mapping:
            names[run_test_id][str(config_id)] = str(name or config_id)
    return names


def scoring_input_from_values(row: Mapping[str, Any]) -> ScoringInput:
    csat_scored = row["scoring_csat"] is not None
    return ScoringInput(
        call_status=str(row["status"]),
        metadata=_scoring_meta(row["scoring_meta"]),
        eval_entries=_eval_entries(row["scoring_outputs"]),
        csat_scored=csat_scored,
        has_csat_value=csat_scored or row["overall_score"] is not None,
        anchor=row["completed_at"] or row["ended_at"] or row["created_at"],
    )


def _column(name: str) -> str:
    return f'"{CallExecution._meta.db_table}"."{name}"'


_META_PAIRS = ", ".join(
    f"'{key}', {_column('call_metadata')} -> '{key}'"
    for key in SCORING_META_KEYS
    if key != EVAL_DISPATCH_FAILED_KEY
)
# The dispatch failure is projected as a flag so its exception text never
# leaves the database.
_SCORING_META_SQL = (
    f"jsonb_strip_nulls(jsonb_build_object({_META_PAIRS}, "
    f"'{EVAL_DISPATCH_FAILED_KEY}', CASE WHEN {_column('call_metadata')} ? "
    f"'{EVAL_DISPATCH_FAILED_KEY}' THEN 'true'::jsonb END))"
)
# Each entry reduced to the four keys the derivation reads. A non-object
# column or entry is skipped here rather than failing every read.
_SCORING_OUTPUTS_SQL = (
    f"CASE WHEN jsonb_typeof({_column('eval_outputs')}) = 'object' THEN ("
    "SELECT COALESCE(jsonb_object_agg(entry.key, jsonb_strip_nulls("
    "jsonb_build_object('status', entry.value -> 'status', "
    "'source', entry.value -> 'source', 'error', entry.value -> 'error', "
    "'skipped', entry.value -> 'skipped'))), '{}'::jsonb) "
    f"FROM jsonb_each({_column('eval_outputs')}) AS entry "
    "WHERE jsonb_typeof(entry.value) = 'object'"
    ") ELSE '{}'::jsonb END"
)
_SCORING_CSAT_SQL = f"{_column('conversation_metrics_data')} -> 'csat_score'"


def scoring_values(queryset: QuerySet, *extra_fields: str) -> QuerySet:
    """The reduced projection the counts and the sweeper read, one dict per call.

    Pairs with ``scoring_input_from_values``; ``extra_fields`` are added to
    each row as given.
    """
    return queryset.annotate(
        scoring_meta=RawSQL(_SCORING_META_SQL, (), output_field=JSONField()),
        scoring_outputs=RawSQL(_SCORING_OUTPUTS_SQL, (), output_field=JSONField()),
        scoring_csat=RawSQL(_SCORING_CSAT_SQL, (), output_field=JSONField()),
    ).values(
        "id",
        "status",
        "created_at",
        "completed_at",
        "ended_at",
        "overall_score",
        "scoring_meta",
        "scoring_outputs",
        "scoring_csat",
        *extra_fields,
    )


@dataclass(frozen=True)
class EvalScoring:
    status: EvalStatus
    reason: str | None  # None keeps the stored reason


@dataclass(frozen=True)
class CallScoring:
    status: ScoringStatus
    evals: Mapping[str, EvalScoring]
    missing: tuple[str, ...]  # expected evals with no stored entry, synthesized
    csat_status: CsatStatus
    csat_reason: str | None


TRANSPORT_OPEN_STATUSES = ("pending", "queued", "ongoing")
GRADING_STATUSES = (*TRANSPORT_OPEN_STATUSES, "analyzing")
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")
CANCELLED_RUN_STATUSES = ("cancelled", "cancelling")
_PRECEDENCE = ("pending", "timed_out", "failed", "succeeded")
_NO_DUE = Due(frozenset(), {}, False, None)


def derive_call_scoring(
    inp: ScoringInput,
    *,
    visible_ids: Iterable[Any],
    runnable_ids: Iterable[Any],
    run_status: str,
    now: datetime,
) -> CallScoring:
    """The one mapping from a call's stored scoring state to its API statuses.

    Never raises on data. A finished call reads ``pending`` only while its
    clock is not yet due, so no row stays pending forever, swept or not.
    """
    visible_ids = {str(key) for key in visible_ids}
    runnable_ids = {str(key) for key in runnable_ids}
    meta, outputs = inp.metadata, inp.eval_entries
    grading = inp.call_status in GRADING_STATUSES
    cancelled = run_status in CANCELLED_RUN_STATUSES
    stale = grading and run_status in TERMINAL_RUN_STATUSES
    closed = inp.call_status == "completed" and meta.get(EVAL_COMPLETED_KEY) is True
    due = (
        _NO_DUE
        if grading
        else scoring_due(inp, runnable_ids=runnable_ids, cancelled=cancelled, now=now)
    )
    pending = set(due.pending)
    if grading:
        pending = pending_eval_ids(meta, outputs, runnable_ids) | {
            key for key in runnable_ids if key not in outputs
        }

    def settle(eval_id: str, status: EvalStatus) -> EvalScoring:
        if status != "pending":
            return EvalScoring(status, None)
        if closed:
            return EvalScoring("timed_out", REASON_EVAL_EXPIRED)
        if eval_id in due.evals:
            status_due, reason, _code = due.evals[eval_id]
            return EvalScoring(status_due, reason)
        if cancelled:
            return EvalScoring("skipped", REASON_EVAL_RUN_CANCELLED)
        if stale:
            return EvalScoring("timed_out", REASON_EVAL_EXPIRED)
        return EvalScoring("pending", None)

    # The same liveness rule the rows use: live configs and harness checks.
    evals = {
        eval_id: settle(eval_id, normalize_eval_entry(entry))
        for eval_id, entry in outputs.items()
        if eval_id in visible_ids or entry.get("source") == "harness"
    }
    missing = tuple(
        sorted(key for key in pending if key not in outputs and key in visible_ids)
    )
    evals.update({eval_id: settle(eval_id, "pending") for eval_id in missing})
    csat_status, csat_reason = _derive_csat(
        inp, due, grading=grading, cancelled=cancelled, stale=stale
    )
    parts = ({entry.status for entry in evals.values()} | {csat_status}) - {
        "skipped",
        "not_applicable",
    }
    status = next((part for part in _PRECEDENCE if part in parts), "not_applicable")
    return CallScoring(status, evals, missing, csat_status, csat_reason)


def _derive_csat(
    inp: ScoringInput, due: Due, *, grading: bool, cancelled: bool, stale: bool
) -> tuple[str, str | None]:
    raw = inp.metadata.get("csat_status")
    # A non-string error (a list or dict from another writer) is unhashable,
    # and the set lookups below would 500 the whole calls page on it.
    error = str(inp.metadata.get("csat_error"))
    if inp.csat_scored or (raw is None and inp.has_csat_value):
        # A stored score beats a status a stale save reverted, and it is the
        # only signal on the native paths that never track a CSAT status.
        return ("succeeded", None)
    if raw in CSAT_OPEN_STATUSES or (raw is None and grading):
        if due.csat:
            return (due.csat[0], due.csat[1])
        if cancelled:
            return ("skipped", REASON_CSAT_RUN_CANCELLED)
        return ("timed_out", REASON_CSAT_EXPIRED) if stale else ("pending", None)
    if raw == "completed":
        return ("succeeded", None)
    if raw == "failed":
        # The stored error is exception text: it is never shown.
        return ("failed", REASON_CSAT_FAILED)
    if raw == "skipped":
        return (
            "skipped",
            error if error in CSAT_SKIPPED_REASONS else REASON_CSAT_SKIPPED,
        )
    if raw == "timed_out":
        return (
            "timed_out",
            error if error in CSAT_TIMED_OUT_REASONS else REASON_CSAT_EXPIRED,
        )
    return ("not_applicable", None)


def scoring_counts(
    rows: Iterable[Mapping[str, Any]],
    *,
    visible_ids: Iterable[Any],
    runnable_ids: Iterable[Any],
    run_status: str,
    now: datetime,
    derived: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """``summary.scoring``: how many of ``rows`` sit in each status.

    ``rows`` are ``scoring_values`` rows, one per call. ``derived`` maps a
    call id to the status its row in the same response shows; that status is
    counted instead, so a row and the counts never disagree even when ``rows``
    were read earlier.
    """
    visible_ids = {str(key) for key in visible_ids}
    runnable_ids = {str(key) for key in runnable_ids}
    derived = derived or {}
    counts = dict.fromkeys(SCORING_STATUSES, 0)
    for row in rows:
        status = derived.get(str(row["id"]))
        if status is None:
            status = derive_call_scoring(
                scoring_input_from_values(row),
                visible_ids=visible_ids,
                runnable_ids=runnable_ids,
                run_status=run_status,
                now=now,
            ).status
        counts[status] += 1
    return counts
