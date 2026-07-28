"""Pull-based observability for Bland + Twilio: normalizers, fetcher dispatch, registry wiring."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import ObservabilityService
from tracer.utils.bland import normalize_bland_data
from tracer.utils.twilio_calls import normalize_twilio_data

BLAND_CALL = {
    "call_id": "bl-123",
    "created_at": "2026-06-09T20:00:00Z",
    "started_at": "2026-06-09T20:00:05Z",
    "end_at": "2026-06-09T20:01:05Z",
    "call_length": 1.0,  # minutes
    "completed": True,
    "status": "completed",
    "to": "+15551230000",
    "from": "+15559990000",
    "price": 0.09,
    "recording_url": None,
    "transcripts": [
        {"id": 1, "user": "assistant", "text": "Hello, how can I help?"},
        {"id": 2, "user": "user", "text": "What are your opening hours?"},
    ],
}

TWILIO_CALL = {
    "sid": "CA0123",
    "status": "completed",
    "start_time": "Tue, 09 Jun 2026 20:00:00 +0000",
    "end_time": "Tue, 09 Jun 2026 20:00:43 +0000",
    "duration": "43",
    "to": "+12175696753",
    "from": "+15555550000",
    "price": "-0.0085",
    "direction": "outbound-api",
}


@pytest.mark.unit
def test_normalize_bland_data_shape():
    out = normalize_bland_data(BLAND_CALL)
    assert out["id"] == "bl-123"
    assert out["status"] == "ok"
    assert out["cost"] == 0.09
    assert out["start_time"] == datetime(2026, 6, 9, 20, 0, 5, tzinfo=UTC)
    assert out["end_time"] == datetime(2026, 6, 9, 20, 1, 5, tzinfo=UTC)
    # Transcript flattened to role/message pairs.
    transcript = out["input"]["transcript"]
    assert transcript[0] == {"role": "assistant", "message": "Hello, how can I help?"}
    assert transcript[1]["role"] == "user"
    # Common call fields present in span_attributes.
    assert out["span_attributes"]["call.total_turns"] == 2
    assert out["span_attributes"]["call.duration"] == 60
    assert out["span_attributes"]["raw_log"] is BLAND_CALL


@pytest.mark.unit
def test_normalize_bland_call_length_is_minutes():
    out = normalize_bland_data({**BLAND_CALL, "end_at": None, "call_length": 2.5})
    assert out["span_attributes"]["call.duration"] == 150
    # end derived from start + duration
    assert (out["end_time"] - out["start_time"]).total_seconds() == 150


@pytest.mark.unit
def test_normalize_twilio_data_shape():
    out = normalize_twilio_data(TWILIO_CALL)
    assert out["id"] == "CA0123"
    assert out["status"] == "ok"
    assert out["cost"] == 0.0085  # magnitude of the negative charge
    assert out["start_time"] == datetime(2026, 6, 9, 20, 0, 0, tzinfo=UTC)
    assert out["input"] == {}  # Twilio stores no transcript on the Call resource
    assert out["span_attributes"]["call.duration"] == 43
    assert out["metadata"]["direction"] == "outbound-api"


@pytest.mark.unit
@pytest.mark.parametrize("status", ["busy", "failed", "no-answer", "canceled"])
def test_normalize_twilio_failure_statuses(status):
    assert normalize_twilio_data({**TWILIO_CALL, "status": status})["status"] == "error"


def _provider(provider_value, api_key="k", assistant_id="a"):
    agent = SimpleNamespace(api_key=api_key, assistant_id=assistant_id)
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        provider=provider_value,
        agent_definition=agent,
    )


@pytest.mark.unit
def test_get_call_logs_dispatches_bland_and_twilio():
    with patch.object(ObservabilityService, "_fetch_bland_logs", return_value=[1]) as b:
        assert (
            ObservabilityService.get_call_logs(_provider(ProviderChoices.BLAND)) == [1]
        )
        b.assert_called_once()
    with patch.object(
        ObservabilityService, "_fetch_twilio_logs", return_value=[2]
    ) as t:
        assert (
            ObservabilityService.get_call_logs(_provider(ProviderChoices.TWILIO)) == [2]
        )
        t.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("provider_value", [ProviderChoices.LIVEKIT])
def test_get_call_logs_no_pull_providers_return_empty(provider_value):
    """No-pull providers must skip gracefully; a raise crash-loops the scheduled fetch."""
    assert ObservabilityService.get_call_logs(_provider(provider_value)) == []


@pytest.mark.unit
def test_twilio_fetch_requires_sid_token_format():
    """api_key must be '<AccountSid>:<AuthToken>'; anything else skips safely."""
    assert (
        ObservabilityService._fetch_twilio_logs(
            _provider(ProviderChoices.TWILIO, api_key="just-a-token")
        )
        == []
    )


ELEVEN_LABS_RAW = {
    "conversation_id": "el-1",
    "status": "done",
    "agent_id": "agent-x",
    "metadata": {
        "start_time_unix_secs": 1750000000,
        "call_duration_secs": 42,
        "cost": 12,
    },
    "transcript": [{"role": "user", "message": "hi", "time_in_call_secs": 0}],
}


@pytest.mark.unit
def test_process_raw_logs_bland_read_shape():
    """READ-side processor maps a pulled Bland call to the VoiceCallLogs shape."""
    out = ObservabilityService.process_raw_logs(BLAND_CALL, ProviderChoices.BLAND)
    assert out["call_id"] == "bl-123"
    assert out["status"] == "completed"
    assert out["duration_seconds"] == 60  # call_length 1.0 min -> 60s
    assert out["cost_cents"] == pytest.approx(9.0)  # 0.09 * 100
    assert len(out["transcript"]) == 2


@pytest.mark.unit
def test_process_raw_logs_twilio_read_shape():
    out = ObservabilityService.process_raw_logs(TWILIO_CALL, ProviderChoices.TWILIO)
    assert out["call_id"] == "CA0123"
    assert out["duration_seconds"] == 43
    assert out["cost_cents"] == pytest.approx(0.85)  # abs(-0.0085) * 100
    assert out["transcript"] == []


@pytest.mark.unit
def test_process_raw_logs_eleven_labs_normalizes_status_and_utc():
    out = ObservabilityService.process_raw_logs(
        ELEVEN_LABS_RAW, ProviderChoices.ELEVEN_LABS
    )
    assert out["call_id"] == "el-1"
    assert out["status"] == "completed"  # 'done' -> 'completed'
    assert out["duration_seconds"] == 42
    assert out["started_at"].endswith("+00:00")  # UTC, not naive local
    assert len(out["transcript"]) == 1


@pytest.mark.unit
def test_process_raw_logs_empty_synthesizes_from_call_attrs():
    """Collector spans carry no raw_log; derive the call-log shape from call.* attrs."""
    out = ObservabilityService.process_raw_logs(
        {},
        ProviderChoices.BLAND,
        span_attributes={
            "call.status": "error",
            "call.duration": 30,
            "metadata": {"call_execution_id": "exec-1"},
        },
    )
    assert out["status"] == "error"
    assert out["duration_seconds"] == 30
    assert out["call_id"] == "exec-1"
    assert out["started_at"] is None  # the span's own start_time is authoritative
