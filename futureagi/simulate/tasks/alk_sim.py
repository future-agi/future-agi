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
        csat_score = _score_from_recording(call)
        if csat_score is None:
            csat_score = _score_from_transcript(call)
        if csat_score is None:
            raise RuntimeError("CSAT scorer returned no result for available evidence")
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
    metadata = dict(call.call_metadata or {})
    metadata["csat_status"] = status
    if error:
        metadata["csat_error"] = error[:2000]
    else:
        metadata.pop("csat_error", None)
    call.call_metadata = metadata
    call.save(update_fields=["call_metadata"])


@temporal_activity(
    time_limit=900,
    max_retries=2,
    queue="tasks_xl",
)
def judge_harness_sub_goals(call_execution_id: str) -> None:
    """Decide the sub-goals no code could settle, from the evidence the guest sealed.

    The sandbox holds no platform credentials, so judging happens here, where the run's
    organization and workspace are already known.
    """
    close_old_connections()
    try:
        call = CallExecution.objects.select_related(
            "test_execution", "test_execution__run_test"
        ).get(id=call_execution_id)
    except CallExecution.DoesNotExist:
        logger.warning("harness_judge_call_missing", call_execution_id=call_execution_id)
        return

    metadata = dict(call.call_metadata or {})
    pending = [str(name) for name in (metadata.get("harness_judge_pending") or [])]
    if not pending:
        return

    from simulate.services.harness_judging import (
        judge_sub_goal,
        load_scenario_evidence,
    )

    evidence = load_scenario_evidence(call)
    if evidence is None:
        _set_judge_state(call, "failed", "no evidence artifact was sealed for this scenario")
        return

    claims = {
        str(item.get("name")): item
        for item in (evidence.get("judged_sub_goals") or [])
        if isinstance(item, dict) and item.get("name")
    }
    outputs = dict(call.eval_outputs or {})
    decided = 0
    for name in pending:
        claim = claims.get(name)
        if claim is None:
            logger.warning(
                "harness_judge_claim_missing",
                call_execution_id=call_execution_id,
                sub_goal=name,
            )
            continue
        output_id, result = judge_sub_goal(call, claim, evidence)
        if result is None:
            continue
        outputs[output_id] = result
        decided += 1

    call.eval_outputs = outputs
    metadata = dict(call.call_metadata or {})
    metadata["harness_judge_pending"] = [
        name for name in pending if name not in claims
    ]
    metadata["harness_judge_status"] = "completed" if decided else "failed"
    call.call_metadata = metadata
    call.save(update_fields=["eval_outputs", "call_metadata"])


def _set_judge_state(call: CallExecution, status: str, error: str = "") -> None:
    metadata = dict(call.call_metadata or {})
    metadata["harness_judge_status"] = status
    if error:
        metadata["harness_judge_error"] = error[:2000]
    else:
        metadata.pop("harness_judge_error", None)
    call.call_metadata = metadata
    call.save(update_fields=["call_metadata"])
