"""A call's kept worker record: reading the app's spans after the call, keeping each read, and serving what was kept."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import structlog
from django.utils import timezone

from simulate.models import (
    CallExecution,
    CallTranscript,
    HostedHarnessAttempt,
    HostedHarnessExecution,
    HostedHarnessJob,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import CallExecutionSnapshot
from simulate.services.harness_worker_events import recorded_timeline
from simulate.services.harness_worker_text import digest, utc_iso
from simulate.services.harness_worker_timeline import HAND_OFF_TYPES, bounded, in_order
from simulate.services.run_traces_v3 import list_run_traces
from simulate.services.scoring_status import locked_call_metadata_update
from simulate.types import (
    CallTurn,
    DeclaredWorker,
    HarnessWorker,
    SealedToolCall,
    StoppedWorker,
    WorkerCleanup,
    WorkerEvent,
    WorkerSpan,
    WorkerState,
    WorkerTimeline,
    is_worker_subject,
    worker_subject_id,
)
from tracer.services.clickhouse.application_read_policy import application_read_context
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.span_reader import CHSpan

logger = structlog.get_logger(__name__)

WORKERS_KEY = "harness_workers"
# Set once a read of this attempt ran: when it ran and whether it failed, so a call with no record is told apart.
READ_KEY = "harness_workers_read"
# Seconds after the result; OpenTelemetry's batch exporter delivers a span within 30 s.
FIRST_READ = 35
SECOND_READ = 600
# How long past its due time a read may still be waiting in the queue.
READ_GRACE = 240
# How soon a client reads again once a read is due but has not run yet.
RECHECK = 30
# One call's span read: at most this many spans (past it the timeline is `partial`), for at most this long.
MAX_SPANS = 5000
SPAN_READ_CAP_MS = 30_000


def _span_dict(span: CHSpan) -> WorkerSpan:
    return {
        "span_id": str(span.id),
        "parent_span_id": span.parent_span_id or None,
        "name": span.name,
        "observation_type": span.observation_type,
        "start_time": span.start_time.isoformat() if span.start_time else None,
        "end_time": span.end_time.isoformat() if span.end_time else None,
        "status": span.status,
        "input": span.input,
        "output": span.output,
        "attributes": dict(span.attrs_string or {}),
        "trace_id": str(span.trace_id or ""),
        "model": span.model or "",
        "tokens": int(span.total_tokens or 0),
    }


_SCENARIO_DOMAINS = frozenset({"simulator", "environment"})

# The end state of a worker still out when the trial ended, by how the trial ended.
_STILL_OUT: dict[str, WorkerState] = {
    "ended": "no_return",
    "infrastructure": "infrastructure",
    "cancel_requested": "cancel_requested",
    "cancelled": "cancelled",
}
# Trials cut short, whose ending a worker seen only through its tool calls takes.
_CUT_SHORT_TRIALS = frozenset({"cancel_requested", "cancelled", "infrastructure"})


def worker_states(
    timeline: WorkerTimeline,
    *,
    trial: str,
    past_deadline: Iterable[str] = (),
    stopped: Mapping[str, float] | None = None,
) -> WorkerTimeline:
    """The timeline with each worker's end state, matching each hand-off to its own return by delegation id.
    A refusal past the deadline says its later world calls were refused, never that it was stopped (`stopped` names
    the workers whose own process the harness ended); no return is invented."""
    events = timeline["events"]
    handed: dict[str, list[str]] = {}
    for event in events:
        if event["type"] == "delegated":
            handed.setdefault(str(event["worker_id"]), []).append(
                str(event.get("delegation_id") or "")
            )
    returns = {
        str(event.get("delegation_id") or ""): event
        for event in events
        if event["type"] == "returned" and event.get("delegation_id")
    }
    # A record that kept no delegation ids pairs a worker's hand-offs with its returns in order instead.
    unkeyed: dict[str, list[WorkerEvent]] = {}
    for event in events:
        if event["type"] == "returned" and not event.get("delegation_id"):
            unkeyed.setdefault(str(event["worker_id"]), []).append(event)
    # Refused past its deadline: by the trial's world changes, or by the refusal code on its own recorded calls.
    stopped = dict(stopped or {})
    late = (
        set(past_deadline)
        | set(stopped)
        | {
            str(event["worker_id"]).rsplit("/", 1)[-1]
            for event in events
            if event.get("refusal_code") == "deadline_passed" and event.get("worker_id")
        }
    )
    workers: list[HarnessWorker] = []
    for worker in timeline["workers"]:
        ended = worker.copy()
        state: WorkerState
        delegations = handed.get(worker["worker_id"], [])
        spare = list(unkeyed.get(worker["worker_id"], []))
        back = [
            returns.get(one) if one else (spare.pop(0) if spare else None)
            for one in delegations
        ]
        # A hand-off with no return of its own is still out, whatever another hand-off to the same worker returned.
        out = any(one is None for one in back)
        if delegations and not out:
            state = (
                "failed"
                if any(one.get("status") == "error" for one in back if one)
                else "completed"
            )
        elif worker["id"] in late:
            # The world refused its calls, so the world saw it: observed, whatever the trace recorded.
            ended["observed"] = True
            ended["state"] = "deadline_passed"
            if worker["id"] in stopped:
                ended["stopped_at"] = stopped[worker["id"]]
            workers.append(ended)
            continue
        elif not worker["observed"]:
            state = "not_observed"
        elif out:
            state = _STILL_OUT.get(trial, "no_return")
        elif trial in _CUT_SHORT_TRIALS:
            # Seen only through its tool calls in a trial cut short: its hand-off was still open, so it takes that ending.
            ended["state"] = _STILL_OUT[trial]
            ended["seen"] = "tool_calls_only"
            workers.append(ended)
            continue
        else:
            # Seen only through its tool calls: no recorded end, so no end state rather than an invented one.
            workers.append(ended)
            continue
        ended["state"] = state
        workers.append(ended)
    return {**timeline, "workers": workers}


def deadline_refused(call: CallExecution) -> set[str]:
    """The workers the world refused past their deadline in this call's trial, by id, from its world changes."""
    # circular: hosted_harness_ingestion imports this module at its top.
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

    trial = (call.call_metadata or {}).get(TRIAL_STATE_KEY) or {}
    return {
        str(worker_subject_id(entry.get("who")))
        for entry in trial.get("state_changes") or []
        if isinstance(entry, dict)
        and is_worker_subject(entry.get("who"))
        and any(
            isinstance(one, dict) and one.get("code") == "deadline_passed"
            for one in entry.get("refused_actions") or []
        )
    }


def stopped_workers_of(state: Mapping[str, object]) -> list[StoppedWorker]:
    """The workers a trial's state says the harness stopped; an entry without a worker or a time is left out."""
    entries = state.get("stopped_workers")
    return [
        cast(StoppedWorker, one)
        for one in (entries if isinstance(entries, list) else [])
        if isinstance(one, dict)
        and one.get("worker")
        and isinstance(one.get("at"), (int, float))
        and not isinstance(one.get("at"), bool)
    ]


def stopped_workers(call: CallExecution) -> dict[str, float]:
    """The workers the harness stopped in this call's trial (their own process ended past their deadline), by id."""
    # circular: hosted_harness_ingestion imports this module at its top.
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

    trial = (call.call_metadata or {}).get(TRIAL_STATE_KEY) or {}
    return {str(one["worker"]): float(one["at"]) for one in stopped_workers_of(trial)}


def trial_ending(call: CallExecution, job: HostedHarnessJob | None) -> str:
    """How one call's trial ended, from the call row, its receipt and the job."""
    # circular: hosted_harness_ingestion imports this module at its top.
    from simulate.services.hosted_harness_ingestion import SCENARIO_FAILURE_CODES

    if call.status == CallExecution.CallStatus.CANCELLED:
        # Confirmed only once the platform has stopped the run; stopping our observation is not a confirmed cancel.
        confirmed = job is not None and job.state == HostedHarnessJob.State.CANCELED
        return "cancelled" if confirmed else "cancel_requested"
    receipt = (call.call_metadata or {}).get("hosted_harness_receipt")
    # A call the job closed without a receipt ended the way the job did.
    failure = (
        receipt.get("failure") if receipt else (getattr(job, "failure", None) or {})
    ) or {}
    if failure.get("code") == "user_canceled":
        # Stopped by the user (a receipt ingested before the call was marked cancelled for it): never infrastructure.
        confirmed = job is not None and job.state == HostedHarnessJob.State.CANCELED
        return "cancelled" if confirmed else "cancel_requested"
    # A scenario's own problem (simulator or environment side, or a scenario hold) is not an infrastructure fault.
    if (
        failure.get("code") in SCENARIO_FAILURE_CODES
        or failure.get("domain") in _SCENARIO_DOMAINS
    ):
        return "ended"
    return "infrastructure" if failure.get("domain") not in (None, "agent") else "ended"


# A worker's end when the trial stopped it before it returned; one that returned or never ran has none.
CUT_SHORT_STATES: frozenset[WorkerState] = frozenset(
    {"deadline_passed", "no_return", "cancel_requested", "cancelled", "infrastructure"}
)
_TERMINAL_JOB_STATES = ("completed", "failed", "canceled")


def _job_for_call(call: CallExecution) -> HostedHarnessJob | None:
    """The job that ran this call: its execution's job, else the newest job of its test execution."""
    execution = (
        HostedHarnessExecution.objects.select_related("job")
        .filter(call_execution=call)
        .first()
    )
    if execution is not None:
        return execution.job
    return (
        HostedHarnessJob.no_workspace_objects.filter(test_execution=call.test_execution)
        .order_by("-created_at")
        .first()
    )


def _call_attempt(
    call: CallExecution, job: HostedHarnessJob
) -> HostedHarnessAttempt | None:
    """The attempt that ran this call: its receipt's, else the job's current one."""
    execution = (
        HostedHarnessExecution.objects.select_related("receipt__attempt")
        .filter(call_execution=call)
        .first()
    )
    receipt = getattr(execution, "receipt", None) if execution is not None else None
    if receipt is not None:
        attempt: HostedHarnessAttempt = receipt.attempt
        return attempt
    return job.attempts.filter(attempt_number=job.current_attempt_number).first()


def worker_cleanup(
    call: CallExecution,
    job: HostedHarnessJob | None,
    attempt: HostedHarnessAttempt | None,
    worker: HarnessWorker,
) -> WorkerCleanup | None:
    """What happened to a cut-short worker's process when the call ended; computed when served, never stored.
    `stopped` only when the harness ended the worker's own process; one inside the app's process goes with the sandbox."""
    if worker.get("state") not in CUT_SHORT_STATES:
        return None
    stopped_at = worker.get("stopped_at")
    if stopped_at is not None:
        return {
            "status": "stopped",
            "at": datetime.fromtimestamp(stopped_at, tz=UTC).isoformat(),
        }
    if job is None:
        return None
    if attempt is None:
        return {"status": "unknown", "at": None}
    if attempt.cleanup_verified_at is not None:
        return {
            "status": "stopped_with_sandbox",
            "at": attempt.cleanup_verified_at.isoformat(),
        }
    if attempt.terminal_event_received and job.state in _TERMINAL_JOB_STATES:
        # The guest ended on its own and nothing confirmed the sandbox went.
        return {"status": "unknown", "at": None}
    # The attempt is live, or the cancel window is open with cleanup pending.
    ended = call.completed_at.isoformat() if call.completed_at else None
    return {"status": "still_running", "at": ended}


def stopped_by_user(call: CallExecution, attempt: UUID | None) -> bool:
    """Whether the user stopped this call; an earlier attempt's own status is not kept, so it never says."""
    return attempt is None and call.status == CallExecution.CallStatus.CANCELLED


def served_workers(call: CallExecution, timeline: WorkerTimeline) -> WorkerTimeline:
    """The stored record as served: a `cancel_requested` the job has since confirmed reads `cancelled`, and each
    cut-short worker carries its cleanup. The stored record is never rewritten."""
    workers = [one.copy() for one in timeline["workers"]]
    if not any(one.get("state") in CUT_SHORT_STATES for one in workers):
        return {**timeline, "workers": workers}
    job = _job_for_call(call)
    # Read once for every worker, not per worker.
    attempt = _call_attempt(call, job) if job is not None else None
    ending = (
        trial_ending(call, job)
        if any(one.get("state") == "cancel_requested" for one in workers)
        else None
    )
    for worker in workers:
        if worker.get("state") == "cancel_requested" and ending == "cancelled":
            worker["state"] = "cancelled"
        cleanup = worker_cleanup(call, job, attempt, worker)
        if cleanup is not None:
            worker["cleanup"] = cleanup
    return {**timeline, "workers": workers}


def declared_workers(job: HostedHarnessJob) -> list[DeclaredWorker]:
    """The workers the contract of the version the run was pinned to declares."""
    version = job.environment_version or (
        job.environment.environment_version if job.environment else None
    )
    contract = (
        (version.documents or {}).get("contract") if version is not None else None
    ) or {}
    return [
        cast(DeclaredWorker, one)
        for one in contract.get("workers") or []
        if isinstance(one, dict) and one.get("id")
    ]


def _clock_start(call: CallExecution, sealed: list[SealedToolCall]) -> float | None:
    """Epoch seconds the call's `start_time_ms` offsets count from: a sealed tool call's, else the call's start."""
    for one in sealed:
        at, offset = one.get("at"), one.get("start_time_ms")
        if isinstance(at, (int, float)) and isinstance(offset, (int, float)):
            start = float(at) - offset / 1000
            if utc_iso(start) is not None:
                return start
    return call.started_at.timestamp() if call.started_at else None


def _call_start(call: CallExecution, sealed: list[SealedToolCall]) -> str | None:
    start = _clock_start(call, sealed)
    return utc_iso(start) if start is not None else None


def _call_turns(call: CallExecution, sealed: list[SealedToolCall]) -> list[CallTurn]:
    """The call's messages with when each was said, on the tool calls' clock; a message with no offset is left out."""
    start = _clock_start(call, sealed)
    if start is None:
        return []
    # Only the caller and the agent under test; any other speaker is neither the request nor the reply.
    roles: dict[str, Literal["user", "agent"]] = {"user": "user", "assistant": "agent"}
    said = []
    for message in ChatMessageModel.objects.filter(call_execution=call).order_by(
        "created_at"
    ):
        for item in message.content if isinstance(message.content, list) else []:
            if (
                isinstance(item, dict)
                and isinstance(item.get("start_time_ms"), (int, float))
                and message.role in roles
            ):
                said.append(
                    (item["start_time_ms"], roles[message.role], item.get("content"))
                )
    if not said:
        said = [
            (row.start_time_ms, roles[row.speaker_role], row.content)
            for row in CallTranscript.objects.filter(
                call_execution=call, start_time_ms__isnull=False
            )
            if row.speaker_role in roles
        ]
    return [
        {"role": role, "text": text, "at": at}
        for offset, role, text in sorted(said, key=lambda one: one[0])
        if (at := utc_iso(start + offset / 1000)) is not None
    ]


# How far a read got, best first. A record without the field says nothing about its read, so it ranks with a failed one.
_COLLECTION_RANK = {
    "complete": 3,
    "partial": 2,
    "unavailable": 1,
    None: 1,
    "no_evidence": 0,
}


def _event_keys(one: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    """What says which call or hand-off an event is, across reads: its recorded id, the sealed row it stands for, and,
    with neither id, everything it recorded. Two events from two reads are one when any of these is shared.
    """
    keys: set[tuple[Any, ...]] = set()
    ident = one.get("span_id") or one.get("runtime_event_id")
    if one["type"] != "tool_call":
        ident = one.get("delegation_id") or ident
    if ident:
        keys.add((one["type"], str(ident)))
    # A launching call one read saw as a tool call is the hand-off another read saw with the same id.
    launch = (
        one.get("span_id")
        if one["type"] == "tool_call"
        else one.get("delegation_id") or one.get("span_id")
    )
    if launch and one["type"] in ("tool_call", "delegated", "transferred"):
        keys.add(("launch", str(launch)))
    if one.get("row") is not None:
        keys.add((one["type"], "row", one["row"]))
    if not ident:
        keys.add(
            (
                one["type"],
                one.get("worker_id"),
                one.get("tool"),
                one.get("at"),
                digest(one.get("input")),
                one.get("task"),
                one.get("result"),
            )
        )
    return keys


def _has_roots(spans: list[CHSpan]) -> bool:
    """Whether every trace read has its top-level span: a trace whose spans all name a parent lacks its root."""
    rooted: dict[str, bool] = {}
    for one in spans:
        trace = str(getattr(one, "trace_id", "") or "")
        rooted[trace] = rooted.get(trace, False) or not getattr(
            one, "parent_span_id", None
        )
    return all(rooted.values())


def kept_record(
    kept: Mapping[str, object] | None, read: WorkerTimeline
) -> WorkerTimeline:
    """Everything either read found, events joined by their recorded ids; the other fields from the read that found
    spans, then got further, then had every top-level span (on a tie, the later read)."""
    if not isinstance(kept, Mapping) or not (kept.get("events") or kept.get("workers")):
        return read
    stored = cast(WorkerTimeline, kept)

    def weight(one: Mapping[str, Any]) -> tuple[bool, int, bool]:
        # Not how many events a read found: an incomplete span tree finds more (a launcher read twice), not better.
        return (
            bool(one.get("spans_received")),
            _COLLECTION_RANK.get(one.get("collection"), 0),
            bool(one.get("tree_complete", True)),
        )

    # On a tie, the later read: it saw every span the earlier one did, and more.
    best, other = (read, stored) if weight(read) >= weight(stored) else (stored, read)
    events = [cast(WorkerEvent, dict(one)) for one in best.get("events") or []]
    # Each held event matches at most one, so two events alike in everything they recorded stay two.
    held = [_event_keys(one) for one in events]
    for one in other.get("events") or []:
        keys = _event_keys(one)
        match = next((at for at, mine in enumerate(held) if mine & keys), None)
        if match is None:
            events.append(cast(WorkerEvent, dict(one)))
        else:
            held[match] = set()
    in_order(events)
    omitted = max(
        int(best.get("events_omitted") or 0), int(other.get("events_omitted") or 0)
    )
    # The merged record keeps the same bounds as one read: as many events, and as much payload.
    events, cut = bounded(events)
    omitted += cut
    workers = [cast(HarnessWorker, dict(one)) for one in best.get("workers") or []]
    by_id = {one["worker_id"]: one for one in workers}
    for worker in other.get("workers") or []:
        if not worker.get("observed"):
            continue
        mine = by_id.get(worker["worker_id"])
        if mine is None:
            workers.append(cast(HarnessWorker, dict(worker)))
        elif not mine.get("observed"):
            mine["observed"] = True
            mine["instance_ids"] = list(
                dict.fromkeys(
                    [
                        *(mine.get("instance_ids") or []),
                        *(worker.get("instance_ids") or []),
                    ]
                )
            )
    traced = [one for one in (stored, read) if one.get("spans_received")] or [
        stored,
        read,
    ]
    collection = max(
        (one.get("collection") for one in traced),
        key=lambda name: _COLLECTION_RANK.get(name, 0),
    )
    return cast(
        WorkerTimeline,
        {
            **best,
            "events": events,
            "events_omitted": omitted,
            "workers": workers,
            "spans_received": any(one.get("spans_received") for one in (stored, read)),
            "collection": collection,
        },
    )


def mark_read(call_execution_id: UUID | str, *, failed: bool) -> None:
    """Note that this attempt's read ran, or failed; a failure never replaces a record an earlier read kept."""
    now = timezone.now().isoformat()

    def note(metadata: dict[str, object]) -> bool:
        if failed and metadata.get(WORKERS_KEY):
            return False
        metadata[READ_KEY] = {"at": now, "failed": failed}
        return True

    locked_call_metadata_update(call_execution_id, note)


def attempt_reset(call_execution_id: UUID | str) -> datetime | None:
    """When the call was last reset for a rerun of the call itself; None when it never was."""
    return (
        CallExecutionSnapshot.objects.filter(
            call_execution_id=UUID(str(call_execution_id)),
            rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        )
        .order_by("-snapshot_timestamp")
        .values_list("snapshot_timestamp", flat=True)
        .first()
    )


def _epoch(at: datetime) -> float:
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp()


def _begun_after(spans: list[CHSpan], since: float) -> list[CHSpan]:
    """The spans of the traces whose first span began after `since`; a trace with no start time is kept."""
    first: dict[str, float] = {}
    for one in spans:
        if one.start_time is not None:
            trace, at = str(one.trace_id or ""), _epoch(one.start_time)
            first[trace] = min(first.get(trace, at), at)
    return [
        one for one in spans if first.get(str(one.trace_id or ""), since + 1) > since
    ]


def with_later_read(
    record: WorkerTimeline, now: datetime, *, second_read: bool
) -> WorkerTimeline:
    """The record with when its later read is due, set after the first read only when that read may change it."""
    record.pop("next_read_at", None)
    # Spans may still be arriving: the later read replaces what an incomplete tree got wrong.
    if not second_read and (
        any(not one["observed"] for one in record["workers"])
        or not record.get("tree_complete", True)
        or record.get("collection") in ("partial", "unavailable")
        # A record built from the sandbox's rows alone still meets the spans that arrive later.
        or not record.get("spans_received", True)
    ):
        record["next_read_at"] = (now + timedelta(seconds=SECOND_READ)).isoformat()
    return record


def read_pending(due: str | None, now: datetime) -> bool:
    """Whether a read due at `due` may still change the record, allowing for the queue's delay."""
    if not due:
        return False
    try:
        at = datetime.fromisoformat(due)
    except ValueError:
        return False
    return now < (at if at.tzinfo else at.replace(tzinfo=UTC)) + timedelta(
        seconds=READ_GRACE
    )


def check_again_at(due: str, now: datetime) -> str:
    """When a client should read again: when the read is due, or soon after now once it is due but still queued."""
    at = datetime.fromisoformat(due)
    soon = now + timedelta(seconds=RECHECK)
    return max(at if at.tzinfo else at.replace(tzinfo=UTC), soon).isoformat()


def first_read_due(call: CallExecution) -> str | None:
    """When a completed hosted call's first worker read runs; None when none is scheduled."""
    if call.status != CallExecution.CallStatus.COMPLETED or call.completed_at is None:
        return None
    if not HostedHarnessJob.no_workspace_objects.filter(
        test_execution_id=call.test_execution_id
    ).exists():
        return None
    return (call.completed_at + timedelta(seconds=FIRST_READ)).isoformat()


def record_call_workers(
    call_execution_id: str, *, second_read: bool = False
) -> WorkerTimeline | None:
    """Keep one call's worker timeline on the call and return it; None when it has no worker or hand-off to keep."""
    call = CallExecution.objects.select_related("test_execution").get(
        id=call_execution_id
    )
    traces = list_run_traces(call.test_execution, call_execution_id=call.id)
    sealed = [
        cast(SealedToolCall, one)
        for one in ((call.provider_call_data or {}).get("livekit") or {}).get(
            "tool_calls"
        )
        or []
        if isinstance(one, dict)
    ]
    traced_calls = [
        one
        for one in sealed
        if one.get("worker_id")
        or one.get("worker_label")
        or one.get("delegated_to")
        or one.get("worker_events")
    ]
    job = _job_for_call(call)
    declared = declared_workers(job) if job is not None else []
    # A declared team with no evidence is still recorded, so the call never reads like a single-agent one.
    if traces.project_id is None and not traced_calls and not declared:
        mark_read(call.id, failed=False)
        return None
    spans: list[CHSpan] = []
    partial, unavailable = False, False
    # A rerun keeps the call's id, so only the traces begun after this attempt's reset are read.
    reset = attempt_reset(call.id)
    since = reset.timestamp() if reset is not None else None
    if traces.project_id is not None:
        with application_read_context(execution_cap_ms=SPAN_READ_CAP_MS):
            reader = get_reader()
            try:
                spans = reader.list_by_trace_ids(
                    [
                        one.trace_id
                        for one in traces.traces
                        if since is None
                        or one.start_time is None
                        or _epoch(one.start_time) > since
                    ],
                    project_id=traces.project_id,
                    include_heavy=False,
                    limit=MAX_SPANS + 1,
                )
            except Exception:
                # Kept, not raised: a retry would repeat the read on a loaded ClickHouse, and the later read still runs.
                logger.warning(
                    "call_workers_span_read_failed",
                    call_execution_id=str(call.id),
                    exc_info=True,
                )
                unavailable = True
            finally:
                reader.close()
        if since is not None:
            spans = _begun_after(spans, since)
        if len(spans) > MAX_SPANS:
            spans, partial = spans[:MAX_SPANS], True
    timeline = worker_states(
        recorded_timeline(
            [_span_dict(one) for one in spans],
            declared,
            run_key=str(job.run_id) if job is not None else str(call.test_execution_id),
            traced_calls=traced_calls,
            turns=_call_turns(call, sealed),
            started_at=_call_start(call, sealed),
        ),
        trial=trial_ending(call, job),
        past_deadline=deadline_refused(call),
        stopped=stopped_workers(call),
    )
    now = timezone.now()
    timeline["collected_at"] = now.isoformat()
    # An early read can lack the top-level span (it ends last) or stop at the row bound; it ranks below a whole one.
    timeline["tree_complete"] = not partial and _has_roots(spans)
    timeline["collection"] = (
        "unavailable"
        if unavailable
        else "partial"
        if partial
        else "complete"
        if (spans or traced_calls)
        else "no_evidence"
    )
    if not timeline["workers"] and not any(
        one["type"] in HAND_OFF_TYPES for one in timeline["events"]
    ):
        # A read that could not reach the spans found nothing because it saw nothing, not because there was none.
        mark_read(call.id, failed=unavailable)
        return None

    kept: list[WorkerTimeline] = []

    def keep(metadata: dict[str, object]) -> bool:
        # A later read adds what it found, and never takes away what an earlier one kept.
        merged = with_later_read(
            kept_record(
                cast("Mapping[str, object] | None", metadata.get(WORKERS_KEY)),
                timeline,
            ),
            now,
            second_read=second_read,
        )
        kept.append(merged)
        metadata[WORKERS_KEY] = merged
        return True

    locked_call_metadata_update(call.id, keep)
    return kept[-1] if kept else timeline
