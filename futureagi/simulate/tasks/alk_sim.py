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

import structlog
from django.db import close_old_connections, transaction

from simulate.constants.csat_score_prompt import CSAT_SCORE_PROMPT
from simulate.models import CallExecution
from tfc.temporal.drop_in import temporal_activity
from tfc.utils.storage_client import server_reachable_url

logger = structlog.get_logger(__name__)

_CSAT_RULE_PROMPT = (
    CSAT_SCORE_PROMPT["criteria"] + "\n\n## Inputs\n\n<output>{{output}}</output>"
)
_CSAT_CHOICES = list(CSAT_SCORE_PROMPT["choices"])


@temporal_activity(
    time_limit=600,
    max_retries=2,
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
        return

    _set_csat_state(call, "running")
    try:
        csat_score, scorer_errors = _score_call(call)
        if csat_score is None and not scorer_errors:
            # Nothing to score is not a failure: no retry, "-" in the table.
            _set_csat_state(call, "skipped")
            logger.info("alk_csat_skipped", call_execution_id=str(call.id))
            return
        if csat_score is None:
            raise RuntimeError(_describe_scorer_errors(scorer_errors))
    except Exception as exc:
        _set_csat_state(call, "failed", str(exc))
        logger.exception("alk_csat_failed", call_execution_id=str(call.id))
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
    logger.info(
        "alk_csat_scored",
        call_execution_id=str(call.id),
        csat_score=csat_score,
    )


def _score_call(
    call: CallExecution,
) -> tuple[float | None, list[tuple[str, str]]]:
    """Try the recording, then the transcript; collect each scorer's failure.

    ``(None, [])`` means the call carried no evidence at all. ``(None, errors)``
    means evidence was present but every scorer that ran failed.
    """
    errors: list[str] = []
    for source, event, scorer in (
        ("recording", "alk_csat_recording_failed", _score_from_recording),
        ("transcript", "alk_csat_transcript_failed", _score_from_transcript),
    ):
        try:
            score = scorer(call)
        except Exception as exc:
            logger.warning(
                event,
                call_execution_id=str(call.id),
                error=str(exc),
                exc_info=True,
            )
            errors.append((source, str(exc)))
            continue
        if score is not None:
            return score, errors
    return None, errors


def _describe_scorer_errors(errors: list[tuple[str, str]]) -> str:
    """The stored CSAT reason: one line when every source failed the same way,
    otherwise one ``Source: message`` line per source."""
    messages = {message for _, message in errors}
    if len(messages) == 1:
        return messages.pop()
    return "\n".join(
        f"{source.capitalize()}: {message}" for source, message in errors
    )


def _score_from_recording(call: CallExecution) -> float | None:
    """Priority-1 CSAT via audio-native AgentEvaluator (turing_large).

    Runs only when the SDK supplied a public ``recording_url`` — otherwise
    the transcript-text path is used. Returns ``None`` only when there is no
    recording; a scorer failure raises.
    """
    if not call.recording_url:
        return None
    # Addressed for a server-side fetch; an unreachable URL is sniffed as text and scored as a link.
    score = _run_agent_csat(server_reachable_url(call.recording_url))
    if score is None:
        raise RuntimeError("CSAT scorer returned no score for the recording")
    return score


def _score_from_transcript(call: CallExecution) -> float | None:
    """Priority-2 CSAT — AgentEvaluator on the stored transcript text.

    Same evaluator + rule prompt as the recording path (and native voice), so
    scores stay consistent whether or not a recording was available. Returns
    ``None`` only when there is no transcript text; a scorer failure raises.
    """
    transcript_text = _build_transcript_text(call)
    if not transcript_text:
        return None
    score = _run_agent_csat(transcript_text)
    if score is None:
        raise RuntimeError("CSAT scorer returned no score for the transcript")
    return score


def _run_agent_csat(output: str) -> float | None:
    """Run the CSAT AgentEvaluator against a recording URL or transcript text.

    Mirrors ee.voice.temporal.activities.voice_xl.calculate_voice_csat_score:
    turing_large in agent mode, choices 1–10. A URL is auto-detected as audio;
    plain text is scored as text. Any failure propagates so the caller can
    record the real reason; a missing or unparseable result is reported as
    the scorer returning no score.
    """
    from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

    evaluator = AgentEvaluator(
        rule_prompt=_CSAT_RULE_PROMPT,
        model="turing_large",
        output_type="choices",
        choices=_CSAT_CHOICES,
        agent_mode="agent",
    )
    batch_result = evaluator.run(output=output, required_keys=["output"])
    try:
        return float(batch_result.eval_results[0]["data"]["result"])
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        raise RuntimeError(f"CSAT scorer returned no score: {exc!r}") from exc


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

    segments = list(
        CallTranscript.objects.filter(call_execution=call).order_by("start_time_ms")
    )
    if not segments:
        return None
    lines: list[str] = []
    for seg in segments:
        role = (
            "Customer"
            if seg.speaker_role == CallTranscript.SpeakerRole.USER
            else "Agent"
        )
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
        if error:
            metadata["csat_error"] = error[:2000]
        else:
            metadata.pop("csat_error", None)
        locked.call_metadata = metadata
        locked.save(update_fields=["call_metadata"])
    call.call_metadata = metadata
