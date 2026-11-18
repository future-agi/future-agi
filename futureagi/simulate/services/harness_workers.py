"""A call's kept worker record: reading the app's spans after the call, keeping each read, and serving what was kept."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import structlog
from django.utils import timezone

from simulate.constants.hosted_harness import TRIAL_STATE_KEY
from simulate.models import (
    CallExecution,
    CallTranscript,
    HostedHarnessJob,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import CallExecutionSnapshot
from simulate.services.harness_worker_events import recorded_timeline
from simulate.services.harness_worker_states import (
    ATTEMPT_END_KEY,
    _call_attempt,
    _execution,
    _job_of,
    deadline_refused,
    job_for_call,
    refused_past_deadline,
    stopped_by_user,
    stopped_workers,
    stopped_workers_of,
    trial_ending,
    worker_cleanup,
    worker_states,
)
from simulate.services.harness_worker_text import digest, utc_iso
from simulate.services.harness_worker_timeline import HAND_OFF_TYPES, bounded, in_order
from simulate.services.run_traces_v3 import RunTraces, list_run_traces
from simulate.services.scoring_status import locked_call_metadata_update
from simulate.types import (
    CallTurn,
    DeclaredWorker,
    HarnessWorker,
    SealedToolCall,
    WorkerEvent,
    WorkerSpan,
    WorkerTimeline,
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
        # The collector writes 0 when the span reported none: unreported, not zero.
        "tokens": int(span.total_tokens) if span.total_tokens else None,
    }


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


_ANY_ATTEMPT = object()


def _same_attempt(call_execution_id: UUID | str, attempt: object) -> bool:
    """Whether the call is still on the attempt a read was for (`attempt`, its reset time); read under the caller's
    row lock, so a rerun that reset the call meanwhile is seen."""
    return attempt is _ANY_ATTEMPT or attempt_reset(call_execution_id) == attempt


def mark_read(
    call_execution_id: UUID | str, *, failed: bool, attempt: object = _ANY_ATTEMPT
) -> None:
    """Note that this attempt's read ran, or failed; a failure never replaces a record an earlier read kept, and a
    read for an attempt a rerun has since replaced (`attempt`, its reset time) notes nothing."""
    now = timezone.now().isoformat()

    def note(metadata: dict[str, object]) -> bool:
        if not _same_attempt(call_execution_id, attempt):
            return False
        if failed and metadata.get(WORKERS_KEY):
            return False
        metadata[READ_KEY] = {"at": now, "failed": failed}
        return True

    locked_call_metadata_update(call_execution_id, note)


def recorded_worker_names(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    """Each worker's name by its contract id, as the call's worker record keeps them; empty without a record."""
    record = (metadata or {}).get(WORKERS_KEY)
    workers = record.get("workers") if isinstance(record, Mapping) else None
    return {
        str(one["id"]): str(one["name"])
        for one in workers or []
        if isinstance(one, Mapping) and one.get("id") and one.get("name")
    }


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


def attempt_end(call: CallExecution) -> dict[str, Any] | None:
    """How the call's attempt ended, to keep with its rerun snapshot: whether the user stopped it, how the trial
    ended, and what became of the sandbox of a worker it cut short. None when the call kept no worker record."""
    if not (call.call_metadata or {}).get(WORKERS_KEY):
        return None
    execution = _execution(call)
    job = _job_of(call, execution)
    sandbox = _call_attempt(execution, job) if job is not None else None
    return {
        "stopped_by_user": stopped_by_user(call),
        "ending": trial_ending(call, job),
        "cleanup": worker_cleanup(
            call, job, sandbox, cast(HarnessWorker, {"state": "cancelled"})
        ),
    }


_ENDED = (
    CallExecution.CallStatus.COMPLETED,
    CallExecution.CallStatus.FAILED,
    CallExecution.CallStatus.CANCELLED,
)


def first_read_due(call: CallExecution) -> str | None:
    """When an ended hosted call's first worker read runs, however it ended (a read is scheduled for every ended trial,
    a stopped one too); None when none is scheduled."""
    if call.status not in _ENDED or call.completed_at is None:
        return None
    if not HostedHarnessJob.no_workspace_objects.filter(
        test_execution_id=call.test_execution_id
    ).exists():
        return None
    return (call.completed_at + timedelta(seconds=FIRST_READ)).isoformat()


def later_read_due(first_due: str) -> str:
    """When the later read runs for a call whose first read, due at `first_due`, found nothing to keep."""
    return (
        datetime.fromisoformat(first_due) + timedelta(seconds=SECOND_READ)
    ).isoformat()


def _sealed_calls(provider_call_data: object) -> list[SealedToolCall]:
    """The tool calls the harness sealed on a call or an attempt's snapshot."""
    data = provider_call_data if isinstance(provider_call_data, dict) else {}
    return [
        cast(SealedToolCall, one)
        for one in (data.get("livekit") or {}).get("tool_calls") or []
        if isinstance(one, dict)
    ]


def _with_workers(sealed: list[SealedToolCall]) -> list[SealedToolCall]:
    return [
        one
        for one in sealed
        if one.get("worker_id")
        or one.get("worker_label")
        or one.get("delegated_to")
        or one.get("worker_events")
    ]


def attempt_timeline(
    call: CallExecution,
    attempt: CallExecutionSnapshot,
    metadata: Mapping[str, Any],
) -> WorkerTimeline | None:
    """An earlier attempt's record built from the tool calls its snapshot sealed, for an attempt rerun before its own
    read ran; None when it sealed no worker and the app declares none. Nothing is stored and no span is read."""
    traced = _with_workers(_sealed_calls(attempt.provider_call_data))
    job = job_for_call(call)
    declared = declared_workers(job) if job is not None else []
    if not traced and not declared:
        return None
    end = metadata.get(ATTEMPT_END_KEY)
    trial = metadata.get(TRIAL_STATE_KEY)
    timeline = worker_states(
        recorded_timeline(
            [],
            declared,
            run_key=str(job.run_id) if job is not None else str(call.test_execution_id),
            traced_calls=traced,
        ),
        trial=(end.get("ending") if isinstance(end, Mapping) else None) or "ended",
        past_deadline=refused_past_deadline(metadata),
        stopped={
            str(one["worker"]): float(one["at"])
            for one in stopped_workers_of(trial if isinstance(trial, Mapping) else {})
        },
    )
    timeline["collected_at"] = timezone.now().isoformat()
    timeline["project_id"] = None
    timeline["tree_complete"] = True
    timeline["spans_received"] = False
    timeline["collection"] = "complete" if traced else "no_evidence"
    return timeline


def record_call_workers(
    call_execution_id: str, *, second_read: bool = False, attempt: object = _ANY_ATTEMPT
) -> WorkerTimeline | None:
    """Keep one call's worker timeline on the call and return it; None when it has no worker or hand-off to keep.
    `attempt` is the reset time of the attempt the read was scheduled for, read before anything else."""
    call = CallExecution.objects.select_related("test_execution").get(
        id=call_execution_id
    )
    # The attempt this read is for, fixed before any read: a rerun during the trace or span read is never this one.
    reset = (
        attempt_reset(call.id)
        if attempt is _ANY_ATTEMPT
        else cast("datetime | None", attempt)
    )
    sealed = _sealed_calls(call.provider_call_data)
    traced_calls = _with_workers(sealed)
    # The worker events the harness sealed are the record; the app's spans only add to it (models, tokens, older
    # calls), so a span store that cannot be reached never loses a sealed record.
    sealed_events = any(one.get("worker_events") for one in traced_calls)
    try:
        traces = list_run_traces(call.test_execution, call_execution_id=call.id)
    except Exception:
        if not sealed_events:
            raise
        logger.warning(
            "call_workers_trace_list_failed",
            call_execution_id=str(call.id),
            exc_info=True,
        )
        traces = RunTraces(project_id=None, traces=[])
    job = job_for_call(call)
    declared = declared_workers(job) if job is not None else []
    # A declared team with no evidence is still recorded, so the call never reads like a single-agent one.
    if traces.project_id is None and not traced_calls and not declared:
        # Spans may still be on their way: only the later read says there were none.
        if second_read:
            mark_read(call.id, failed=False, attempt=reset)
        return None
    spans: list[CHSpan] = []
    partial, unavailable = False, False
    # A rerun keeps the call's id, so only the traces begun after this attempt's reset are read.
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
    timeline["project_id"] = (
        str(traces.project_id) if traces.project_id is not None else None
    )
    # An early read can lack the top-level span (it ends last) or stop at the row bound; it ranks below a whole one.
    timeline["tree_complete"] = not partial and _has_roots(spans)
    timeline["collection"] = (
        "unavailable"
        if unavailable and not sealed_events
        else "partial"
        if partial
        else "complete"
        if (spans or traced_calls)
        else "no_evidence"
    )
    if not timeline["workers"] and not any(
        one["type"] in HAND_OFF_TYPES for one in timeline["events"]
    ):
        # A read that could not reach the spans found nothing because it saw nothing, not because there was none;
        # a first read that found nothing leaves the answer to the later one.
        if second_read or unavailable:
            mark_read(call.id, failed=unavailable, attempt=reset)
        return None

    kept: list[WorkerTimeline] = []

    def keep(metadata: dict[str, object]) -> bool:
        # Rechecked under the row lock: a rerun that reset the call during this read owns the call now.
        if not _same_attempt(call.id, reset):
            return False
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

    if locked_call_metadata_update(call.id, keep) is None:
        return None
    return kept[-1] if kept else timeline
