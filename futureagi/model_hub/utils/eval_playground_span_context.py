"""Pure span/voice eval context; shared by ID resolution and exact GET payloads."""

import json


def build_span_context(span) -> dict:
    """Build a span_context dict from an ObservationSpan row.

    For voice spans (observation_type == 'conversation' or Vapi-style
    span_attributes present), promotes the most useful nested fields
    (transcript, recording_url, ended_reason, duration, meaningful
    input/output) to the top level so evaluator templates can use:

        {{span.transcript}}
        {{span.recording_url}}
        {{span.ended_reason}}
        {{span.duration_seconds}}
        {{span.input}}   # first user turn
        {{span.output}}  # last assistant turn

    instead of the deeply-nested real locations
    (`{{span.span_attributes.provider_transcript}}` etc.).
    """
    base = {
        "id": span.id,
        "trace_id": str(span.trace_id) if getattr(span, "trace_id", None) else None,
        "name": span.name,
        "observation_type": span.observation_type,
        "input": span.input,
        "output": span.output,
        "span_attributes": span.span_attributes or {},
        "resource_attributes": span.resource_attributes or {},
        "status": span.status,
        "status_message": span.status_message,
        "model": span.model,
        "provider": span.provider,
        "start_time": str(span.start_time) if span.start_time else None,
        "end_time": str(span.end_time) if span.end_time else None,
        "latency_ms": span.latency_ms,
        "cost": float(span.cost) if span.cost is not None else None,
        "prompt_tokens": span.prompt_tokens,
        "completion_tokens": span.completion_tokens,
        "total_tokens": span.total_tokens,
        "metadata": span.metadata or {},
        "tags": span.tags or [],
    }

    sa = span.span_attributes or {}
    is_voice = (
        span.observation_type == "conversation"
        or "vapi.call_id" in sa
        or "provider_transcript" in sa
        or "call_logs" in sa
    )
    if not is_voice:
        return base

    # Voice enrichment — hoist the useful fields.
    base["is_voice"] = True

    # Turn-by-turn transcript. Prefer the clean provider_transcript list
    # (role/content pairs) over the verbose raw_log messages.
    transcript = sa.get("provider_transcript")
    if not isinstance(transcript, list):
        # Fall back to raw_log.messages if present
        raw_log = sa.get("raw_log")
        if isinstance(raw_log, str):
            try:
                raw_log = json.loads(raw_log)
            except Exception:
                raw_log = None
        if isinstance(raw_log, dict):
            msgs = raw_log.get("messages")
            if isinstance(msgs, list):
                # Vapi messages have extra fields (time, secondsFromStart);
                # normalize to {role, content} for template use.
                transcript = [
                    {
                        "role": m.get("role"),
                        "content": m.get("message") or m.get("content"),
                    }
                    for m in msgs
                    if m.get("role") in ("user", "assistant", "bot", "system")
                ]
            else:
                transcript = None

    if isinstance(transcript, list) and transcript:
        base["transcript"] = transcript
        # Derive meaningful input/output from the transcript when the
        # top-level span.input/output are empty (Vapi leaves them null).
        if not base.get("input"):
            _first_user = next(
                (t.get("content") for t in transcript if t.get("role") in ("user",)),
                None,
            )
            if _first_user:
                base["input"] = _first_user
        if not base.get("output"):
            _last_asst = next(
                (
                    t.get("content")
                    for t in reversed(transcript)
                    if t.get("role") in ("assistant", "bot")
                ),
                None,
            )
            if _last_asst:
                base["output"] = _last_asst

    # Recording URLs — look in raw_log first, then flat attributes.
    raw_log = sa.get("raw_log")
    if isinstance(raw_log, str):
        try:
            raw_log = json.loads(raw_log)
        except Exception:
            raw_log = {}
    if not isinstance(raw_log, dict):
        raw_log = {}

    # Prefer the S3-mirrored flat alias over the raw ingest snapshot.
    base["recording_url"] = (
        sa.get("recording_url")
        or sa.get("recordingUrl")
        or (raw_log.get("artifact") or {})
        .get("recording", {})
        .get("mono", {})
        .get("combinedUrl")
        or raw_log.get("recordingUrl")
        or raw_log.get("recording_url")
    )
    base["stereo_recording_url"] = (
        sa.get("stereo_recording_url")
        or (raw_log.get("artifact") or {}).get("recording", {}).get("stereoUrl")
        or raw_log.get("stereoRecordingUrl")
        or raw_log.get("stereo_recording_url")
    )

    # Call-level fields that are commonly referenced in voice evals.
    base["call_status"] = sa.get("call.status") or raw_log.get("status")
    base["duration_seconds"] = (
        sa.get("call.duration")
        or raw_log.get("durationSeconds")
        or raw_log.get("duration_seconds")
    )
    base["ended_reason"] = sa.get("ended_reason") or raw_log.get("endedReason")
    base["provider_call_id"] = sa.get("vapi.call_id") or raw_log.get("id")
    base["provider_summary"] = raw_log.get("summary")

    # Metrics: WPM, interruptions, talk ratio, turn count
    base["metrics"] = {
        "turn_count": sa.get("call.total_turns"),
        "talk_ratio": sa.get("call.talk_ratio"),
        "user_wpm": sa.get("call.user_wpm"),
        "bot_wpm": sa.get("call.bot_wpm"),
        "user_interruptions": sa.get("numUserInterrupted"),
        "ai_interruption_rate": sa.get("ai_interruption_rate"),
        "avg_agent_latency_ms": sa.get("avg_agent_latency_ms"),
        "turn_latency_avg": sa.get("turnLatencyAverage"),
    }

    return base
