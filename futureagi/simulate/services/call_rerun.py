from django.db.models import F, Value
from django.db.models.functions import Coalesce

from simulate.models import CallExecution
from simulate.models.test_execution import CallExecutionSnapshot


def transcript_snapshot(call_execution: CallExecution) -> list[dict]:
    """The call's transcript as a rerun snapshot keeps it, with who spoke each actor turn."""
    return [
        dict(row)
        for row in call_execution.transcripts.values(
            "speaker_role",
            "content",
            "start_time_ms",
            "end_time_ms",
            "confidence_score",
            speaker_name=Coalesce("actor_turn__speaker_name", Value("")),
            actor_id=F("actor_turn__actor_id"),
        )
    ]


def call_snapshot(call_execution: CallExecution) -> CallExecutionSnapshot:
    """The call's whole result as a call-and-eval rerun keeps it, with who spoke each turn. Not saved: single reruns
    save it, bulk reruns create many at once."""
    return CallExecutionSnapshot(
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
    """Snapshot a call's result, then reset the row so a rerun can fill it again. Deleting the transcripts removes
    their actor rows with them (cascade).

    ``clear_metadata``: a hosted rerun drops ``call_metadata`` (the harness batch claim, eval flags) so its rerun is
    ingested like a first one, keeping ``row_id`` and any ``keep_metadata_keys``. A native rerun keeps it, apart from
    the eval flags ``reset_to_default`` clears."""
    call_snapshot(call_execution).save()
    call_execution.transcripts.all().delete()
    call_execution.reset_to_default(save=False)
    if clear_metadata:
        # The results grid resolves a call's Scenario Information cells via row_id, so it always survives.
        metadata = call_execution.call_metadata or {}
        kept = {key: metadata[key] for key in keep_metadata_keys if key in metadata}
        if metadata.get("row_id"):
            kept["row_id"] = metadata["row_id"]
        call_execution.call_metadata = kept
    call_execution.save()
