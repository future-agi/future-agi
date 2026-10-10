"""Public envelope constructors and a strict, non-mutating read projection."""

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .constants import FAILED_REASONS, UNAVAILABLE_REASONS

METRIC_UNITS = {
    "average_pitch_hz": "Hz",
    "estimated_snr_db": "dB",
    "voice_quality_index": "index",
}
COVERAGE_FIELDS = {
    "average_pitch_hz": {
        "analyzed_seconds",
        "voiced_seconds",
        "frame_count",
        "voiced_frame_count",
    },
    "estimated_snr_db": {
        "analyzed_seconds",
        "active_seconds",
        "inactive_seconds",
        "frame_count",
    },
    "voice_quality_index": {
        "window_count",
        "excluded_window_count",
        "detected_speech_seconds",
        "covered_speech_seconds",
        "expected_sample_count",
        "resampled_sample_count",
        "detector_id",
        "polynomial_version",
        "window_policy_version",
        "preprocessing_version",
    },
}
SOURCE_FIELDS = {
    "capture_origin",
    "transport",
    "provider",
    "target_role",
    "mapping_version",
    "original_sample_rate_hz",
    "original_channel_count",
    "target_sample_count",
    "input_duration_seconds",
    "preprocessing_version",
}


@dataclass
class MetricEntry:
    value: float | None
    unit: str
    state: str
    reason: str | None
    algorithm_version: str | None = None
    coverage: dict | None = None


@dataclass
class AudioEnvelope:
    generation: int
    metrics: dict[str, dict]
    schema_version: int = 1
    analysis_id: str | None = None
    state: str = "not_requested"
    computed_at: str | None = None
    scheduled_at: str | None = None
    deadline_at: str | None = None
    target_role: str = "tested_agent"
    source: dict | None = None
    pipeline_version: str | None = None


def metric_entry(
    name,
    *,
    state="unavailable",
    reason=None,
    value=None,
    algorithm_version=None,
    coverage=None,
    **extra,
):
    result = asdict(
        MetricEntry(
            value, METRIC_UNITS[name], state, reason, algorithm_version, coverage
        )
    )
    if name == "estimated_snr_db":
        result["flags"] = None
    if name == "voice_quality_index":
        result.update(model_version=None, calibration_id=None, scale_id=None)
    result.update(extra)
    return result


def empty_envelope(generation=0, reason="not_analyzed") -> dict:
    return asdict(
        AudioEnvelope(
            generation=generation,
            metrics={name: metric_entry(name, reason=reason) for name in METRIC_UNITS},
        )
    )


def derive_state(metrics) -> str:
    states = [entry["state"] for entry in metrics.values()]
    if "pending" in states:
        return "pending"
    if len(states) == 3 and all(s == "available" for s in states):
        return "complete"
    if "available" in states:
        return "partial"
    if "failed" in states:
        return "failed"
    return "unavailable"


def _finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _scalar(value):
    if value is None or isinstance(value, (str, bool)) or _finite(value):
        return value
    return None


def _projection(raw, fields):
    return (
        {key: _scalar(raw[key]) for key in fields if key in raw}
        if isinstance(raw, dict)
        else None
    )


def _expired(deadline, now):
    if not isinstance(deadline, str):
        return False
    try:
        parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed <= now
    except (ValueError, TypeError):
        return False


def sanitize_for_api(raw: Any, generation: int = 0, *, now=None) -> dict:
    if raw is None:
        return empty_envelope(generation)
    if not isinstance(raw, dict):
        raw = {}
    if raw.get("schema_version") != 1:
        return {
            "schema_version": _scalar(raw.get("schema_version")),
            "state": "unsupported",
        }
    safe = empty_envelope(generation)
    for key in (
        "analysis_id",
        "computed_at",
        "scheduled_at",
        "deadline_at",
        "pipeline_version",
    ):
        value = raw.get(key)
        safe[key] = value if isinstance(value, str) else None
    stored_generation = raw.get("generation")
    if type(stored_generation) is int and stored_generation >= 0:
        safe["generation"] = stored_generation
    safe["source"] = _projection(raw.get("source"), SOURCE_FIELDS)
    expired = _expired(safe["deadline_at"], now or datetime.now(UTC))
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    for name in METRIC_UNITS:
        entry = metrics.get(name)
        if not isinstance(entry, dict):
            entry = {}
        result = safe["metrics"][name]
        state = entry.get("state", "unavailable")
        if not isinstance(state, str) or state not in {
            "available",
            "pending",
            "unavailable",
            "failed",
        }:
            state = "failed"
        reason = entry.get("reason")
        value = entry.get("value")
        if (value is not None and not _finite(value)) or (
            state == "available" and value is None
        ):
            state, reason = "failed", "nonfinite_output"
        elif state == "pending" and expired:
            state, reason = "failed", "timeout"
        if state in {"available", "pending"}:
            reason = None
        elif state == "failed":
            reason = (
                reason
                if isinstance(reason, str) and reason in FAILED_REASONS
                else "other_failed"
            )
        else:
            reason = (
                reason
                if isinstance(reason, str) and reason in UNAVAILABLE_REASONS
                else "other_unavailable"
            )
        result.update(
            value=value if state == "available" else None,
            state=state,
            reason=reason,
            algorithm_version=entry.get("algorithm_version")
            if isinstance(entry.get("algorithm_version"), str)
            else None,
            coverage=_projection(entry.get("coverage"), COVERAGE_FIELDS[name]),
        )
        if name == "estimated_snr_db":
            flags = entry.get("flags")
            result["flags"] = (
                {
                    k: v
                    for k, v in flags.items()
                    if k in {"noise_floor_floored", "value_capped"}
                    and isinstance(v, bool)
                }
                if isinstance(flags, dict)
                else None
            )
        if name == "voice_quality_index":
            for key in ("model_version", "calibration_id", "scale_id"):
                result[key] = (
                    entry.get(key) if isinstance(entry.get(key), str) else None
                )
    safe["state"] = (
        "not_requested"
        if raw.get("state") == "not_requested"
        else derive_state(safe["metrics"])
    )
    return safe
