"""Guard for the OSS-lane early-return in _extract_metrics."""

from unittest.mock import patch

import pytest

from tracer.utils import vapi


_SAMPLE_LOG = {
    "id": "vapi-call-123",
    "artifact": {"messages": [], "recording": {}},
    "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ],
    "phoneNumber": {"number": "+15550001111"},
    "startedAt": "2026-07-23T00:00:00Z",
    "endedAt": "2026-07-23T00:01:00Z",
}

_METRIC_KEYS = (
    "avg_agent_latency_ms",
    "user_interruption_count",
    "user_interruption_rate",
    "ai_interruption_count",
    "ai_interruption_rate",
    "avg_stop_time_after_interruption_ms",
    "metrics_data",
)


class TestExtractMetricsOSSGuard:
    def test_extract_metrics_skips_when_calculator_absent(self):
        eval_attributes = {}
        with patch.object(vapi, "metrics_calculator", None):
            vapi._extract_metrics(_SAMPLE_LOG, eval_attributes)

        for key in _METRIC_KEYS:
            assert key not in eval_attributes, f"{key} should not be set on OSS"

    def test_extract_eval_attributes_survives_when_calculator_absent(self):
        with patch.object(vapi, "metrics_calculator", None):
            attrs = vapi._extract_eval_attributes(
                _SAMPLE_LOG, include_call_logs=False
            )

        assert attrs["vapi.call_id"] == "vapi-call-123"
        assert "raw_log" in attrs
        for key in _METRIC_KEYS:
            assert key not in attrs, f"{key} should not be present on OSS"
