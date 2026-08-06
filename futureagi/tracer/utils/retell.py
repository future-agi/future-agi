import json
import math
from datetime import UTC, datetime, timedelta

from simulate.temporal.utils.async_storage import convert_audio_url_to_s3_sync
from tracer.utils.helper import flatten_dict
from tracer.utils.otel import (
    CallAttributes,
    ConversationAttributes,
    MessageAttributes,
    SpanAttributes,
)
from tracer.utils.vapi_recording import VapiRecordingService

_RETELL_RECORDING_KEY_BY_ARTIFACT_TYPE = {
    "mono_combined": (
        f"{ConversationAttributes.CONVERSATION_RECORDING}."
        f"{ConversationAttributes.MONO_COMBINED}"
    ),
    "stereo": (
        f"{ConversationAttributes.CONVERSATION_RECORDING}."
        f"{ConversationAttributes.STEREO}"
    ),
}


def normalize_retell_data(log: dict, *, project_id: str | None = None) -> dict:
    """
    Normalizes a single log entry from Retell AI into a structured format.
    """
    status = _map_status(log.get("call_status", ""))
    start_time, end_time = _extract_timestamps(log)
    eval_attributes = _extract_eval_attributes(log)
    rehost_uploads = _rehost_recording_urls_sync(
        log, eval_attributes, project_id=project_id
    )

    prompt_tokens = eval_attributes.get(SpanAttributes.USAGE_INPUT_TOKENS)
    completion_tokens = eval_attributes.get(SpanAttributes.USAGE_OUTPUT_TOKENS)
    total_tokens = eval_attributes.get(SpanAttributes.USAGE_TOTAL_TOKENS)

    return {
        "id": log.get("call_id"),
        "start_time": start_time,
        "end_time": end_time,
        "cost": (log.get("call_cost") or {}).get("combined_cost"),
        "status": status,
        "metadata": log,
        "span_attributes": eval_attributes,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "rehost_bytes_uploaded": sum(rehost_uploads.values()),
        "rehost_uploads": rehost_uploads,
    }


def _rehost_recording_urls_sync(
    log: dict, eval_attributes: dict, *, project_id: str | None
) -> dict[str, int]:
    """Best-effort inline rehost of Retell recordings to configured storage.

    Retell does not have an authenticated artifact-download endpoint, so its
    provider URLs are passed directly to the shared converter. Each artifact
    is isolated: a failure for one recording leaves that source URL in place
    while the remaining recordings can still be rehosted.
    """
    if not project_id:
        return {}

    call_id = log.get("call_id") if isinstance(log, dict) else None
    if not call_id:
        return {}

    bytes_by_artifact_type: dict[str, int] = {}
    for artifact_type, key in _RETELL_RECORDING_KEY_BY_ARTIFACT_TYPE.items():
        source_url = eval_attributes.get(key)
        if not source_url or VapiRecordingService.is_fagi_s3_url(source_url):
            continue

        try:
            durable_url, artifact_bytes = convert_audio_url_to_s3_sync(
                call_id=call_id,
                audio_url=source_url,
                url_type=artifact_type,
                provider="retell",
                artifact_type=artifact_type,
                project_id=project_id,
            )
        except Exception:
            # Rehosting is best-effort; retaining Retell's URL makes a later
            # poll eligible to retry this artifact.
            continue

        if durable_url and durable_url != source_url:
            eval_attributes[key] = durable_url
            bytes_by_artifact_type[artifact_type] = artifact_bytes

    return bytes_by_artifact_type


def _map_status(call_status: str) -> str:
    """Maps Retell's call status to the convention used in ObservationSpan."""
    if call_status == "ended":
        return "ok"
    elif call_status == "error":
        return "error"
    return "unset"


def _extract_timestamps(log: dict) -> tuple:
    """Extracts start and end timestamps from a Retell AI log."""
    start_time = (
        datetime.fromtimestamp(log["start_timestamp"] / 1000, tz=UTC)
        if "start_timestamp" in log
        else None
    )
    end_time = (
        datetime.fromtimestamp(log["end_timestamp"] / 1000, tz=UTC)
        if "end_timestamp" in log
        else None
    )
    return start_time, end_time


def _extract_metadata(log: dict, eval_attributes: dict):
    # Populating Ended Reason
    eval_attributes["ended_reason"] = (
        log.get("disconnection_reason")
        if log and log.get("disconnection_reason")
        else None
    )

    # Flatten cost fields without mutating the provider response kept in raw_log.
    call_cost_object = dict(log.get("call_cost") or {})
    product_costs = call_cost_object.pop("product_costs", []) or []
    for i, cost in enumerate(product_costs):
        eval_attributes[f"product_costs.{i}.product"] = cost.get("product")
        eval_attributes[f"product_costs.{i}.unit_price"] = cost.get("unit_price")
        eval_attributes[f"product_costs.{i}.cost"] = cost.get("cost")
    eval_attributes.update(flatten_dict(call_cost_object))

    # Keep the full token usage payload in raw_log and expose scalar totals
    # through the standard GenAI usage attributes.
    usage = log.get("llm_token_usage") or {}
    values = _numeric_values(usage.get("values"))
    if values:
        eval_attributes[SpanAttributes.USAGE_TOTAL_TOKENS] = sum(values)
    input_tokens = _first_number(
        usage.get("input_tokens"),
        usage.get("prompt_tokens"),
        usage.get("input"),
    )
    output_tokens = _first_number(
        usage.get("output_tokens"),
        usage.get("completion_tokens"),
        usage.get("output"),
    )
    if input_tokens is not None:
        eval_attributes[SpanAttributes.USAGE_INPUT_TOKENS] = input_tokens
    if output_tokens is not None:
        eval_attributes[SpanAttributes.USAGE_OUTPUT_TOKENS] = output_tokens
    if input_tokens is not None and output_tokens is not None:
        eval_attributes[SpanAttributes.USAGE_TOTAL_TOKENS] = (
            input_tokens + output_tokens
        )
    if usage:
        eval_attributes.update(flatten_dict({"llm_token_usage": usage}))
        for index, value in enumerate(values):
            eval_attributes[f"llm_token_usage.{index}"] = value

    eval_attributes["metadata"] = log.get("metadata", {})
    eval_attributes["latency"] = log.get("latency")


def _extract_latency_metrics(log: dict, eval_attributes: dict):
    latency = log.get("latency") or {}
    e2e = latency.get("e2e") if isinstance(latency, dict) else None
    e2e = e2e if isinstance(e2e, dict) else {}
    values = _numeric_values(e2e.get("values"))
    average = sum(values) / len(values) if values else None
    if average is None:
        total = _first_number(e2e.get("sum"))
        count = _first_number(e2e.get("num"))
        if total is not None and count:
            average = total / count
    if average is not None:
        eval_attributes["avg_agent_latency_ms"] = average


def _extract_eval_attributes(log: dict) -> dict:
    """Extracts and flattens evaluation attributes from a Retell AI log."""
    eval_attributes = {
        SpanAttributes.SPAN_KIND: "conversation",
        "raw_log": log,
    }
    _process_transcript(log, eval_attributes)
    _extract_recording_urls(log, eval_attributes)
    _extract_metadata(log, eval_attributes)
    _extract_latency_metrics(log, eval_attributes)
    _extract_common_call_fields(log, eval_attributes)
    return eval_attributes


def _numeric_values(values) -> list[float]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        number = _first_number(value)
        if number is not None:
            result.append(number)
    return result


def _first_number(*values):
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _process_transcript(log: dict, eval_attributes: dict):
    """Processes the transcript to extract conversation and tool call data."""
    transcript = log.get("transcript_with_tool_calls")
    if not (transcript and isinstance(transcript, list)):
        return

    tool_call_index = 0
    eval_attributes["provider_transcript"] = []
    for i, msg in enumerate(transcript):
        role = msg.get("role")
        if role == "tool_call_invocation":
            _process_tool_call(msg, eval_attributes, tool_call_index)
            tool_call_index += 1
        else:
            _process_conversation_message(msg, eval_attributes, i)


def _process_tool_call(msg: dict, eval_attributes: dict, index: int):
    """Processes a tool call message and adds it to eval_attributes."""
    tool_call_data = {}

    if tool_call_id := msg.get("tool_call_id"):
        key = f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{index}.tool_calls.0.tool_call.id"
        eval_attributes[key] = tool_call_id
        tool_call_data["tool_call_id"] = tool_call_id
    if name := msg.get("name"):
        key = f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{index}.tool_calls.0.tool_call.function.name"
        eval_attributes[key] = name
        tool_call_data["name"] = name
    if arguments := msg.get("arguments"):
        key = f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{index}.tool_calls.0.tool_call.function.arguments"
        try:
            eval_attributes[key] = json.loads(arguments)
            tool_call_data["arguments"] = json.loads(arguments)
        except json.JSONDecodeError:
            eval_attributes[key] = arguments

    if len(tool_call_data) > 0:
        eval_attributes["provider_transcript"].append(tool_call_data)


def _process_conversation_message(msg: dict, eval_attributes: dict, index: int):
    """Processes a regular conversation message."""
    if role := msg.get("role"):
        eval_attributes[
            f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{index}.{MessageAttributes.MESSAGE_ROLE}"
        ] = role
    if content := msg.get("content"):
        eval_attributes[
            f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{index}.{MessageAttributes.MESSAGE_CONTENT}"
        ] = content

    transcript_exists = msg.get("words") and len(msg.get("words")) > 0
    seconds_from_start = None
    end_time = None
    duration = None

    if transcript_exists:
        words = msg.get("words")
        seconds_from_start = words[0].get("start")
        end_time = words[-1].get("end")
        start_timedelta = timedelta(seconds=seconds_from_start)
        end_timedelta = timedelta(seconds=end_time)
        duration = end_timedelta - start_timedelta

    duration = round(duration.total_seconds(), 2) if duration else None
    seconds_from_start = round(seconds_from_start, 2) if seconds_from_start else None

    eval_attributes[
        f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{index}.{MessageAttributes.MESSAGE_DURATION}"
    ] = duration

    if not eval_attributes.get("provider_transcript"):
        eval_attributes["provider_transcript"] = []

    eval_attributes["provider_transcript"].append(
        {
            "role": role,
            "content": content,
        }
    )


def _extract_recording_urls(log: dict, eval_attributes: dict):
    """Extracts recording URLs and adds them to eval_attributes."""
    if recording_url := log.get("recording_url"):
        eval_attributes[
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}"
        ] = recording_url

    if multi_channel_url := log.get("recording_multi_channel_url"):
        eval_attributes[
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}"
        ] = multi_channel_url


def _extract_common_call_fields(log: dict, eval_attributes: dict):
    """Extract provider-agnostic call fields and transcript-derived metrics."""
    transcript = log.get("transcript_with_tool_calls", [])
    conversation_messages = (
        [
            message
            for message in transcript
            if isinstance(message, dict)
            and message.get("role") in ("user", "agent", "assistant")
        ]
        if isinstance(transcript, list)
        else []
    )
    eval_attributes[CallAttributes.TOTAL_TURNS] = len(conversation_messages)

    duration_ms = _first_number(log.get("duration_ms"))
    eval_attributes[CallAttributes.DURATION] = (
        int(duration_ms / 1000) if duration_ms is not None else None
    )

    direction = log.get("direction", "")
    if direction == "outbound":
        participant_number = log.get("to_number")
    elif direction == "inbound":
        participant_number = log.get("from_number")
    else:
        participant_number = log.get("from_number") or log.get("to_number")
    eval_attributes[CallAttributes.PARTICIPANT_PHONE_NUMBER] = participant_number
    eval_attributes[CallAttributes.STATUS] = log.get("call_status")

    speech = _speech_metrics(conversation_messages)
    eval_attributes[CallAttributes.USER_WPM] = speech["user_wpm"]
    eval_attributes[CallAttributes.BOT_WPM] = speech["bot_wpm"]
    eval_attributes[CallAttributes.TALK_RATIO] = speech["talk_ratio"]
    eval_attributes["user_interruption_count"] = speech["user_interruption_count"]
    eval_attributes["ai_interruption_count"] = speech["ai_interruption_count"]


def _speech_metrics(messages: list[dict]) -> dict:
    segments = []
    for message in messages:
        words = message.get("words")
        if not isinstance(words, list):
            continue
        timed_words = [
            word
            for word in words
            if isinstance(word, dict)
            and _first_number(word.get("start")) is not None
            and _first_number(word.get("end")) is not None
        ]
        if not timed_words:
            continue
        start = min(_first_number(word["start"]) for word in timed_words)
        end = max(_first_number(word["end"]) for word in timed_words)
        if end <= start:
            continue
        role = "user" if message.get("role") == "user" else "agent"
        segments.append(
            {
                "role": role,
                "start": start,
                "end": end,
                "word_count": len(timed_words),
            }
        )
    segments.sort(key=lambda s: s["start"])

    speech_seconds = {"user": 0.0, "agent": 0.0}
    word_counts = {"user": 0, "agent": 0}
    user_interruptions = 0
    ai_interruptions = 0
    for segment in segments:
        duration = segment["end"] - segment["start"]
        speech_seconds[segment["role"]] += duration
        word_counts[segment["role"]] += segment["word_count"]

    for previous, current in zip(segments, segments[1:], strict=False):
        overlap = previous["end"] - current["start"]
        if previous["role"] == current["role"] or overlap <= 0:
            continue
        if current["role"] == "user":
            user_interruptions += 1
        else:
            ai_interruptions += 1

    def wpm(role: str):
        wpm_seconds = 0.0
        wpm_words = 0
        for segment in segments:
            if segment["role"] != role:
                continue
            duration = segment["end"] - segment["start"]
            if duration < 0.1:
                continue
            wpm_seconds += duration
            wpm_words += segment["word_count"]
        wpm_val = wpm_words * 60 / wpm_seconds if wpm_seconds > 0 else None
        # 300 WPM ceiling: bad word timings can spike rates to absurd values.
        return min(wpm_val, 300) if wpm_val is not None else None

    return {
        "user_wpm": wpm("user"),
        "bot_wpm": wpm("agent"),
        "talk_ratio": (
            speech_seconds["agent"] / speech_seconds["user"]
            if speech_seconds["user"] > 0
            else None
        ),
        "user_interruption_count": user_interruptions,
        "ai_interruption_count": ai_interruptions,
    }
