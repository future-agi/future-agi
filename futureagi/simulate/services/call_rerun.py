from simulate.models import CallExecution
from simulate.models.test_execution import CallExecutionSnapshot


def clear_call_for_rerun(
    call_execution: CallExecution, *, keep_metadata_keys: tuple[str, ...] = ()
) -> None:
    """Snapshot a call's result, then reset the row so a rerun can fill it again."""

    CallExecutionSnapshot.objects.create(
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
        transcripts=list(
            call_execution.transcripts.values(
                "speaker_role",
                "content",
                "start_time_ms",
                "end_time_ms",
                "confidence_score",
            )
        ),
    )

    call_execution.transcripts.all().delete()

    call_execution.service_provider_call_id = None
    call_execution.monitor_call_data = None
    call_execution.status = CallExecution.CallStatus.PENDING
    call_execution.started_at = None
    call_execution.completed_at = None
    call_execution.ended_at = None
    call_execution.duration_seconds = None
    call_execution.recording_url = None
    call_execution.stereo_recording_url = None
    call_execution.cost_cents = None
    call_execution.stt_cost_cents = None
    call_execution.llm_cost_cents = None
    call_execution.tts_cost_cents = None
    call_execution.vapi_cost_cents = None
    call_execution.call_summary = None
    call_execution.ended_reason = None
    call_execution.overall_score = None
    call_execution.response_time_ms = None
    call_execution.assistant_id = None
    call_execution.customer_number = None
    call_execution.call_type = None
    call_execution.analysis_data = None
    call_execution.evaluation_data = None
    call_execution.message_count = None
    call_execution.transcript_available = False
    call_execution.recording_available = False
    call_execution.eval_outputs = {}
    call_execution.tool_outputs = {}
    call_execution.provider_call_data = {}
    call_execution.avg_agent_latency_ms = None
    call_execution.user_interruption_count = None
    call_execution.user_interruption_rate = None
    call_execution.user_wpm = None
    call_execution.bot_wpm = None
    call_execution.talk_ratio = None
    call_execution.ai_interruption_count = None
    call_execution.ai_interruption_rate = None
    call_execution.avg_stop_time_after_interruption_ms = None
    call_execution.conversation_metrics_data = None
    # The results grid resolves a call's Scenario Information cells via row_id, so
    # it always survives. Everything else (the ALK alk_batch_claimed claim, eval
    # flags) is dropped so the rerun's result is ingested like a first one; callers
    # name any further identity keys the rerun still needs.
    metadata = call_execution.call_metadata or {}
    kept = {key: metadata[key] for key in keep_metadata_keys if key in metadata}
    if metadata.get("row_id"):
        kept["row_id"] = metadata["row_id"]
    call_execution.call_metadata = kept
    call_execution.save()
