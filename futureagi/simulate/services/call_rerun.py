import json
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.constants.hosted_harness import TRIAL_STATE_KEY
from simulate.models import CallExecution
from simulate.models.test_execution import CallExecutionSnapshot
from simulate.selectors.call_transcripts import with_speakers
from simulate.services.harness_worker_states import (
    ATTEMPT_END_KEY,
    ATTEMPTS_KEY,
    MAX_KEPT_ATTEMPTS,
)
from simulate.services.harness_workers import READ_KEY, WORKERS_KEY, attempt_end
from simulate.services.hosted_harness_ingestion import WORLD_ACTIONS_KEY

logger = structlog.get_logger(__name__)

# What an earlier attempt's worker answer and its trial view read, kept per attempt (`keep_attempt`); the receipt and
# the world's actions only in the fields they read.
_ATTEMPT_KEYS = (WORKERS_KEY, READ_KEY, TRIAL_STATE_KEY, "sub_goals")
_RECEIPT_FIELDS = ("sub_goals", "failure")
_WORLD_ACTION_FIELDS = ("actor_id", "action", "name", "refusal_code", "handoff")
# The closing attempt's own keys, gone from the call on a call rerun (kept per attempt instead), so its trial, receipt
# and worker record never read as the new attempt's. An evals-only rerun re-scores the same attempt and keeps them.
_ATTEMPT_LIVE_KEYS = (
    WORKERS_KEY,
    READ_KEY,
    TRIAL_STATE_KEY,
    WORLD_ACTIONS_KEY,
    "hosted_harness_receipt",
)
# A record past this is not kept (its attempt then reads `not_kept`): the call row is read on every list and detail.
MAX_KEPT_ATTEMPT_BYTES = 64 * 1024


def _attempt_record(metadata: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        key: metadata[key] for key in _ATTEMPT_KEYS if key in metadata
    }
    receipt = metadata.get("hosted_harness_receipt")
    if isinstance(receipt, dict):
        record["hosted_harness_receipt"] = {
            key: receipt[key] for key in _RECEIPT_FIELDS if key in receipt
        }
    actions = metadata.get(WORLD_ACTIONS_KEY)
    if isinstance(actions, list):
        record[WORLD_ACTIONS_KEY] = [
            {key: one[key] for key in _WORLD_ACTION_FIELDS if key in one}
            for one in actions
            if isinstance(one, dict)
        ]
    return record


def transcript_snapshot(call_execution: CallExecution) -> list[dict]:
    """The call's transcript as a rerun snapshot keeps it, with who spoke each actor turn."""
    return [
        dict(row)
        for row in with_speakers(call_execution.transcripts.all()).values(
            "speaker_role",
            "content",
            "start_time_ms",
            "end_time_ms",
            "confidence_score",
            "speaker_name",
            "actor_id",
        )
    ]


def _kept_attempts(
    metadata: dict[str, Any],
    call_id: Any,
    snapshot_id: Any,
    ending: dict[str, Any] | None,
) -> dict[str, Any]:
    """The call's kept attempts with the one closing now: its record by the snapshot's id, unless past
    `MAX_KEPT_ATTEMPT_BYTES`, and the latest `MAX_KEPT_ATTEMPTS` only."""
    record = _attempt_record(metadata)
    if ending is not None:
        record[ATTEMPT_END_KEY] = ending
    record["kept_at"] = timezone.now().isoformat()
    earlier = metadata.get(ATTEMPTS_KEY)
    attempts = dict(earlier if isinstance(earlier, dict) else {})
    size = len(json.dumps(record, default=str))
    if size > MAX_KEPT_ATTEMPT_BYTES:
        logger.warning(
            "harness_attempt_not_kept",
            call_execution_id=str(call_id),
            snapshot_id=str(snapshot_id),
            bytes=size,
        )
    else:
        attempts[str(snapshot_id)] = record
    # Stored as jsonb, which keeps no key order: the oldest go by when they were kept.
    latest = sorted(
        attempts, key=lambda key: str(attempts[key].get("kept_at") or ""), reverse=True
    )
    return {key: attempts[key] for key in latest[:MAX_KEPT_ATTEMPTS]}


def closed_attempt_metadata(
    metadata: Any,
    *,
    call_id: Any,
    snapshot_id: Any,
    ending: dict[str, Any] | None,
    keep_metadata_keys: tuple[str, ...] = (),
    clear_metadata: bool = False,
) -> dict[str, Any]:
    """A rerun call's metadata from what its locked row holds now: the closing attempt kept by its snapshot's id, and
    the attempt's own keys gone. ``clear_metadata`` keeps only ``row_id``, ``keep_metadata_keys`` and the kept
    attempts; without it, the attempt's own keys (`_ATTEMPT_LIVE_KEYS`) go. Every rerun path writes this, so a new attempt
    never starts with the earlier one's record."""
    current = metadata if isinstance(metadata, dict) else {}
    attempts = _kept_attempts(current, call_id, snapshot_id, ending)
    if clear_metadata:
        kept = {key: current[key] for key in keep_metadata_keys if key in current}
        # The results grid resolves a call's Scenario Information cells via row_id, so it always survives.
        if current.get("row_id"):
            kept["row_id"] = current["row_id"]
    else:
        kept = {
            key: value
            for key, value in current.items()
            if key not in _ATTEMPT_LIVE_KEYS
        }
    if attempts:
        kept[ATTEMPTS_KEY] = attempts
    return kept


def call_snapshot(call_execution: CallExecution) -> CallExecutionSnapshot:
    """The call's whole result as a call-and-eval rerun keeps it, with who spoke each turn. Not saved: single reruns
    save it, bulk reruns create many at once; each then writes `closed_attempt_metadata` by its id."""
    snapshot = CallExecutionSnapshot(
        call_execution=call_execution,
        rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        service_provider_call_id=call_execution.service_provider_call_id,
        status=call_execution.status,
        started_at=call_execution.started_at,
        completed_at=call_execution.completed_at,
        ended_at=call_execution.ended_at,
        duration_seconds=call_execution.duration_seconds,
        recording_url=call_execution.recording_url,
        stereo_recording_url=call_execution.stereo_recording_url,
        cost_cents=call_execution.cost_cents,
        stt_cost_cents=call_execution.stt_cost_cents,
        llm_cost_cents=call_execution.llm_cost_cents,
        tts_cost_cents=call_execution.tts_cost_cents,
        vapi_cost_cents=call_execution.vapi_cost_cents,
        call_summary=call_execution.call_summary,
        ended_reason=call_execution.ended_reason,
        overall_score=call_execution.overall_score,
        response_time_ms=call_execution.response_time_ms,
        assistant_id=call_execution.assistant_id,
        customer_number=call_execution.customer_number,
        call_type=call_execution.call_type,
        analysis_data=call_execution.analysis_data,
        evaluation_data=call_execution.evaluation_data,
        message_count=call_execution.message_count,
        transcript_available=call_execution.transcript_available,
        recording_available=call_execution.recording_available,
        eval_outputs=call_execution.eval_outputs,
        tool_outputs=call_execution.tool_outputs,
        provider_call_data=call_execution.provider_call_data,
        monitor_call_data=call_execution.monitor_call_data,
        avg_agent_latency_ms=call_execution.avg_agent_latency_ms,
        user_interruption_count=call_execution.user_interruption_count,
        user_interruption_rate=call_execution.user_interruption_rate,
        user_wpm=call_execution.user_wpm,
        bot_wpm=call_execution.bot_wpm,
        talk_ratio=call_execution.talk_ratio,
        ai_interruption_count=call_execution.ai_interruption_count,
        ai_interruption_rate=call_execution.ai_interruption_rate,
        avg_stop_time_after_interruption_ms=call_execution.avg_stop_time_after_interruption_ms,
        conversation_metrics_data=call_execution.conversation_metrics_data,
        transcripts=transcript_snapshot(call_execution),
    )
    return snapshot


def eval_snapshot(call_execution: CallExecution) -> CallExecutionSnapshot:
    """The call's evaluation results as an eval-only rerun keeps them. Not saved."""
    return CallExecutionSnapshot(
        call_execution=call_execution,
        rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
        eval_outputs=call_execution.eval_outputs,
        provider_call_data=call_execution.provider_call_data,
        overall_score=call_execution.overall_score,
        tool_outputs=call_execution.tool_outputs,
    )


def clear_call_for_rerun(
    call_execution: CallExecution,
    *,
    keep_metadata_keys: tuple[str, ...] = (),
    clear_metadata: bool = True,
) -> None:
    """Snapshot a call's result, then reset the row so a rerun can fill it again.

    ``clear_metadata`` drops ``call_metadata`` except ``row_id`` and ``keep_metadata_keys``; without it, the attempt's
    own keys go. Either way the closing attempt is kept (`closed_attempt_metadata`)."""
    with transaction.atomic():
        # A worker read or a receipt may write the row's metadata meanwhile: read it under the lock, then reset.
        locked = (
            CallExecution.objects.select_for_update()
            .only("call_metadata")
            .get(pk=call_execution.pk)
        )
        call_execution.call_metadata = locked.call_metadata
        ending = attempt_end(call_execution)
        snapshot = call_snapshot(call_execution)
        snapshot.save()
        call_execution.transcripts.all().delete()
        call_execution.reset_to_default(save=False)
        call_execution.call_metadata = closed_attempt_metadata(
            locked.call_metadata,
            call_id=call_execution.id,
            snapshot_id=snapshot.id,
            ending=ending,
            keep_metadata_keys=keep_metadata_keys,
            clear_metadata=clear_metadata,
        )
        call_execution.save()
