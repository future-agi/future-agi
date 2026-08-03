import gzip
import io
import json
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

import requests
import structlog

try:
    from ee.voice.services.conversation_metrics import ConversationMetricsCalculator
except ImportError:
    ConversationMetricsCalculator = None
from simulate.temporal.utils.async_storage import convert_audio_url_to_s3_sync
from tracer.utils.helper import flatten_dict

logger = structlog.get_logger(__name__)
from tracer.utils.otel import (
    CallAttributes,
    ConversationAttributes,
    MessageAttributes,
    PerformanceMetrics,
    SpanAttributes,
    TurnLatencyAttributes,
    WorkflowAttributes,
)

metrics_calculator = ConversationMetricsCalculator() if ConversationMetricsCalculator else None

# The four Vapi recording OTel attribute keys that _extract_recording_urls writes.
_VAPI_RECORDING_KEYS: list[str] = [
    f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}",
    f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_CUSTOMER}",
    f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_ASSISTANT}",
    f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}",
]

# Mapping from OTel key tail segment to url-type shorthand used in S3 object keys / billing.
_VAPI_URL_TYPE_BY_KEY: dict[str, str] = {
    ConversationAttributes.MONO_COMBINED: "mono_combined",
    ConversationAttributes.MONO_CUSTOMER: "mono_customer",
    ConversationAttributes.MONO_ASSISTANT: "mono_assistant",
    ConversationAttributes.STEREO: "stereo",
}


def _rehost_recording_urls_sync(
    log: dict,
    eval_attributes: dict,
    *,
    api_key: str | None = None,
    project_id: str | None = None,
) -> tuple[int, dict[str, int]]:
    """Best-effort inline rehost of Vapi recording URLs to FA S3.

    Iterates the 4 Vapi recording keys; for each non-S3 URL calls
    ``convert_audio_url_to_s3_sync`` and replaces in-place on success.
    On failure the original URL is left untouched (best-effort).
    After the loop, mirrors S3 URLs onto flat aliases and propagates
    to ``CallExecution`` / ``CallExecutionSnapshot`` consumer fields
    via ``VapiRecordingService.mirror_s3_url_to_consumer_fields``.

    Returns ``(total_artifact_bytes, bytes_by_url_type)`` where
    ``bytes_by_url_type`` maps each rehosted url-type to its byte count.
    Existing S3 objects contribute their stored size so a failed billing emit
    can be retried with the same idempotency key on a later poll.
    """
    from tracer.utils.vapi_recording import VapiRecordingService

    call_id = log.get("id") if isinstance(log, dict) else None

    s3_url_by_url_type: dict[str, str] = {}
    bytes_by_url_type: dict[str, int] = {}
    prefix = f"{ConversationAttributes.CONVERSATION_RECORDING}."
    total_bytes_uploaded: int = 0

    for key in _VAPI_RECORDING_KEYS:
        url = eval_attributes.get(key)
        if not url:
            continue
        # Skip if already FA S3 (canonical check — matches our own bucket list).
        if VapiRecordingService.is_fagi_s3_url(url):
            continue

        # Derive url_type from the key tail (e.g. "mono.combined" -> "mono_combined")
        if key.startswith(prefix):
            tail = key[len(prefix):]
        else:
            tail = key
        url_type = _VAPI_URL_TYPE_BY_KEY.get(tail)
        if not url_type:
            continue

        artifact_type = VapiRecordingService.artifact_for_url_type(url_type)
        s3_url, bytes_uploaded = convert_audio_url_to_s3_sync(
            call_id=call_id,
            audio_url=url,
            url_type=url_type,
            provider="vapi",
            api_key=api_key,
            artifact_type=artifact_type,
            project_id=project_id,
        )
        # Replace in-place only if we got a different (S3) URL back
        if s3_url and s3_url != url:
            eval_attributes[key] = s3_url
            s3_url_by_url_type[url_type] = s3_url
            total_bytes_uploaded += bytes_uploaded
            bytes_by_url_type[url_type] = bytes_uploaded

    # Mirror S3 URLs onto flat consumer-facing aliases (in-place on eval_attributes)
    mono_s3 = s3_url_by_url_type.get("mono_combined")
    stereo_s3 = s3_url_by_url_type.get("stereo")
    if mono_s3 and not VapiRecordingService.is_fagi_s3_url(eval_attributes.get("recording_url")):
        eval_attributes["recording_url"] = mono_s3
    if stereo_s3 and not VapiRecordingService.is_fagi_s3_url(eval_attributes.get("stereo_recording_url")):
        eval_attributes["stereo_recording_url"] = stereo_s3

    # Mirror to CallExecution / CallExecutionSnapshot (best-effort, import-guarded).
    if s3_url_by_url_type:
        try:
            VapiRecordingService.mirror_s3_url_to_consumer_fields(
                attrs=eval_attributes,
                call_id=call_id,
                s3_url_by_url_type=s3_url_by_url_type,
            )
        except Exception:
            logger.exception(
                "_rehost_recording_urls_sync: mirror_s3_url_to_consumer_fields failed (non-fatal)"
            )

    return total_bytes_uploaded, bytes_by_url_type


def normalize_vapi_data(
    log: dict, *, api_key: str | None = None, project_id: str | None = None
) -> dict:
    """Normalize a Vapi log entry; api_key routes call-logs through the auth endpoint."""
    if not isinstance(log, dict):
        logger.error(
            "normalize_vapi_data: LOG IS NOT A DICT — skipping",
            log_type=type(log).__name__,
        )
        return {"id": None, "span_attributes": {}}
    status = _map_status(log.get("status", ""))
    start_time, end_time = _extract_timestamps(log)
    eval_attributes = _extract_eval_attributes(log, api_key=api_key)

    # Inline best-effort rehost: try to convert Vapi recording URLs to S3;
    # on failure leave the original URL (non-fatal).
    try:
        total_bytes, bytes_by_url_type = _rehost_recording_urls_sync(
            log, eval_attributes, api_key=api_key, project_id=project_id
        )
    except Exception:
        logger.exception("normalize_vapi_data: inline rehost failed (non-fatal)")
        total_bytes, bytes_by_url_type = 0, {}

    prompt_tokens = eval_attributes.get(SpanAttributes.USAGE_INPUT_TOKENS)
    completion_tokens = eval_attributes.get(SpanAttributes.USAGE_OUTPUT_TOKENS)
    total_tokens = eval_attributes.get(SpanAttributes.USAGE_TOTAL_TOKENS)
    latency_ms = eval_attributes.get("avg_agent_latency_ms")

    out = {
        "id": log.get("id"),
        "start_time": start_time,
        "end_time": end_time,
        "cost": log.get("cost"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "status": status,
        "metadata": log.get("metadata"),
        "span_attributes": eval_attributes,
        "rehost_bytes_uploaded": total_bytes,
        # Per-url-type byte counts for idempotent billing across re-polls.
        "rehost_uploads": bytes_by_url_type,
    }
    return out


def _map_status(vapi_status: str) -> str:
    """Maps Vapi's status to the convention used in ObservationSpan."""
    return "ok" if vapi_status == "ended" else "unset"


def _extract_timestamps(log: dict) -> tuple:
    """Extracts start and end timestamps from a Vapi log."""
    start_time = (
        datetime.fromisoformat(log["createdAt"].replace("Z", "+00:00"))
        if "createdAt" in log
        else None
    )
    end_time = (
        datetime.fromisoformat(log["endedAt"].replace("Z", "+00:00"))
        if "endedAt" in log
        else None
    )
    return start_time, end_time


def _extract_eval_attributes(
    log: dict,
    *,
    include_call_logs: bool = True,
    api_key: str | None = None,
) -> dict:
    """Extract and flatten eval attributes from a Vapi log; skip call-logs via include_call_logs=False."""
    if not isinstance(log, dict):
        logger.error(
            "extract_eval_attributes: LOG IS NOT A DICT",
            log_type=type(log).__name__,
        )
        return {}
    eval_attributes = {
        SpanAttributes.SPAN_KIND: "conversation",
        "raw_log": log,
        "vapi.call_id": log.get("id"),
    }
    _extract_llm_and_token_details(log, eval_attributes)
    _extract_conversation(log, eval_attributes)
    _extract_recording_urls(log, eval_attributes)
    _extract_metrics(log, eval_attributes)
    _extract_metadata(log, eval_attributes)
    _extract_common_call_fields(log, eval_attributes)
    if include_call_logs:
        _extract_call_logs(log, eval_attributes, api_key=api_key)
    return eval_attributes


def _extract_llm_and_token_details(log: dict, eval_attributes: dict):
    """Extracts LLM and token details and adds them to eval_attributes."""
    costs = log.get("costs", [])
    if not isinstance(costs, list):
        return

    for cost_item in costs:
        if cost_item.get("type") == "model":
            if model_info := cost_item.get("model"):
                eval_attributes[SpanAttributes.REQUEST_MODEL] = model_info.get("model")
                eval_attributes[SpanAttributes.PROVIDER_NAME] = model_info.get(
                    "provider"
                )
            prompt_tokens = cost_item.get("promptTokens", 0)
            completion_tokens = cost_item.get("completionTokens", 0)
            eval_attributes[SpanAttributes.USAGE_INPUT_TOKENS] = prompt_tokens
            eval_attributes[SpanAttributes.USAGE_OUTPUT_TOKENS] = completion_tokens
            eval_attributes[SpanAttributes.USAGE_TOTAL_TOKENS] = (
                prompt_tokens + completion_tokens
            )
            break  # Assume only one primary model cost item


def _extract_conversation(log: dict, eval_attributes: dict):
    """Extracts and flattens the conversation from the Vapi log."""
    messages = log.get("messages")
    if not (messages and isinstance(messages, list)):
        return

    conversation_index = 0
    eval_attributes["provider_transcript"] = []
    for msg in messages:
        role = msg.get("role")
        start_time = (
            msg.get("secondsFromStart") if msg.get("secondsFromStart") else None
        )
        duration = msg.get("duration") / 1000 if msg.get("duration") else None
        if role in ["user", "assistant", "bot"]:
            # Normalize "bot" to "assistant" for consistent storage
            normalized_role = "assistant" if role == "bot" else role
            key_role = f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{conversation_index}.{MessageAttributes.MESSAGE_ROLE}"
            key_content = f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{conversation_index}.{MessageAttributes.MESSAGE_CONTENT}"
            key_start_time = f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{conversation_index}.{MessageAttributes.MESSAGE_START_TIME}"
            key_duration = f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{conversation_index}.{MessageAttributes.MESSAGE_DURATION}"

            eval_attributes[key_role] = normalized_role
            eval_attributes[key_start_time] = start_time
            eval_attributes[key_duration] = duration
            if content := msg.get("message"):
                eval_attributes[key_content] = content
            eval_attributes["provider_transcript"].append(
                {
                    "role": normalized_role,
                    "content": content,
                }
            )
            conversation_index += 1


def _extract_metadata(log: dict, eval_attributes: dict):
    """
    Extracts the following metadata from 'log' and adds them to 'eval_attributes'

    Args:
        log(dict): The log obtained from Vapi
        eval_attributes(dict): The eval_attributes dict
    """

    # Fetching call ended reason
    ended_reason = log.get("endedReason") if log.get("endedReason") else None
    eval_attributes["ended_reason"] = ended_reason

    # Fetching ids for filters
    eval_attributes["squad.id"] = log.get("squadId")
    eval_attributes["phone_number.id"] = log.get("phoneNumberId")
    eval_attributes["customer.id"] = log.get("customerId")

    # Fetching workflow details
    key_workflow_id = f"{WorkflowAttributes.WORKFLOW_ID}"
    key_workflow_name = f"{WorkflowAttributes.WORKFLOW_NAME}"
    key_background_sound = f"{WorkflowAttributes.WORKFLOW_BACKGROUND_SOUND}"
    key_workflow_voicemail_message = f"{WorkflowAttributes.WORKFLOW_VOICEMAIL_MESSAGE}"
    key_workflow_voicemail_detection = (
        f"{WorkflowAttributes.WORKFLOW_VOICEMAIL_DETECTION}"
    )
    workflow = log.get("workflow")

    eval_attributes[key_workflow_id] = (
        log.get("workflowId") if log.get("workflowId") else None
    )
    eval_attributes[key_workflow_name] = (
        workflow.get("name") if workflow and workflow.get("name") else None
    )
    eval_attributes[key_background_sound] = (
        workflow.get("backgroundSound")
        if workflow and workflow.get("backgroundSound")
        else "off"
    )
    eval_attributes[key_workflow_voicemail_message] = (
        workflow.get("voicemailMessage")
        if workflow and workflow.get("voicemailMessage")
        else None
    )
    eval_attributes[key_workflow_voicemail_detection] = (
        workflow.get("voicemailDetection")
        if workflow and workflow.get("voicemailDetection")
        else None
    )

    # Fetching cost breakdown
    cost_breakdown = log.get("costBreakdown") or {}
    flattened_cost_breakdown = flatten_dict(cost_breakdown, "cost_breakdown")
    eval_attributes.update(flattened_cost_breakdown)

    # Fetching performance metrics
    artifacts = log.get("artifact") or {}
    performance_metrics = artifacts.get("performanceMetrics", {}).copy()
    if performance_metrics.get("turnLatencies") is not None:
        turn_latencies = performance_metrics.get("turnLatencies")

        for i in range(len(turn_latencies)):
            key_model_latency = f"{PerformanceMetrics.TURN_LATENCIES}.{i}.{TurnLatencyAttributes.MODEL_LATENCY}"
            key_voice_latency = f"{PerformanceMetrics.TURN_LATENCIES}.{i}.{TurnLatencyAttributes.VOICE_LATENCY}"
            key_transcriber_latency = f"{PerformanceMetrics.TURN_LATENCIES}.{i}.{TurnLatencyAttributes.TRANSCRIBER_LATENCY}"
            key_endpointing_latency = f"{PerformanceMetrics.TURN_LATENCIES}.{i}.{TurnLatencyAttributes.ENDPOINTING_LATENCY}"
            key_turn_latency = f"{PerformanceMetrics.TURN_LATENCIES}.{i}.{TurnLatencyAttributes.TURN_LATENCY}"

            performance_metrics[key_model_latency] = turn_latencies[i].get(
                "modelLatency"
            )
            performance_metrics[key_voice_latency] = turn_latencies[i].get(
                "voiceLatency"
            )
            performance_metrics[key_transcriber_latency] = turn_latencies[i].get(
                "transcriberLatency"
            )
            performance_metrics[key_endpointing_latency] = turn_latencies[i].get(
                "endpointingLatency"
            )
            performance_metrics[key_turn_latency] = turn_latencies[i].get("turnLatency")

        del performance_metrics["turnLatencies"]

    flattened_performance_metrics = flatten_dict(performance_metrics)
    eval_attributes.update(flattened_performance_metrics)


def _extract_recording_urls(log: dict, eval_attributes: dict):
    """Extracts recording URLs and adds them to eval_attributes."""
    artifact = log.get("artifact")
    recording = artifact.get("recording") if isinstance(artifact, dict) else None
    if not (recording and isinstance(recording, dict)):
        return

    if mono := recording.get("mono"):
        if combined_url := mono.get("combinedUrl"):
            key = f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}"
            eval_attributes[key] = combined_url
        if customer_url := mono.get("customerUrl"):
            key = f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_CUSTOMER}"
            eval_attributes[key] = customer_url
        if assistant_url := mono.get("assistantUrl"):
            key = f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_ASSISTANT}"
            eval_attributes[key] = assistant_url

    if stereo_url := recording.get("stereoUrl"):
        key = f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}"
        eval_attributes[key] = stereo_url


def _extract_metrics(log: dict, eval_attributes: dict):
    """Extracts metrics from Vapi call log."""
    if metrics_calculator is None:
        return
    artifact = log.get("artifact", {})
    metrics = metrics_calculator.calculate_metrics(artifact)

    eval_attributes["avg_agent_latency_ms"] = metrics.avg_agent_latency_ms
    eval_attributes["user_interruption_count"] = metrics.user_interruption_count
    eval_attributes["user_interruption_rate"] = metrics.user_interruption_rate
    eval_attributes[CallAttributes.USER_WPM] = metrics.user_wpm
    eval_attributes[CallAttributes.BOT_WPM] = metrics.bot_wpm
    eval_attributes[CallAttributes.TALK_RATIO] = metrics.talk_ratio
    eval_attributes["ai_interruption_count"] = metrics.ai_interruption_count
    eval_attributes["ai_interruption_rate"] = metrics.ai_interruption_rate
    eval_attributes["avg_stop_time_after_interruption_ms"] = (
        metrics.avg_stop_time_after_interruption_ms
    )
    eval_attributes["metrics_data"] = metrics.detailed_data


def _extract_common_call_fields(log: dict, eval_attributes: dict):
    """Extracts provider-agnostic call fields into eval_attributes."""
    # total_number_of_turns
    messages = log.get("messages", [])
    if isinstance(messages, list):
        eval_attributes[CallAttributes.TOTAL_TURNS] = sum(
            1 for msg in messages if msg.get("role") in ("user", "assistant", "bot")
        )
    else:
        eval_attributes[CallAttributes.TOTAL_TURNS] = 0

    # total_call_duration (seconds, int) — prefer startedAt (actual call start),
    # fall back to createdAt (always present) if startedAt is missing
    # (e.g. queued/scheduled calls).
    try:
        start_key = "startedAt" if "startedAt" in log else "createdAt"
        if start_key in log and "endedAt" in log:
            start = datetime.fromisoformat(log[start_key].replace("Z", "+00:00"))
            end = datetime.fromisoformat(log["endedAt"].replace("Z", "+00:00"))
            eval_attributes[CallAttributes.DURATION] = int(
                (end - start).total_seconds()
            )
        else:
            eval_attributes[CallAttributes.DURATION] = None
    except (ValueError, TypeError):
        eval_attributes[CallAttributes.DURATION] = None

    # participant_phone_number
    customer = log.get("customer") or {}
    eval_attributes[CallAttributes.PARTICIPANT_PHONE_NUMBER] = customer.get("number")

    # call_status (raw provider status)
    eval_attributes[CallAttributes.STATUS] = log.get("status")


def _coerce_log_datetime(payload: dict) -> str | None:
    """Convert Vapi log timestamp to ISO string. Tries time (ms), timestamp (ns), ts (ISO)."""
    time_value = payload.get("time")
    if isinstance(time_value, (int, float)):
        try:
            return datetime.fromtimestamp(
                time_value / 1000, tz=dt_timezone.utc
            ).isoformat()
        except (OverflowError, OSError, ValueError):
            pass

    timestamp_value = payload.get("timestamp")
    if isinstance(timestamp_value, (int, float)):
        try:
            return datetime.fromtimestamp(
                timestamp_value / 1_000_000_000, tz=dt_timezone.utc
            ).isoformat()
        except (OverflowError, OSError, ValueError):
            pass

    iso_value = payload.get("ts")
    if isinstance(iso_value, str):
        try:
            return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass

    return None


def _extract_call_logs(log: dict, eval_attributes: dict, *, api_key: str | None = None):
    """Fetch call logs (Tier 1 auth then Tier 2 legacy) and store under call_logs in span_attributes."""
    from tracer.utils.vapi_recording import VapiRecordingService

    if not isinstance(log, dict):
        logger.error(
            "extract_call_logs: LOG IS NOT A DICT — cannot extract call_id/artifact",
            log_type=type(log).__name__,
        )
        return

    call_id = log.get("id")
    artifact = log.get("artifact", {})
    legacy_url = artifact.get("logUrl") if isinstance(artifact, dict) else None
    if not (call_id or legacy_url):
        return

    entries = VapiRecordingService.fetch_and_parse_call_logs(
        call_id=call_id,
        api_key=api_key,
        legacy_url=legacy_url,
    )
    if entries is None:
        logger.warning("extract_call_logs: fetch returned None")
        return

    eval_attributes["call_logs"] = entries
