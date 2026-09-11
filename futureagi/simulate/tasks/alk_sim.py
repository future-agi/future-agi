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
from django.db import close_old_connections

from simulate.constants.csat_score_prompt import CSAT_SCORE_PROMPT
from simulate.models import CallExecution
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

_CSAT_RULE_PROMPT = (
    CSAT_SCORE_PROMPT["criteria"] + "\n\n## Inputs\n\n<output>{{output}}</output>"
)
_CSAT_CHOICES = list(CSAT_SCORE_PROMPT["choices"])


@temporal_activity(
    time_limit=600,
    max_retries=0,
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
        return

    csat_score = _score_from_recording(call)
    if csat_score is None:
        csat_score = _score_from_transcript(call)
    if csat_score is None:
        logger.info("alk_csat_unavailable", call_execution_id=str(call.id))
        return

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
    logger.info(
        "alk_csat_scored",
        call_execution_id=str(call.id),
        csat_score=csat_score,
    )


def _score_from_recording(call: CallExecution) -> float | None:
    """Priority-1 CSAT via audio-native AgentEvaluator (turing_large).

    Runs only when the SDK supplied a public ``recording_url`` — otherwise
    the transcript-text path is used.
    """
    if not call.recording_url:
        return None
    score = _run_agent_csat(call.recording_url)
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
