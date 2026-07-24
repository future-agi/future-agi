"""Bland.ai call-log normalization for pull-based observability."""

from datetime import UTC, datetime, timedelta

from simulate.temporal.utils.async_storage import convert_audio_url_to_s3_sync
from tracer.utils.otel import (
    CallAttributes,
    ConversationAttributes,
    MessageAttributes,
    SpanAttributes,
)
from tracer.utils.vapi_recording import VapiRecordingService

# Bland returns a single combined (mono-mixed) recording — no channel
# separation, so only the combined key is written (per-channel needs diarization).
_BLAND_COMBINED_RECORDING_KEY = (
    f"{ConversationAttributes.CONVERSATION_RECORDING}."
    f"{ConversationAttributes.MONO_COMBINED}"
)


def normalize_bland_data(log: dict, *, project_id: str | None = None) -> dict:
    """Normalizes a single detailed Bland call into the standard log shape."""
    status_map = {"completed": "ok", "complete": "ok", "failed": "error", "error": "error"}
    raw_status = (log.get("status") or "") if isinstance(log.get("status"), str) else ""
    status = status_map.get(raw_status.lower(), "ok" if log.get("completed") else "unset")

    eval_attributes = _extract_eval_attributes(log)

    # Observe ingest only (project_id set): rehost the combined recording to our
    # storage via the shared converter — Bland's raw URL has cross-origin
    # problems. The sim path rehosts separately, so gating on project_id avoids a
    # double-rehost. Best-effort: a failure leaves the source URL in place so a
    # later poll can retry, and must not drop the whole span.
    rehost_uploads: dict[str, int] = {}
    source_url = eval_attributes.get(_BLAND_COMBINED_RECORDING_KEY)
    if project_id and source_url and not VapiRecordingService.is_fagi_s3_url(source_url):
        try:
            durable_url, artifact_bytes = convert_audio_url_to_s3_sync(
                call_id=log.get("call_id") or log.get("c_id"),
                audio_url=source_url,
                url_type="mono_combined",
                provider="bland",
                artifact_type="mono_combined",
                project_id=project_id,
            )
            if durable_url and durable_url != source_url:
                eval_attributes[_BLAND_COMBINED_RECORDING_KEY] = durable_url
                rehost_uploads = {"mono_combined": artifact_bytes}
        except Exception:
            pass

    start_time, end_time = _extract_timestamps(log)
    transcript = _transcript_messages(log)

    price = log.get("price")
    cost = float(price) if price not in (None, "") else None

    return {
        "id": log.get("call_id") or log.get("c_id"),
        "start_time": start_time,
        "end_time": end_time,
        "cost": cost,
        "status": status,
        "input": {"transcript": transcript} if transcript else {},
        "metadata": {
            "to": log.get("to"),
            "from": log.get("from"),
            "recording_url": log.get("recording_url"),
            "summary": log.get("summary"),
            "error_message": log.get("error_message"),
        },
        "span_attributes": eval_attributes,
        # Bland exposes no token usage on the call object.
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "rehost_bytes_uploaded": sum(rehost_uploads.values()),
        "rehost_uploads": rehost_uploads,
    }


def _transcript_messages(log: dict) -> list[dict]:
    """Bland ``transcripts`` rows → [{"role", "message"}] (speaker key is ``user``)."""  # noqa: E501
    rows = log.get("transcripts")
    if not (rows and isinstance(rows, list)):
        return []
    return [
        {"role": row.get("user"), "message": row.get("text")}
        for row in rows
        if isinstance(row, dict) and row.get("text")
    ]


def _extract_eval_attributes(log: dict) -> dict:
    eval_attributes = {
        SpanAttributes.SPAN_KIND: "conversation",
        "raw_log": log,
    }
    _extract_transcript(log, eval_attributes)
    _extract_recording(log, eval_attributes)
    _extract_common_call_fields(log, eval_attributes)
    return eval_attributes


def _extract_recording(log: dict, eval_attributes: dict):
    recording_url = log.get("recording_url")
    if recording_url:
        eval_attributes[_BLAND_COMBINED_RECORDING_KEY] = recording_url


def _extract_transcript(log: dict, eval_attributes: dict):
    for i, msg in enumerate(_transcript_messages(log)):
        if msg.get("role"):
            eval_attributes[
                f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{i}.{MessageAttributes.MESSAGE_ROLE}"
            ] = msg["role"]
        if msg.get("message"):
            eval_attributes[
                f"{ConversationAttributes.CONVERSATION_TRANSCRIPT}.{i}.{MessageAttributes.MESSAGE_CONTENT}"
            ] = msg["message"]


def _duration_seconds(log: dict) -> int | None:
    """``call_length`` is in MINUTES (float) per Bland's API."""
    call_length = log.get("call_length")
    if call_length in (None, ""):
        return None
    try:
        return int(round(float(call_length) * 60))
    except (TypeError, ValueError):
        return None


def _parse_ts(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _extract_timestamps(log: dict) -> tuple:
    start_time = _parse_ts(log.get("started_at")) or _parse_ts(log.get("created_at"))
    end_time = _parse_ts(log.get("end_at"))
    if start_time and not end_time:
        duration = _duration_seconds(log)
        if duration is not None:
            end_time = start_time + timedelta(seconds=duration)
    return start_time, end_time


def _extract_common_call_fields(log: dict, eval_attributes: dict):
    """Provider-agnostic call fields, mirroring the other normalizers."""
    transcript = _transcript_messages(log)
    eval_attributes[CallAttributes.TOTAL_TURNS] = sum(
        1 for msg in transcript if msg.get("role") in ("user", "assistant")
    )
    eval_attributes[CallAttributes.DURATION] = _duration_seconds(log)
    eval_attributes[CallAttributes.PARTICIPANT_PHONE_NUMBER] = log.get("to")
    eval_attributes[CallAttributes.STATUS] = log.get("status")
    # Bland does not provide per-message timing → no WPM / talk-ratio signals.
    eval_attributes[CallAttributes.USER_WPM] = None
    eval_attributes[CallAttributes.BOT_WPM] = None
    eval_attributes[CallAttributes.TALK_RATIO] = None
