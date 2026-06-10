"""Pull-based observability for Bland + Twilio (TH-5642).

Per product direction, 3rd-party observability is PULL-based (like Vapi: we
fetch call data from the provider's API). These tests pin the two new
normalizers, the fetcher dispatch (incl. the graceful no-pull providers), and
the normalization registry wiring.
"""

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
@pytest.mark.parametrize(
    "provider_value",
    [
        ProviderChoices.LIVEKIT,
        ProviderChoices.DEEPGRAM,
        ProviderChoices.PIPECAT,
        ProviderChoices.AGORA,
    ],
)
def test_get_call_logs_no_pull_providers_return_empty(provider_value):
    """Providers with nothing hosted to pull must skip gracefully — a raise
    here crash-loops the scheduled fetch for every enabled agent definition."""
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


@pytest.mark.unit
def test_normalization_registry_includes_new_providers():
    import tracer.utils.observability_provider as op_module
    import inspect

    src = inspect.getsource(op_module.process_and_store_logs)
    for key in ('"bland"', '"twilio"'):
        assert key in src, f"{key} missing from normalization_functions registry"
