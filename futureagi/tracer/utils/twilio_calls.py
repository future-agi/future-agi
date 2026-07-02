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
    status = status_map.get((log.get("status") or "").lower(), "unset")

    start_time = _parse_rfc2822(log.get("start_time"))
    end_time = _parse_rfc2822(log.get("end_time"))

    price = log.get("price")
    # Twilio reports price as a negative charge (e.g. "-0.0085"); store magnitude.
    cost = abs(float(price)) if price not in (None, "") else None

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
