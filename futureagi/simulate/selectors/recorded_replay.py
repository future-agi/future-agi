"""Recorded replay: whether a trial can be replayed with the tool answers it recorded, the recording a replay serves,
and which trial a replay replays."""

import json
from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from simulate.models import CallExecution, TestExecution
from simulate.selectors.call_detail import end_state_object_key
from simulate.services.hosted_harness_ingestion import (
    TRIAL_STATE_KEY,
    UPLOAD_BUCKET_NAME,
)
from tfc.utils.storage_client import get_storage_client

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


class RecordedReplayView(TypedDict):
    """What a trial's detail says about replaying it: whether it can be, why not, and the trial it replays."""

    available: bool
    unavailable_reason: str | None
    original: ReplayOriginal | None


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
    if call.simulation_call_type != CallExecution.SimulationCallType.TEXT:
        return "Recorded replay works for chat trials only."
    metadata, state = _trial(call)
    if not state or (state.get("recorded") is False and state.get("why") in _NO_DOOR):
        return (
            "This environment was built before its agent's tools could be answered from a recording. Rebuild it to "
            "replay its trials."
        )
    if not state.get("door") or end_state_object_key(call, metadata, state) is None:
        return "This trial kept no recording of its tool answers, so it can't be replayed."
    return None


def _sealed_log(call: CallExecution) -> list[dict[str, Any]]:
    """The world's door log the trial sealed with its end state."""
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
    log = sealed.get("action_log") if isinstance(sealed, dict) else None
    return [row for row in log or [] if isinstance(row, dict)]


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


def recorded_replay_source(metadata: Mapping[str, Any] | None) -> str | None:
    """The trial a run's payload metadata says it replays; None for any other run."""
    held = (metadata or {}).get(RECORDED_REPLAY_KEY)
    if not isinstance(held, dict) or not held.get("source_call_execution_id"):
        return None
    return str(held["source_call_execution_id"])
