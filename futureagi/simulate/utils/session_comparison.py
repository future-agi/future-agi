import json
from collections import ChainMap
from datetime import datetime
from typing import List

from simulate.models import CallExecution
from simulate.models.chat_message import ChatMessageModel
from simulate.pydantic_schemas.chat import ChatRole
from simulate.serializers.chat_message import ChatMessageSerializer
from tracer.models.trace import Trace
from tracer.utils.otel import CallAttributes, ConversationAttributes

RECORDING_ATTR_KEYS = {
    "stereo": f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}",
    "mono_combined": f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}",
    "mono_customer": f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_CUSTOMER}",
    "mono_assistant": f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_ASSISTANT}",
}


def fetch_base_session_metrics(session_id: str, *, organization=None):

    if not session_id:
        raise ValueError("Session ID is required")

    # Codex wave-3 P0 (2026-05-26): the legacy ORM path joined through
    # `trace__session=session` which the caller had already org-scoped
    # via `CallExecution.objects.filter(test_execution__organization=)`.
    # The new CH path receives a raw `session_id` (from `Row.metadata`)
    # and queries by id only — a forged/stale Row.metadata could return
    # another tenant's metrics. Verify tenant scope BEFORE the CH read.
    if organization is not None:
        from tracer.models.trace_session import TraceSession

        if not TraceSession.objects.filter(
            id=session_id, project__organization=organization
        ).exists():
            raise ValueError("Session not found in organization scope")

    # Session-level aggregates from ClickHouse via wave-3 reader extension
    # ``CHSpanReader.aggregate_by_session_ids`` (commit 93c5c415f).
    #
    # Original PG query was a single .aggregate() call producing:
    #   {start_time, end_time, total_tokens, total_traces, total_tools_count}
    #
    # The reader returns most of those in one query
    # (span_count, traces_count, tokens, cost, start_time, end_time) but NOT
    # the conditional ``Count(filter=Q(observation_type="tool"))``. For a
    # single session a second narrow CH read via ``count_with_filters`` is
    # cheap and avoids burdening the bulk-rollup helper with a special
    # case (no N+1 — this function takes one session at a time).
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        per_session = reader.aggregate_by_session_ids([str(session_id)])
        tools_count = reader.count_with_filters(
            session_id=str(session_id),
            observation_type="tool",
        )

    agg = per_session.get(str(session_id))
    if not agg or agg["start_time"] is None or agg["end_time"] is None:
        # PG path raised on missing duration via the subsequent subtraction;
        # surface the same ValueError shape here when CH has no spans for
        # the session yet (or the session_id is unknown).
        raise ValueError("Session duration is required")

    base_session_duration = (agg["end_time"] - agg["start_time"]).total_seconds()
    if not base_session_duration:
        raise ValueError("Session duration is required")

    # Reader guarantees non-null ints for tokens/traces_count (sum/uniqExact
    # return 0 on empty groups); count_with_filters returns 0 on no match.
    base_session_metrics = {
        "duration": base_session_duration,
        "tokens": int(agg["tokens"] or 0),
        "turn_count": int(agg["traces_count"] or 0),
        "tools_count": int(tools_count or 0),
    }

    return base_session_metrics


def convert_trace_to_chat_messages(traces: List[Trace]):
    if not traces or len(traces) == 0:
        return []

    chat_messages = []
    for trace in traces:
        input = trace.input
        output = trace.output
        if not input or not output:
            continue

        if not isinstance(input, str):
            input = json.dumps(input)
        if not isinstance(output, str):
            output = json.dumps(output)

        chat_messages.append(
            {
                "role": ChatRole.USER,
                "messages": [input],
                "created_at": trace.created_at,
            }
        )
        chat_messages.append(
            {
                "role": ChatRole.ASSISTANT,
                "messages": [output],
                "created_at": trace.created_at,
            }
        )

    return chat_messages


def fetch_call_execution_metrics(call_execution: CallExecution):

    call_execution_metrics = (
        call_execution.conversation_metrics_data
        if call_execution.conversation_metrics_data
        else {}
    )
    latency_ms = call_execution_metrics.get("avg_latency_ms", 0)
    tokens = call_execution_metrics.get("output_tokens", 0)
    turn_count = call_execution_metrics.get("turn_count", 0)

    tool_call_metrics = fetch_tool_calls(call_execution, ChatRole.ASSISTANT)

    if latency_ms > 0:
        latency_ms = latency_ms / 1000

    call_execution_metrics = {
        "duration": latency_ms,
        "turn_count": turn_count,
        "tokens": tokens,
        "tools_count": tool_call_metrics.get("no_of_tool_calls", 0),
    }

    return call_execution_metrics


def fetch_tool_calls(call_execution: CallExecution, role: ChatRole):

    chat_messages = ChatMessageModel.objects.filter(
        call_execution=call_execution, role=role, deleted=False
    ).order_by("created_at")

    no_of_tool_calls = 0
    tool_calls = []

    for chat_message in chat_messages:
        content = chat_message.content
        if content and len(content) > 0:

            for item in content:
                if (
                    item.get("role") == role
                    and item.get("tool_calls")
                    and len(item.get("tool_calls")) > 0
                ):
                    no_of_tool_calls += len(item.get("tool_calls"))
                    tool_calls.extend(item.get("tool_calls"))

    return {
        "no_of_tool_calls": no_of_tool_calls,
        "tool_calls": tool_calls,
    }


def fetch_base_session_transcripts(session_id: str):
    if not session_id:
        raise ValueError("Session ID is required")

    traces = Trace.objects.filter(session_id=session_id, deleted=False).order_by(
        "created_at"
    )
    base_session_transcripts = convert_trace_to_chat_messages(traces)
    return base_session_transcripts


def fetch_call_execution_transcripts(call_execution: CallExecution):
    if not call_execution:
        raise ValueError("Call execution is required")

    chat_messages = ChatMessageModel.objects.filter(
        call_execution=call_execution,
        role__in=[ChatRole.USER, ChatRole.ASSISTANT],
        deleted=False,
    ).order_by("created_at")
    chat_messages = ChatMessageSerializer(chat_messages, many=True).data

    return chat_messages


def fetch_comparison_transcripts(call_execution: CallExecution, session_id: str):
    if not call_execution or not session_id:
        raise ValueError("Call execution and session ID are required")

    base_session_transcripts = fetch_base_session_transcripts(session_id)
    comparison_call_transcripts = fetch_call_execution_transcripts(call_execution)

    return {
        "base_session_transcripts": base_session_transcripts,
        "comparison_call_transcripts": comparison_call_transcripts,
    }


def _build_metric_comparisons(
    base_metrics: dict, comparison_metrics: dict
) -> list[dict]:
    """Compute percentage-change comparisons between base and comparison metric dicts."""
    result = []
    for key in comparison_metrics:
        base_value = base_metrics.get(key)
        comparison_value = comparison_metrics.get(key)
        if base_value is None or comparison_value is None:
            continue
        change = comparison_value - base_value
        pct = None if base_value == 0 else change / base_value * 100
        result.append(
            {
                "metric": key,
                "value": comparison_value,
                "percentage_change": pct,
                "change": change,
            }
        )
    return result


def fetch_comparison_metrics(call_execution: CallExecution, session_id: str):

    if not call_execution or not session_id:
        raise ValueError("Call execution and session ID are required")

    # Codex wave-3 P0: thread the call_execution's organization through to
    # the session lookup so the CH read is tenant-scoped. The CallExecution
    # is the caller's org-scoped anchor (test_execution__organization gate
    # upstream); the session_id comes from Row.metadata which is untrusted.
    organization = getattr(
        getattr(call_execution, "test_execution", None), "organization", None
    )
    base_session_metrics = fetch_base_session_metrics(
        session_id, organization=organization
    )
    comparison_call_metrics = fetch_call_execution_metrics(call_execution)

    return _build_metric_comparisons(base_session_metrics, comparison_call_metrics)


def fetch_voice_conversation_span(trace_id: str) -> dict:
    """
    Fetch the conversation span for a voice trace via ClickHouse.
    Returns a dict with span_attributes and eval_attributes (legacy shape).

    Voice traces have at most a handful of conversation spans (typically one
    per trace), so list_by_trace + Python filter is bounded. `eval_attributes`
    was a PG-only JSONField; in CH it round-trips through `attributes_extra`
    (see tracer/services/clickhouse/v2/adapter.py:330). We reconstruct the
    same dict shape downstream callers expect.
    """
    if not trace_id:
        raise ValueError("Trace ID is required")

    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        spans = reader.list_by_trace(str(trace_id))

    conversation_span = next(
        (s for s in spans if s.observation_type == "conversation"), None
    )
    if conversation_span is None:
        raise ValueError(f"No conversation span found for trace {trace_id}")

    # Legacy callers expect a dict shaped like
    # {"span_attributes": {...}, "eval_attributes": {...}}.
    # CH's `span_attributes` is the merge of attrs_string/number/bool +
    # attributes_extra (without the round-tripped eval_attributes /
    # model_parameters keys, which live alongside in attributes_extra).
    extra = type(reader).attributes_extra_as_dict(conversation_span)
    # Defensive: attributes_extra_as_dict can yield a non-dict if the
    # underlying CH column is a raw String (schema 013) rather than typed
    # JSON. Treat anything that's not a dict as no overflow data.
    if not isinstance(extra, dict):
        extra = {}
    span_attributes: dict = {}
    span_attributes.update(conversation_span.attrs_string or {})
    span_attributes.update(conversation_span.attrs_number or {})
    span_attributes.update(conversation_span.attrs_bool or {})
    # Overflow keys that aren't the round-tripped PG JSONField columns are
    # part of the original span_attributes; keep them merged in.
    _ROUND_TRIP_COLS = ("model_parameters", "input_images", "eval_input", "eval_attributes")
    for k, v in extra.items():
        if k not in _ROUND_TRIP_COLS:
            span_attributes[k] = v

    eval_attributes = extra.get("eval_attributes") or {}

    return {
        "span_attributes": span_attributes,
        "eval_attributes": eval_attributes,
    }


def merge_span_attrs(span: dict) -> ChainMap:
    """Merge span_attributes and eval_attributes, with span_attributes taking precedence.

    Returns a ChainMap view over both dicts (no copying).
    span_attributes is first (where new voice metrics live),
    eval_attributes is fallback (legacy/backfilled data).
    """
    span_attrs = span["span_attributes"] or {}
    eval_attrs = span["eval_attributes"] or {}
    return ChainMap(span_attrs, eval_attrs)


def fetch_voice_trace_baseline_metrics(trace_id: str, _span: dict | None = None):
    """
    Fetch baseline metrics from a voice trace's conversation span.
    Accepts an optional pre-fetched span to avoid redundant DB queries.
    """
    span = _span or fetch_voice_conversation_span(trace_id)
    attrs = merge_span_attrs(span)
    return _extract_voice_metrics_from_attrs(attrs)


def _extract_voice_metrics_from_attrs(attrs: dict) -> dict:
    """Extract voice-specific call metrics from span attributes."""
    return {
        "duration": attrs.get(CallAttributes.DURATION, 0) or 0,
        "total_turns": attrs.get(CallAttributes.TOTAL_TURNS, 0) or 0,
        "avg_agent_latency_ms": attrs.get("avg_agent_latency_ms", 0) or 0,
        "user_wpm": attrs.get(CallAttributes.USER_WPM, 0) or 0,
        "bot_wpm": attrs.get(CallAttributes.BOT_WPM, 0) or 0,
        "talk_ratio": attrs.get(CallAttributes.TALK_RATIO, 0) or 0,
    }


_TOOL_ROLES = {"tool", "tool_calls", "tool_call_result"}


def _is_tool_call_message(msg: dict) -> bool:
    """Return True if the message represents a tool call or tool call result."""
    if msg.get("role", "") in _TOOL_ROLES:
        return True
    if msg.get("toolCalls") or msg.get("tool_calls"):
        return True
    return False


def parse_voice_span_transcripts(attrs: dict) -> list[dict]:
    """
    Parse transcripts from a voice span's attributes dict.
    Tries provider_transcript -> flattened keys -> raw_log.messages.
    Returns list of {role, messages} dicts.
    """
    provider_transcript = attrs.get("provider_transcript", [])
    if not isinstance(provider_transcript, list):
        provider_transcript = []

    transcripts = []

    if provider_transcript:
        for msg in provider_transcript:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if (
                role in ("user", "assistant")
                and content
                and not _is_tool_call_message(msg)
            ):
                transcripts.append({"role": role, "messages": [content]})

    # Fallback: read from flattened conversation.transcript.{i} keys
    if not transcripts or all(t["role"] == "user" for t in transcripts):
        flattened = []
        i = 0
        while True:
            role_key = f"conversation.transcript.{i}.message.role"
            content_key = f"conversation.transcript.{i}.message.content"
            role = attrs.get(role_key)
            content = attrs.get(content_key)
            if role is None:
                break
            if role in ("user", "assistant") and content:
                flattened.append({"role": role, "messages": [content]})
            i += 1
        if flattened and not all(t["role"] == "user" for t in flattened):
            transcripts = flattened

    # Final fallback: read from raw_log.messages (full Vapi/Retell response)
    if not transcripts or all(t["role"] == "user" for t in transcripts):
        raw_log = attrs.get("raw_log", {})
        raw_messages = raw_log.get("messages", []) if isinstance(raw_log, dict) else []
        if raw_messages:
            from_raw = []
            for msg in raw_messages:
                role = msg.get("role", "")
                content = msg.get("message", "")
                if (
                    role in ("user", "assistant", "bot")
                    and content
                    and not _is_tool_call_message(msg)
                ):
                    from_raw.append(
                        {
                            "role": "assistant" if role == "bot" else role,
                            "messages": [content],
                        }
                    )
            if from_raw:
                transcripts = from_raw

    return transcripts


def fetch_voice_trace_baseline_transcripts(trace_id: str, _span: dict | None = None):
    """
    Fetch baseline transcripts from a voice trace's provider_transcript.
    Accepts an optional pre-fetched span to avoid redundant DB queries.
    """
    try:
        span = _span or fetch_voice_conversation_span(trace_id)
    except ValueError:
        return []

    attrs = merge_span_attrs(span)
    return parse_voice_span_transcripts(attrs)


def _extract_metrics_from_provider_call_data(call_execution: CallExecution):
    """
    Extract voice-specific metrics from a simulated CallExecution.
    These are stored as model fields, matching the baseline span attributes.
    """
    # Duration from provider timestamps
    provider_data = call_execution.provider_call_data or {}
    vapi_data = provider_data.get("vapi", {})
    duration = 0
    created_at = vapi_data.get("startedAt") or vapi_data.get("createdAt")
    ended_at = vapi_data.get("endedAt")
    if created_at and ended_at:
        try:
            start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration = (end - start).total_seconds()
        except (ValueError, TypeError):
            pass
    # Provider-agnostic fallback (Bland, etc.): duration_seconds is populated on
    # the model for every provider during fetch, so comparison isn't VAPI-only.
    if not duration:
        duration = call_execution.duration_seconds or 0

    # Count turns from messages
    messages = vapi_data.get("messages", [])
    total_turns = 0
    if isinstance(messages, list):
        total_turns = sum(
            1 for m in messages if m.get("role") in ("user", "assistant", "bot")
        )
    if not total_turns:
        # CallTranscript rows are written for every provider.
        from simulate.models.test_execution import CallTranscript

        total_turns = CallTranscript.objects.filter(
            call_execution=call_execution,
            speaker_role__in=[
                CallTranscript.SpeakerRole.USER,
                CallTranscript.SpeakerRole.ASSISTANT,
            ],
        ).count()

    return {
        "duration": duration,
        "total_turns": total_turns,
        "avg_agent_latency_ms": call_execution.avg_agent_latency_ms or 0,
        "user_wpm": call_execution.user_wpm or 0,
        "bot_wpm": call_execution.bot_wpm or 0,
        "talk_ratio": call_execution.talk_ratio or 0,
    }


def fetch_voice_trace_comparison_metrics(
    call_execution: CallExecution, trace_id: str, _span: dict | None = None
):
    """Compare a simulated call execution against a voice trace baseline."""
    if not call_execution or not trace_id:
        raise ValueError("Call execution and trace ID are required")

    base_metrics = fetch_voice_trace_baseline_metrics(trace_id, _span=_span)
    comparison_metrics = _extract_metrics_from_provider_call_data(call_execution)

    return _build_metric_comparisons(base_metrics, comparison_metrics)


def _transcripts_from_call_transcript_rows(call_execution: CallExecution):
    """Provider-agnostic transcripts from the persisted CallTranscript rows.

    These are written for every voice provider (VAPI, Bland, ...) and ordered by
    start_time_ms, so a provider whose raw payload isn't VAPI/Retell-shaped
    (e.g. Bland) still compares against a baseline instead of rendering blank.
    """
    from simulate.models.test_execution import CallTranscript

    role_map = {
        CallTranscript.SpeakerRole.ASSISTANT: "assistant",
        CallTranscript.SpeakerRole.USER: "user",
    }
    transcripts = []
    rows = CallTranscript.objects.filter(call_execution=call_execution).order_by(
        "start_time_ms"
    )
    for row in rows:
        display_role = role_map.get(row.speaker_role)
        content = (row.content or "").strip()
        if display_role and content:
            transcripts.append({"role": display_role, "messages": [content]})
    return transcripts


def _extract_transcripts_from_provider_call_data(call_execution: CallExecution):
    """
    Extract transcripts from provider_call_data for voice call executions.
    Voice simulations store the Vapi/Retell response in provider_call_data,
    not in ChatMessageModel.
    """
    provider_data = call_execution.provider_call_data or {}

    # Try Vapi format
    vapi_data = provider_data.get("vapi", {})
    messages = vapi_data.get("messages", [])

    if messages:
        transcripts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("message", "")
            if (
                role in ("user", "assistant", "bot")
                and content
                and not _is_tool_call_message(msg)
            ):
                transcripts.append(
                    {
                        "role": "assistant" if role == "bot" else role,
                        "messages": [content],
                    }
                )
        return transcripts

    # Try Retell format
    retell_data = provider_data.get("retell", {})
    transcript = retell_data.get("transcript", "") or retell_data.get(
        "transcript_with_tool_calls", []
    )

    # Guard on retell_data so a provider without a retell payload (e.g. Bland,
    # whose empty transcript is still a list) falls through to the CallTranscript
    # fallback instead of returning an empty list here.
    if retell_data and isinstance(transcript, list):
        transcripts = []
        for entry in transcript:
            role = entry.get("role", "")
            content = entry.get("content", "")
            if role in ("user", "agent", "assistant") and content:
                transcripts.append(
                    {
                        "role": "assistant" if role == "agent" else role,
                        "messages": [content],
                    }
                )
        return transcripts

    # Provider-agnostic fallback (Bland, etc.): read the persisted transcript.
    return _transcripts_from_call_transcript_rows(call_execution)


def fetch_voice_trace_comparison_transcripts(
    call_execution: CallExecution, trace_id: str, _span: dict | None = None
):
    """Compare transcripts between a voice trace baseline and a simulated call."""
    if not call_execution or not trace_id:
        raise ValueError("Call execution and trace ID are required")

    base_transcripts = fetch_voice_trace_baseline_transcripts(trace_id, _span=_span)

    # Voice calls store transcripts in provider_call_data, not ChatMessageModel
    comparison_transcripts = _extract_transcripts_from_provider_call_data(
        call_execution
    )
    if not comparison_transcripts:
        # Fallback to ChatMessageModel (in case it's a text-type replay)
        comparison_transcripts = fetch_call_execution_transcripts(call_execution)

    return {
        "base_session_transcripts": base_transcripts,
        "comparison_call_transcripts": comparison_transcripts,
    }


def fetch_baseline_trace_recordings(trace_id: str, _span: dict | None = None) -> dict:
    """Return recording URLs from a baseline voice trace's span attributes."""
    try:
        span = _span or fetch_voice_conversation_span(trace_id)
    except ValueError:
        return {}

    sa = span["span_attributes"] or {}
    recordings = {}
    for label, attr_key in RECORDING_ATTR_KEYS.items():
        if url := sa.get(attr_key):
            recordings[label] = url
    return recordings


def fetch_simulated_call_recordings(call_execution: CallExecution) -> dict:
    """Recording URLs for a simulated call: model fields first, provider_call_data fallback."""
    model_recordings = _fallback_model_recordings(call_execution)

    provider_data = call_execution.provider_call_data
    payload = None
    if isinstance(provider_data, dict):
        if len(provider_data) == 1:
            payload = next(iter(provider_data.values()))
        else:
            payload = provider_data.get("vapi", {})
    if not isinstance(payload, dict):
        payload = {}

    recording = (
        (payload.get("artifact") or {}).get("recording") or payload.get("recording") or {}
    )
    if not isinstance(recording, dict):
        recording = {}

    mono = recording.get("mono") or {}
    recordings: dict[str, str] = {}
    if combined := model_recordings.get("mono_combined") or mono.get("combinedUrl"):
        recordings["mono_combined"] = combined
    if stereo := model_recordings.get("stereo") or recording.get("stereoUrl"):
        recordings["stereo"] = stereo
    if customer_url := mono.get("customerUrl"):
        recordings["mono_customer"] = customer_url
    if assistant_url := mono.get("assistantUrl"):
        recordings["mono_assistant"] = assistant_url

    return recordings


def _fallback_model_recordings(call_execution: CallExecution) -> dict:
    """Read the durable S3-mirrored recording URLs off the model."""
    recordings = {}
    if stereo := call_execution.stereo_recording_url:
        recordings["stereo"] = stereo
    if mono := call_execution.recording_url:
        recordings["mono_combined"] = mono
    return recordings


def fetch_comparison_recordings(
    call_execution: CallExecution, trace_id: str, _span: dict | None = None
) -> dict:
    """Fetch recording URLs for both baseline and simulated calls."""
    return {
        "baseline": fetch_baseline_trace_recordings(trace_id, _span=_span),
        "simulated": fetch_simulated_call_recordings(call_execution),
    }
