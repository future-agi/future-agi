"""
Tests for the span_attributes overlay in ObservabilityService.process_raw_logs.

Moved from test_observability_recordings_rehost.py (which was deleted in
Phase 2 of the inline Vapi recording rehost work — the backfill Temporal
task no longer exists, but these overlay tests remain valid).

Run with: pytest tracer/tests/test_vapi_process_raw_logs.py -v
"""

from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import ObservabilityService


class TestProcessRawLogsOverlay:
    """Tests for the span_attributes overlay in ObservabilityService.process_raw_logs."""

    def test_vapi_overlay_prefers_flat_s3_alias(self):
        """The flat S3 alias (recording_url) is preferred over raw_log provider URLs."""
        raw_log = {
            "id": "vapi-call-1",
            "recordingUrl": "https://storage.vapi.ai/combined.mp3",
            "artifact": {"stereoRecordingUrl": "https://storage.vapi.ai/stereo.mp3"},
            "messages": [],
        }
        span_attributes = {
            "recording_url": "https://fagi.s3.amazonaws.com/x/combined.mp3",
            "stereo_recording_url": "https://fagi.s3.amazonaws.com/x/stereo.mp3",
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.VAPI, span_attributes=span_attributes
        )

        assert result["recording_url"] == span_attributes["recording_url"]
        assert result["stereo_recording_url"] == span_attributes[
            "stereo_recording_url"
        ]

    def test_vapi_overlay_new_shape_before_legacy(self):
        """New shape (artifact.recording.mono.combinedUrl) beats legacy recordingUrl."""
        raw_log = {
            "id": "vapi-call-1",
            "recordingUrl": "https://storage.vapi.ai/legacy.mp3",
            "artifact": {
                "recording": {"mono": {"combinedUrl": "https://storage.vapi.ai/new-shape.mp3"}}
            },
            "messages": [],
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.VAPI
        )

        assert result["recording_url"] == "https://storage.vapi.ai/new-shape.mp3"

    def test_no_overlay_keeps_provider_urls(self):
        """Without span_attributes, the field-chain fallback returns the raw_log value."""
        raw_log = {
            "id": "vapi-call-1",
            "recordingUrl": "https://storage.vapi.ai/combined.mp3",
            "artifact": {"stereoRecordingUrl": "https://storage.vapi.ai/stereo.mp3"},
            "messages": [],
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.VAPI
        )

        assert result["recording_url"] == "https://storage.vapi.ai/combined.mp3"
        assert (
            result["stereo_recording_url"] == "https://storage.vapi.ai/stereo.mp3"
        )

    def test_retell_overlay_prefers_durable_span_urls(self):
        """ClickHouse span attributes override Retell's expiring provider URLs."""
        raw_log = {
            "call_id": "retell-call-1",
            "recording_url": "https://retell.example/raw-mono.wav",
            "recording_multi_channel_url": "https://retell.example/raw-stereo.wav",
            "call_cost": {"product_costs": []},
        }
        span_attributes = {
            "conversation.recording.mono.combined": (
                "https://fi-customer-data.s3.amazonaws.com/rehosted/mono.wav"
            ),
            "conversation.recording.stereo": (
                "https://fi-customer-data.s3.amazonaws.com/rehosted/stereo.wav"
            ),
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.RETELL, span_attributes=span_attributes
        )

        assert result["recording_url"] == span_attributes[
            "conversation.recording.mono.combined"
        ]
        assert result["stereo_recording_url"] == span_attributes[
            "conversation.recording.stereo"
        ]
