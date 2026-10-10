"""R08: safe, finite, forward-compatible read projection."""

import copy
import json
from datetime import UTC, datetime

import pytest

from ee.voice.services.audio_analysis.envelope import (
    derive_state,
    empty_envelope,
    sanitize_for_api,
)


@pytest.mark.parametrize(
    "states,expected",
    [
        (["available"] * 3, "complete"),
        (["available", "unavailable", "failed"], "partial"),
        (["available", "pending", "failed"], "pending"),
        (["failed", "unavailable", "failed"], "failed"),
        (["unavailable"] * 3, "unavailable"),
    ],
)
def test_state_precedence(states, expected):
    assert derive_state(dict(enumerate({"state": s} for s in states))) == expected


def test_null_column_not_requested():
    result = sanitize_for_api(None, 4)
    assert result == empty_envelope(4, "not_analyzed")
    assert result["state"] == "not_requested"
    assert result["pipeline_version"] is None


def test_unknown_schema_version_unsupported():
    assert sanitize_for_api({"schema_version": 999, "secret": "key"}) == {
        "schema_version": 999,
        "state": "unsupported",
    }


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), -float("inf"), True, "12", {"secret": 3}]
)
def test_envelope_json_no_nonfinite(bad):
    raw = empty_envelope(0, "not_validated")
    raw["state"] = "partial"
    raw["metrics"]["average_pitch_hz"].update(value=bad, state="available")
    raw["metrics"]["estimated_snr_db"].update(value=0, state="available", reason="bad")
    raw["metrics"]["estimated_snr_db"]["coverage"] = {
        "active_seconds": float("nan"),
        "secret": "key",
    }
    raw["source"] = {"input_duration_seconds": float("inf"), "object_key": "secret"}
    raw["audio_provenance"] = "secret"
    safe = sanitize_for_api(raw)
    json.dumps(safe, allow_nan=False)
    assert safe["metrics"]["average_pitch_hz"]["reason"] == "nonfinite_output"
    assert safe["metrics"]["estimated_snr_db"]["value"] == 0
    assert safe["state"] == "partial"
    assert "secret" not in json.dumps(safe)


def test_expired_pending_keeps_available_sibling_and_does_not_mutate():
    raw = empty_envelope(1, "not_validated")
    raw.update(state="pending", deadline_at="2000-01-01T00:00:00Z")
    raw["metrics"]["average_pitch_hz"].update(state="available", value=220)
    raw["metrics"]["estimated_snr_db"].update(state="pending", reason=None)
    before = copy.deepcopy(raw)
    safe = sanitize_for_api(raw, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert safe["metrics"]["estimated_snr_db"]["reason"] == "timeout"
    assert safe["state"] == "partial"
    assert raw == before


@pytest.mark.parametrize(
    "state,fallback", [("failed", "other_failed"), ("unavailable", "other_unavailable")]
)
def test_unknown_reason_fallback(state, fallback):
    raw = empty_envelope(0, "not_analyzed")
    raw["metrics"]["average_pitch_hz"].update(state=state, reason="future")
    assert sanitize_for_api(raw)["metrics"]["average_pitch_hz"]["reason"] == fallback


@pytest.mark.parametrize("state", [[], {}, 23])
def test_malformed_state_fails_closed(state):
    raw = empty_envelope(0)
    raw["state"] = "partial"
    raw["metrics"]["average_pitch_hz"]["state"] = state
    safe = sanitize_for_api(raw)
    assert safe["metrics"]["average_pitch_hz"]["state"] == "failed"
    json.dumps(safe, allow_nan=False)
