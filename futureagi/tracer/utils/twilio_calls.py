"""Twilio call-log normalization for pull-based observability.

Twilio Call records carry call metadata but NO transcript, so ``input`` and
``TOTAL_TURNS`` stay empty for Twilio-fronted agents.
"""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from tracer.utils.otel import CallAttributes, SpanAttributes


def normalize_twilio_data(log: dict) -> dict:
    """Normalizes a single Twilio Call resource into the standard log shape."""
    status_map = {
        "completed": "ok",
        "busy": "error",
        "failed": "error",
        "no-answer": "error",
        "canceled": "error",
    }
    raw_status = log.get("status")
    raw_status = raw_status.lower() if isinstance(raw_status, str) else ""
    status = status_map.get(raw_status, "unset")

    start_time = _parse_rfc2822(log.get("start_time"))
    end_time = _parse_rfc2822(log.get("end_time"))

    price = log.get("price")
    # Twilio reports price as a negative charge (e.g. "-0.0085"); store magnitude.
    try:
        cost = abs(float(price)) if price not in (None, "") else None
    except (TypeError, ValueError):
        cost = None

    eval_attributes = {
        SpanAttributes.SPAN_KIND: "conversation",
        "raw_log": log,
        CallAttributes.TOTAL_TURNS: 0,  # no transcript on the Call resource
        CallAttributes.DURATION: _duration_seconds(log),
        CallAttributes.PARTICIPANT_PHONE_NUMBER: log.get("to"),
        CallAttributes.STATUS: log.get("status"),
        CallAttributes.USER_WPM: None,
        CallAttributes.BOT_WPM: None,
        CallAttributes.TALK_RATIO: None,
    }

    # Display fields for the voice-call list (mirror _process_twilio_raw).
    eval_attributes[CallAttributes.STATUS_DISPLAY] = log.get("status")
    eval_attributes[CallAttributes.RECORDING_AVAILABLE] = False
    eval_attributes[CallAttributes.MESSAGE_COUNT] = 0
    eval_attributes[CallAttributes.TRANSCRIPT_AVAILABLE] = False
    if direction := log.get("direction"):
        eval_attributes[CallAttributes.CALL_TYPE] = direction
    if start_time_raw := log.get("start_time"):
        eval_attributes[CallAttributes.STARTED_AT] = start_time_raw
        eval_attributes[CallAttributes.CREATED_AT] = start_time_raw
    elif date_created := log.get("date_created"):
        eval_attributes[CallAttributes.CREATED_AT] = date_created
    if price not in (None, ""):
        try:
            eval_attributes[CallAttributes.COST_CENTS] = abs(float(price)) * 100
        except (TypeError, ValueError):
            pass

    return {
        "id": log.get("sid"),
        "start_time": start_time,
        "end_time": end_time,
        "cost": cost,
        "status": status,
        "input": {},  # no transcript available from Twilio
        "metadata": {
            "to": log.get("to"),
            "from": log.get("from"),
            "direction": log.get("direction"),
            "answered_by": log.get("answered_by"),
        },
        "span_attributes": eval_attributes,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def _duration_seconds(log: dict) -> int | None:
    duration = log.get("duration")
    if duration in (None, ""):
        return None
    try:
        return int(duration)
    except (TypeError, ValueError):
        return None


def _parse_rfc2822(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
