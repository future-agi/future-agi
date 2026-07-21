import json
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
        "cost": log.get("call_cost", {}).get("combined_cost"),
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

    # Populating call cost
    call_cost_object = log.get("call_cost", {})
    product_costs = call_cost_object.get("product_costs", [])
    for i, cost in enumerate(product_costs):
        call_cost_object[f"product_costs.{i}.product"] = cost.get("product")
        call_cost_object[f"product_costs.{i}.unit_price"] = cost.get("unit_price")
        call_cost_object[f"product_costs.{i}.cost"] = cost.get("cost")

    del call_cost_object["product_costs"]

    flattened_call_cost_object = flatten_dict(call_cost_object)
    eval_attributes.update(flattened_call_cost_object)

    # Populating llm token usages
    llm_token_usage_object = log.get("llm_token_usage")
    if llm_token_usage_object:
        values = llm_token_usage_object.get("values")
        for i, value in enumerate(values):
            llm_token_usage_object[f"llm_token_usage.{i}"] = value

        del log["llm_token_usage"]
        flattened_llm_token_usage_object = flatten_dict(llm_token_usage_object)
        eval_attributes.update(flattened_llm_token_usage_object)

    # Populating metadata
    eval_attributes["metadata"] = log.get("metadata", {})
    eval_attributes["latency"] = log.get("latency")


def _extract_eval_attributes(log: dict) -> dict:
    """Extracts and flattens evaluation attributes from a Retell AI log."""
    eval_attributes = {
        SpanAttributes.SPAN_KIND: "conversation",
        "raw_log": log,
    }
    _process_transcript(log, eval_attributes)
    _extract_recording_urls(log, eval_attributes)
    _extract_metadata(log, eval_attributes)
    _extract_common_call_fields(log, eval_attributes)
    return eval_attributes


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
    """Extracts provider-agnostic call fields into eval_attributes."""
    # total_number_of_turns
    transcript = log.get("transcript_with_tool_calls", [])
    if isinstance(transcript, list):
        eval_attributes[CallAttributes.TOTAL_TURNS] = sum(
            1 for msg in transcript if msg.get("role") in ("user", "agent")
        )
    else:
        eval_attributes[CallAttributes.TOTAL_TURNS] = 0

    # total_call_duration (seconds, int) — matches duration_seconds in API response
    try:
        if "start_timestamp" in log and "end_timestamp" in log:
            duration_ms = log["end_timestamp"] - log["start_timestamp"]
            eval_attributes[CallAttributes.DURATION] = int(duration_ms / 1000)
        else:
            eval_attributes[CallAttributes.DURATION] = None
    except (ValueError, TypeError):
        eval_attributes[CallAttributes.DURATION] = None

    # participant_phone_number (based on call direction)
    direction = log.get("direction", "")
    if direction == "outbound":
        eval_attributes[CallAttributes.PARTICIPANT_PHONE_NUMBER] = log.get("to_number")
    elif direction == "inbound":
        eval_attributes[CallAttributes.PARTICIPANT_PHONE_NUMBER] = log.get(
            "from_number"
        )
    else:
        eval_attributes[CallAttributes.PARTICIPANT_PHONE_NUMBER] = log.get(
            "from_number"
        ) or log.get("to_number")

    # call_status (raw provider status)
    eval_attributes[CallAttributes.STATUS] = log.get("call_status")
    eval_attributes[CallAttributes.USER_WPM] = None
    eval_attributes[CallAttributes.BOT_WPM] = None
    eval_attributes[CallAttributes.TALK_RATIO] = None
