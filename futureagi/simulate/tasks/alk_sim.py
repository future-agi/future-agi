"""Async tasks for ALK sim ingestion post-processing.

Computes CSAT for a completed voice call and writes ``overall_score`` +
``conversation_metrics_data['csat_score']`` so the frontend detail drawer
and KPI aggregate both light up.

Uses ``AgentEvaluator`` (turing_large, agent mode) for both scoring paths —
the same evaluator ``ee.voice.temporal.activities.voice_xl.calculate_voice_csat_score``
uses for native voice. The recording URL is scored audio-natively when the SDK
supplied one; otherwise the stored transcript text is scored. Both feed the
identical CSAT rule prompt, so scores are consistent across paths.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from uuid import UUID

import structlog
from django.db import close_old_connections, transaction
from django.utils import timezone

from simulate.constants.csat_score_prompt import CSAT_SCORE_PROMPT
from simulate.models import CallExecution
from simulate.services.harness_workers import (
    FIRST_READ,
    SECOND_READ,
    attempt_reset,
    mark_read,
)
from simulate.services.harness_workers import (
    record_call_workers as record_worker_timeline,
)
from simulate.services.scoring_status import (
    CSAT_STAMP_KEY,
    REASON_CSAT_NO_EVIDENCE,
    settle_run,
)
from simulate.types import CallWorkersRead
from tfc.logging.temporal import try_activity_info
from tfc.temporal.drop_in import temporal_activity
from tfc.utils.storage_client import server_reachable_url

logger = structlog.get_logger(__name__)

# Retries of the CSAT activity after its first attempt.
CSAT_MAX_RETRIES = 2
WORKERS_MAX_RETRIES = 2

_CSAT_RULE_PROMPT = (
    CSAT_SCORE_PROMPT["criteria"] + "\n\n## Inputs\n\n<output>{{output}}</output>"
)
_CSAT_CHOICES = list(CSAT_SCORE_PROMPT["choices"])


@temporal_activity(time_limit=120, max_retries=WORKERS_MAX_RETRIES, queue="tasks_s")
def record_call_workers(payload: dict[str, object]) -> None:
    """Keep which worker did what in one call, and schedule the later read the record says is due."""
    read = CallWorkersRead.parse(payload)
    reset = attempt_reset(read.call_execution_id)
    current = reset.isoformat() if reset is not None else ""
    # A call rerun since this read was scheduled is the new attempt's to read.
    if read.attempt is not None and read.attempt != current:
        return
    try:
        timeline = record_worker_timeline(
            read.call_execution_id, second_read=read.second_read
        )
    except Exception:
        # Out of retries, the call says its read failed rather than reading as one with no other agents.
        if _is_last_attempt(WORKERS_MAX_RETRIES):
            mark_read(read.call_execution_id, failed=True)
        raise
    if timeline and timeline.get("next_read_at"):
        later = CallWorkersRead(
            read.call_execution_id, second_read=True, attempt=current
        )
        record_call_workers.apply_async(
            args=(asdict(later),), start_delay=timedelta(seconds=SECOND_READ)
        )


def dispatch_worker_read(call_id: UUID | str) -> None:
    """Read the call's worker timeline once its spans have had time to arrive."""
    reset = attempt_reset(call_id)
    # Tied to this attempt, so a rerun before the read runs leaves the new attempt to its own read.
    read = CallWorkersRead(
        str(call_id), attempt=reset.isoformat() if reset is not None else ""
    )
    try:
        record_call_workers.apply_async(
            args=(asdict(read),), start_delay=timedelta(seconds=FIRST_READ)
        )
    except Exception:
        # Said on the call, not dropped: with no read the call would look like one with no other agents.
        logger.exception(
            "call_workers_read_dispatch_failed", call_execution_id=str(call_id)
        )
        mark_read(call_id, failed=True)


@temporal_activity(
    time_limit=600,
    max_retries=CSAT_MAX_RETRIES,
    queue="tasks_xl",
)
def calculate_alk_voice_csat_score(call_execution_id: str) -> None:
    close_old_connections()
    try:
        call = CallExecution.objects.select_related(
            "test_execution", "test_execution__run_test"
        ).get(id=call_execution_id)
    except CallExecution.DoesNotExist:
        logger.warning("alk_csat_call_missing", call_execution_id=call_execution_id)
        return

    # Idempotency keys on CSAT's own output, not overall_score — the eval path
    # (test_executor) also writes overall_score, so guarding on it would let
    # evals permanently suppress CSAT whenever they win the race.
    existing_csat = (call.conversation_metrics_data or {}).get("csat_score")
    if existing_csat is not None:
        _set_csat_state(call, "completed")
        settle_run(call.test_execution_id)
        return

    # Nothing to score is an answer, not a failure: no retry and no generic
    # error for a call with neither a recording nor a transcript.
    if not call.recording_url and not _build_transcript_text(call):
        _set_csat_state(call, "skipped", REASON_CSAT_NO_EVIDENCE)
        logger.info("alk_csat_skipped_no_evidence", call_execution_id=str(call.id))
        settle_run(call.test_execution_id)
        return

    _set_csat_state(call, "running")
    try:
        csat_score = _score_from_recording(call)
        if csat_score is None:
            csat_score = _score_from_transcript(call)
        if csat_score is None:
            raise RuntimeError("CSAT scorer returned no result for available evidence")
    except Exception as exc:
        logger.exception("alk_csat_failed", call_execution_id=str(call.id))
        if not _is_last_attempt():
            # Temporal runs this again, seconds from now unless its queue is
            # backed up. CSAT stays `running`, so its run keeps waiting for
            # the attempt that can still score it instead of completing on a
            # failure that is not final yet.
            raise
        _set_csat_state(call, "failed", str(exc))
        settle_run(call.test_execution_id)
        raise

    metrics = dict(call.conversation_metrics_data or {})
    metrics["csat_score"] = csat_score
    call.conversation_metrics_data = metrics
    update_fields = ["conversation_metrics_data"]
    # Only seed overall_score when the eval path hasn't already set it — CSAT is
    # its own metric and must not clobber an eval-derived overall score.
    if call.overall_score is None:
        call.overall_score = csat_score
        update_fields.append("overall_score")
    call.save(update_fields=update_fields)
    _set_csat_state(call, "completed")
    settle_run(call.test_execution_id)
    logger.info(
        "alk_csat_scored",
        call_execution_id=str(call.id),
        csat_score=csat_score,
    )


def _is_last_attempt(max_retries: int = CSAT_MAX_RETRIES) -> bool:
    """Whether Temporal will not run the activity again after this attempt.

    Called outside an activity (a direct call, a test) nothing retries it.
    Inside one, the attempt number is compared with the retry policy the
    server reports, or with this task's own when it reports none. A policy
    with no limit always has another attempt.
    """
    info = try_activity_info()
    if info is None:
        return True
    policy = info.retry_policy
    limit = max_retries + 1 if policy is None else policy.maximum_attempts
    return limit > 0 and info.attempt >= limit


def _score_from_recording(call: CallExecution) -> float | None:
    """Priority-1 CSAT via audio-native AgentEvaluator (turing_large).

    Runs only when the SDK supplied a public ``recording_url`` — otherwise
    the transcript-text path is used.
    """
    if not call.recording_url:
        return None
    # Addressed for a server-side fetch; an unreachable URL is sniffed as text and scored as a link.
    score = _run_agent_csat(server_reachable_url(call.recording_url))
    if score is None:
        logger.warning("alk_csat_recording_failed", call_execution_id=str(call.id))
    return score


def _score_from_transcript(call: CallExecution) -> float | None:
    """Priority-2 CSAT — AgentEvaluator on the stored transcript text.

    Same evaluator + rule prompt as the recording path (and native voice), so
    scores stay consistent whether or not a recording was available.
    """
    transcript_text = _build_transcript_text(call)
    if not transcript_text:
        return None
    score = _run_agent_csat(transcript_text)
    if score is None:
        logger.warning("alk_csat_transcript_failed", call_execution_id=str(call.id))
    return score


def _run_agent_csat(output: str) -> float | None:
    """Run the CSAT AgentEvaluator against a recording URL or transcript text.

    Mirrors ee.voice.temporal.activities.voice_xl.calculate_voice_csat_score:
    turing_large in agent mode, choices 1–10. A URL is auto-detected as audio;
    plain text is scored as text.
    """
    try:
        from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

        evaluator = AgentEvaluator(
            rule_prompt=_CSAT_RULE_PROMPT,
            model="turing_large",
            output_type="choices",
            choices=_CSAT_CHOICES,
            agent_mode="agent",
        )
        batch_result = evaluator.run(output=output, required_keys=["output"])
        return float(batch_result.eval_results[0]["data"]["result"])
    except (ValueError, TypeError, IndexError, KeyError):
        return None
    except Exception:
        logger.exception("alk_csat_agent_evaluator_failed")
        return None


def _build_transcript_text(call: CallExecution) -> str | None:
    if call.simulation_call_type == CallExecution.SimulationCallType.TEXT:
        from simulate.models.chat_message import ChatMessageModel
        from simulate.utils.chat_simulation import _build_chat_transcript

        messages = list(
            ChatMessageModel.objects.filter(call_execution=call).order_by("created_at")
        )
        transcript = _build_chat_transcript(messages)
        if transcript and transcript.strip():
            return transcript

        # Hosted chat results created before native ChatMessage materialization
        # was added are still valid: their transcript is stored in the shared
        # CallTranscript table. Fall through to that representation instead of
        # declaring the completed call to have no CSAT evidence.

    from simulate.models.test_execution import CallTranscript
    from simulate.selectors.call_transcripts import with_speakers
    from simulate.utils.stored_transcript_roles import call_actor_label

    segments = list(
        with_speakers(CallTranscript.objects.filter(call_execution=call)).order_by(
            "start_time_ms"
        )
    )
    if not segments:
        return None
    lines: list[str] = []
    for seg in segments:
        if seg.speaker_role == CallTranscript.SpeakerRole.ACTOR:
            role = call_actor_label(seg.speaker_name)
        elif seg.speaker_role == CallTranscript.SpeakerRole.USER:
            role = "Customer"
        else:
            role = "Agent"
        content = (seg.content or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else None


def _set_csat_state(
    call: CallExecution,
    status: str,
    error: str = "",
) -> None:
    """Record where CSAT reached, re-reading the row so a stale copy cannot revert the eval flags."""
    with transaction.atomic():
        locked = CallExecution.objects.select_for_update().get(id=call.id)
        metadata = dict(locked.call_metadata or {})
        metadata["csat_status"] = status
        if status == "running":
            # The CSAT job clock runs from the start of this attempt.
            metadata[CSAT_STAMP_KEY] = timezone.now().isoformat()
        if error:
            metadata["csat_error"] = error[:2000]
        else:
            metadata.pop("csat_error", None)
        locked.call_metadata = metadata
        locked.save(update_fields=["call_metadata"])
    call.call_metadata = metadata
