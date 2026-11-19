"""Recorded replay: whether a trial can be replayed with the tool answers it recorded, the recording a replay serves,
and which trial a replay replays."""

import json
from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

import structlog

from simulate.models import CallExecution, HostedHarnessJob, TestExecution
from simulate.selectors.call_detail import end_state_object_key
from simulate.services.hosted_harness_ingestion import (
    TRIAL_STATE_KEY,
    UPLOAD_BUCKET_NAME,
)
from tfc.utils.storage_client import get_storage_client

logger = structlog.get_logger(__name__)

# Where a replay keeps the trial it replays: the run job's payload metadata, which the harness reads with the
# recording, and the run's execution metadata, which the run views read without it.
RECORDED_REPLAY_KEY = "recorded_replay"
# What each recorded call keeps, the agent's and each actor's: who made it, what it asked, how it ended and what its
# caller received, as the world's door logged them. Never the world's records, which the same artifact holds for
# re-grading.
RECORDED_FIELDS = (
    "actor_id",
    "action",
    "name",
    "method",
    "arguments",
    "effect",
    "outcome",
    "refusal_code",
    "error",
    "fault",
    "received",
    # The route and query of a call the agent made over HTTP through its tool proxy.
    "path",
)
# A recording larger than this is refused rather than carried in the replay's run.
MAX_RECORDING_BYTES = 1_000_000
_FINISHED = (CallExecution.CallStatus.COMPLETED, CallExecution.CallStatus.FAILED)
# A world record that says the agent ran its tools where no door stood: none of its calls was recorded.
_NO_DOOR = ("in_process_tools", "sealed_without_door", "calls_not_seen")
# A row of the door's log that is no call the agent made: a request stopped at the test world's edge, or a mark that a
# call ran unrecorded.
_NOT_CALLS = ("egress_blocked", "evidence_incomplete")


class ReplayOriginal(TypedDict):
    """The trial a replay replays, and how it ended when the trial view shows the two side by side."""

    call_execution_id: str
    test_execution_id: str | None
    outcome: NotRequired[str]


class ReplayStop(TypedDict):
    """Where a recorded replay stopped: the number of the call, its tool, and whether the agent took another path
    than the original trial (else the recording could not answer the call)."""

    call: int
    tool: str
    diverged: bool


class LiveRun(TypedDict):
    """The run that tries a replay's agent version on the live test world: the replayed scenario, once."""

    scenario_id: str
    agent_version_id: str | None


class RecordedReplayView(TypedDict):
    """What a trial's detail says about replaying it: whether it can be, why not, and the trial it replays."""

    available: bool
    unavailable_reason: str | None
    original: ReplayOriginal | None
    stopped: NotRequired[ReplayStop | None]
    live_run: NotRequired[LiveRun | None]
    agent_changed: NotRequired[bool]


def replay_of(execution: TestExecution | None) -> ReplayOriginal | None:
    """The trial a recorded-replay run replays; None for any other run."""
    metadata = getattr(execution, "execution_metadata", None)
    held = metadata.get(RECORDED_REPLAY_KEY) if isinstance(metadata, dict) else None
    if not isinstance(held, dict) or not held.get("source_call_execution_id"):
        return None
    source_run = held.get("source_test_execution_id")
    return {
        "call_execution_id": str(held["source_call_execution_id"]),
        "test_execution_id": str(source_run) if source_run else None,
    }


def _trial(call: CallExecution) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    state = metadata.get(TRIAL_STATE_KEY)
    return metadata, state if isinstance(state, dict) else {}


def replay_unavailable_reason(call: CallExecution) -> str | None:
    """Why this trial cannot be replayed with its recorded tool answers, in the words the trial shows; None when it
    can. Read from what the trial stored, without opening its artifacts."""
    if call.status not in _FINISHED:
        return "Replay is available once this trial has finished."
    if replay_of(call.test_execution) is not None:
        return "This trial is a recorded replay. Replay the original trial instead."
    metadata, state = _trial(call)
    if not state or (state.get("recorded") is False and state.get("why") in _NO_DOOR):
        return (
            "This environment was built before its agent's tools could be answered from a recording. Rebuild it to "
            "replay its trials."
        )
    if not state.get("door") or end_state_object_key(call, metadata, state) is None:
        return "This trial kept no recording of its tool answers, so it can't be replayed."
    return None


def _sealed(call: CallExecution) -> dict[str, Any]:
    """The world state the trial sealed: its end records and its door's log."""
    metadata, state = _trial(call)
    key = end_state_object_key(call, metadata, state)
    if key is None:
        raise ValueError(
            "This trial kept no recording of its tool answers, so it can't be replayed."
        )
    response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
    try:
        sealed = json.loads(response.read())
    finally:
        response.close()
        response.release_conn()
    return sealed if isinstance(sealed, dict) else {}


def _sealed_log(call: CallExecution) -> list[dict[str, Any]]:
    """The world's door log the trial sealed with its end state."""
    log = _sealed(call).get("action_log")
    return [row for row in log or [] if isinstance(row, dict)]


def recorded_end_state(call: CallExecution) -> dict[str, list[dict[str, Any]]] | None:
    """The records the trial's world ended with, for grading a replay of it that made every change it made: each sealed
    table's rows. None when they do not hold the whole end: a table left out of the seal, or a call that ran without the
    door recording it, whose change the replay could not repeat. Evaluator-only: never part of the recording."""
    sealed = _sealed(call)
    tables = sealed.get("tables")
    log = sealed.get("action_log") or []
    if not isinstance(tables, dict) or sealed.get("skipped"):
        return None
    if any(isinstance(row, dict) and row.get("refusal_code") == "evidence_incomplete" for row in log):
        return None
    return {str(table): [row for row in rows if isinstance(row, dict)] for table, rows in tables.items() if isinstance(rows, list)}


def recorded_end_state_for(metadata: Mapping[str, Any] | None) -> dict[str, dict[str, list[dict[str, Any]]]] | None:
    """A replay run's original end state by scenario key, for the harness to grade its state checks on; None for any
    other run, or when the original's end state is not whole or cannot be read: those checks are then not measured."""
    held = (metadata or {}).get(RECORDED_REPLAY_KEY)
    if not isinstance(held, dict) or not held.get("source_call_execution_id") or not held.get("scenario_key"):
        return None
    source = (
        CallExecution.no_workspace_objects.filter(id=held["source_call_execution_id"])
        .select_related("test_execution__run_test")
        .first()
    )
    if source is None:
        return None
    try:
        tables = recorded_end_state(source)
    except Exception as exc:  # noqa: BLE001 - fail open: the replay's state checks are then not measured
        logger.warning("recorded_end_state_unreadable", call_execution_id=str(source.id), error=type(exc).__name__)
        return None
    return {str(held["scenario_key"]): tables} if tables is not None else None


def replay_stop_of(call: CallExecution) -> ReplayStop | None:
    """Where this recorded replay stopped, as its result said; None when it did not stop."""
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    receipt = metadata.get("hosted_harness_receipt")
    failure = receipt.get("failure") if isinstance(receipt, dict) else None
    details = failure.get("details") if isinstance(failure, dict) else None
    stop = details.get("replay_stop") if isinstance(details, dict) else None
    if not isinstance(stop, dict) or not isinstance(stop.get("call"), int) or not stop.get("tool"):
        return None
    return {"call": stop["call"], "tool": str(stop["tool"]), "diverged": stop.get("diverged") is True}


def recorded_calls(call: CallExecution) -> list[dict[str, Any]]:
    """The recording a replay of this trial serves: each call the agent and its actors made, in order, with what it
    asked, how it ended and what its caller received, from the door log the trial sealed. A trial recorded before
    answers were kept replays until its first call the recording cannot prove; the harness stops there and says so.
    Raises ValueError with the reason a person reads when there is none to serve or it is too large to carry."""
    calls = [
        {field: row[field] for field in RECORDED_FIELDS if field in row}
        for row in _sealed_log(call)
        if row.get("refusal_code") not in _NOT_CALLS and row.get("name")
    ]
    if len(json.dumps(calls, default=str)) > MAX_RECORDING_BYTES:
        raise ValueError("This trial's tool recording is too large to replay.")
    return calls


def recorded_replay(call: CallExecution) -> RecordedReplayView:
    """Whether the trial can be replayed, why not, and which trial it replays when it is a replay itself."""
    reason = replay_unavailable_reason(call)
    return {
        "available": reason is None,
        "unavailable_reason": reason,
        "original": replay_of(call.test_execution),
    }


def replay_result(call: CallExecution) -> dict[str, Any]:
    """What a recorded replay's trial shows beyond its original: where it stopped, the run that tries its agent version
    on the live test world, and whether that version is another than the original trial's. Empty for any other trial."""
    execution = call.test_execution
    metadata = getattr(execution, "execution_metadata", None)
    held = metadata.get(RECORDED_REPLAY_KEY) if isinstance(metadata, dict) else None
    if not isinstance(held, dict):
        return {}
    jobs = HostedHarnessJob.no_workspace_objects
    replayed_with = jobs.filter(test_execution_id=call.test_execution_id).values_list("agent_version_id", flat=True)
    replay_agent = replayed_with.first()
    source_agent = (
        jobs.filter(id=held["source_job_id"]).values_list("agent_version_id", flat=True).first()
        if held.get("source_job_id")
        else None
    )
    return {
        "stopped": replay_stop_of(call),
        "live_run": (
            {"scenario_id": str(held["scenario_key"]), "agent_version_id": str(replay_agent) if replay_agent else None}
            if held.get("scenario_key")
            else None
        ),
        "agent_changed": replay_agent is not None and replay_agent != source_agent,
    }


def recorded_replay_source(metadata: Mapping[str, Any] | None) -> str | None:
    """The trial a run's payload metadata says it replays; None for any other run."""
    held = (metadata or {}).get(RECORDED_REPLAY_KEY)
    if not isinstance(held, dict) or not held.get("source_call_execution_id"):
        return None
    return str(held["source_call_execution_id"])
