"""
Tests for tracer.tasks.recordings_rehost.rehost_external_recordings and
the dispatch wiring in tracer.utils.observability_provider.

Run with: pytest tracer/tests/test_observability_recordings_rehost.py -v
"""

import uuid
from unittest.mock import MagicMock, Mock, patch

import pytest

from tracer.models.observability_provider import (
    ObservabilityProvider,
    ProviderChoices,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.tasks.recordings_rehost import rehost_external_recordings
from tracer.utils.observability_provider import _maybe_enqueue_recording_rehost


VAPI_KEYS = {
    "mono_combined": "conversation.recording.mono.combined",
    "mono_customer": "conversation.recording.mono.customer",
    "mono_assistant": "conversation.recording.mono.assistant",
    "stereo": "conversation.recording.stereo",
}

RETELL_KEYS = {
    "mono_combined": "conversation.recording.mono.combined",
    "stereo": "conversation.recording.stereo",
}


def _make_span(
    project,
    *,
    provider: str,
    span_attributes: dict,
    metadata: dict | None = None,
) -> ObservationSpan:
    trace = Trace.objects.create(project=project, metadata=metadata or {})
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=f"{provider.capitalize()} Call Log",
        observation_type="conversation",
        provider=provider,
        span_attributes=span_attributes,
        metadata=metadata or {},
    )


@pytest.mark.django_db(transaction=True)
class TestRehostExternalRecordings:
    """Tests for the rehost_external_recordings activity body."""

    def test_vapi_overwrites_four_keys(self, observe_project):
        raw_log = {"id": "vapi-call-abc", "artifact": {"recording": {"foo": "bar"}}}
        original = {
            "raw_log": raw_log,
            "vapi.call_id": "vapi-call-abc",
            VAPI_KEYS["mono_combined"]: "https://storage.vapi.ai/combined.mp3",
            VAPI_KEYS["mono_customer"]: "https://storage.vapi.ai/customer.mp3",
            VAPI_KEYS["mono_assistant"]: "https://storage.vapi.ai/assistant.mp3",
            VAPI_KEYS["stereo"]: "https://storage.vapi.ai/stereo.mp3",
        }
        span = _make_span(
            observe_project, provider="vapi", span_attributes=dict(original)
        )

        def _fake_convert(call_id, url, url_type):
            return f"https://fagi.s3.amazonaws.com/{call_id}/{url_type}.mp3"

        with patch(
            "tracer.tasks.recordings_rehost._convert_audio_url_to_s3",
            side_effect=_fake_convert,
        ):
            rehost_external_recordings(span_id=str(span.id))

        span.refresh_from_db()
        attrs = span.span_attributes

        # Raw log untouched
        assert attrs["raw_log"] == raw_log

        # Each recording key now points at S3
        for url_type, key in VAPI_KEYS.items():
            assert attrs[key].startswith("https://fagi.s3.amazonaws.com/")
            assert attrs[key].endswith(f"{url_type}.mp3")
            assert "vapi-call-abc" in attrs[key]

    def test_retell_overwrites_two_keys(self, observe_project):
        raw_log = {"call_id": "retell-call-xyz"}
        original = {
            "raw_log": raw_log,
            RETELL_KEYS["mono_combined"]: "https://retellai.com/combined.wav",
            RETELL_KEYS["stereo"]: "https://retellai.com/stereo.wav",
        }
        span = _make_span(
            observe_project, provider="retell", span_attributes=dict(original)
        )

        def _fake_convert(call_id, url, url_type):
            return f"https://fagi.s3.amazonaws.com/{call_id}/{url_type}.mp3"

        with patch(
            "tracer.tasks.recordings_rehost._convert_audio_url_to_s3",
            side_effect=_fake_convert,
        ):
            rehost_external_recordings(span_id=str(span.id))

        span.refresh_from_db()
        attrs = span.span_attributes

        assert attrs["raw_log"] == raw_log
        assert attrs[RETELL_KEYS["mono_combined"]].startswith(
            "https://fagi.s3.amazonaws.com/"
        )
        assert attrs[RETELL_KEYS["mono_combined"]].endswith("mono_combined.mp3")
        assert attrs[RETELL_KEYS["stereo"]].endswith("stereo.mp3")

    def test_idempotent_when_already_s3(self, observe_project):
        s3_combined = "https://fagi.s3.amazonaws.com/x/combined.mp3"
        s3_stereo = "https://fagi.s3.amazonaws.com/x/stereo.mp3"
        original = {
            "raw_log": {"id": "vapi-call-abc"},
            "vapi.call_id": "vapi-call-abc",
            VAPI_KEYS["mono_combined"]: s3_combined,
            VAPI_KEYS["stereo"]: s3_stereo,
        }
        span = _make_span(
            observe_project, provider="vapi", span_attributes=dict(original)
        )

        with patch(
            "tracer.tasks.recordings_rehost._convert_audio_url_to_s3",
        ) as mock_convert:
            rehost_external_recordings(span_id=str(span.id))

        mock_convert.assert_not_called()
        span.refresh_from_db()
        assert span.span_attributes[VAPI_KEYS["mono_combined"]] == s3_combined
        assert span.span_attributes[VAPI_KEYS["stereo"]] == s3_stereo

    def test_partial_failure_leaves_provider_url(self, observe_project):
        provider_combined = "https://storage.vapi.ai/combined.mp3"
        provider_stereo = "https://storage.vapi.ai/stereo.mp3"
        original = {
            "raw_log": {"id": "vapi-call-abc"},
            "vapi.call_id": "vapi-call-abc",
            VAPI_KEYS["mono_combined"]: provider_combined,
            VAPI_KEYS["stereo"]: provider_stereo,
        }
        span = _make_span(
            observe_project, provider="vapi", span_attributes=dict(original)
        )

        def _flaky_convert(call_id, url, url_type):
            # Combined succeeds, stereo fails (helper returns input on failure).
            if url_type == "stereo":
                return url
            return f"https://fagi.s3.amazonaws.com/{call_id}/{url_type}.mp3"

        with patch(
            "tracer.tasks.recordings_rehost._convert_audio_url_to_s3",
            side_effect=_flaky_convert,
        ):
            rehost_external_recordings(span_id=str(span.id))

        span.refresh_from_db()
        attrs = span.span_attributes
        assert attrs[VAPI_KEYS["mono_combined"]].startswith(
            "https://fagi.s3.amazonaws.com/"
        )
        # Stereo download failed → key still holds provider URL for next retry.
        assert attrs[VAPI_KEYS["stereo"]] == provider_stereo

    def test_skips_when_no_recording_keys(self, observe_project):
        span = _make_span(
            observe_project,
            provider="vapi",
            span_attributes={"raw_log": {"id": "x"}, "some.other.key": "value"},
        )

        with patch(
            "tracer.tasks.recordings_rehost._convert_audio_url_to_s3",
        ) as mock_convert:
            rehost_external_recordings(span_id=str(span.id))

        mock_convert.assert_not_called()

    def test_unknown_provider_is_noop(self, observe_project):
        span = _make_span(
            observe_project,
            provider="eleven_labs",
            span_attributes={
                VAPI_KEYS["mono_combined"]: "https://elevenlabs.io/x.mp3"
            },
        )

        with patch(
            "tracer.tasks.recordings_rehost._convert_audio_url_to_s3",
        ) as mock_convert:
            rehost_external_recordings(span_id=str(span.id))

        mock_convert.assert_not_called()

    def test_missing_span_logs_and_returns(self):
        with patch(
            "tracer.tasks.recordings_rehost._convert_audio_url_to_s3",
        ) as mock_convert:
            rehost_external_recordings(span_id=str(uuid.uuid4()))

        mock_convert.assert_not_called()


@pytest.mark.django_db(transaction=True)
class TestMaybeEnqueueRecordingRehost:
    """Tests for the dispatch helper called from process_and_store_logs."""

    def test_enqueues_when_recording_keys_present(self, observe_project):
        from accounts.models.organization import Organization

        organization = observe_project.organization
        workspace = observe_project.workspace
        provider = ObservabilityProvider.objects.create(
            project=observe_project,
            provider=ProviderChoices.VAPI,
            enabled=True,
            organization=organization,
            workspace=workspace,
            metadata={},
        )
        span = _make_span(
            observe_project,
            provider="vapi",
            span_attributes={
                VAPI_KEYS["mono_combined"]: "https://storage.vapi.ai/x.mp3",
            },
        )

        with patch(
            "tracer.utils.observability_provider.rehost_external_recordings"
        ) as mock_activity, patch(
            "tracer.utils.observability_provider.transaction.on_commit",
            side_effect=lambda fn: fn(),
        ):
            _maybe_enqueue_recording_rehost(provider, span)

        mock_activity.delay.assert_called_once_with(span_id=str(span.id))

    def test_skipped_when_metadata_disables(self, observe_project):
        organization = observe_project.organization
        workspace = observe_project.workspace
        provider = ObservabilityProvider.objects.create(
            project=observe_project,
            provider=ProviderChoices.VAPI,
            enabled=True,
            organization=organization,
            workspace=workspace,
            metadata={"rehost_recordings": False},
        )
        span = _make_span(
            observe_project,
            provider="vapi",
            span_attributes={
                VAPI_KEYS["mono_combined"]: "https://storage.vapi.ai/x.mp3",
            },
        )

        with patch(
            "tracer.utils.observability_provider.rehost_external_recordings"
        ) as mock_activity:
            _maybe_enqueue_recording_rehost(provider, span)

        mock_activity.delay.assert_not_called()

    def test_skipped_when_no_recording_keys(self, observe_project):
        organization = observe_project.organization
        workspace = observe_project.workspace
        provider = ObservabilityProvider.objects.create(
            project=observe_project,
            provider=ProviderChoices.VAPI,
            enabled=True,
            organization=organization,
            workspace=workspace,
            metadata={},
        )
        span = _make_span(
            observe_project,
            provider="vapi",
            span_attributes={"raw_log": {"id": "x"}},
        )

        with patch(
            "tracer.utils.observability_provider.rehost_external_recordings"
        ) as mock_activity:
            _maybe_enqueue_recording_rehost(provider, span)

        mock_activity.delay.assert_not_called()

    def test_skipped_for_unknown_provider(self, observe_project):
        organization = observe_project.organization
        workspace = observe_project.workspace
        provider = ObservabilityProvider.objects.create(
            project=observe_project,
            provider=ProviderChoices.ELEVEN_LABS,
            enabled=True,
            organization=organization,
            workspace=workspace,
            metadata={},
        )
        span = _make_span(
            observe_project,
            provider="eleven_labs",
            span_attributes={
                VAPI_KEYS["mono_combined"]: "https://x/y.mp3",
            },
        )

        with patch(
            "tracer.utils.observability_provider.rehost_external_recordings"
        ) as mock_activity:
            _maybe_enqueue_recording_rehost(provider, span)

        mock_activity.delay.assert_not_called()


class TestProcessRawLogsOverlay:
    """Tests for the span_attributes overlay in ObservabilityService.process_raw_logs."""

    def test_vapi_overlay_overrides_recording_url(self):
        from tracer.services.observability_providers import ObservabilityService

        raw_log = {
            "id": "vapi-call-1",
            "recordingUrl": "https://storage.vapi.ai/combined.mp3",
            "artifact": {"stereoRecordingUrl": "https://storage.vapi.ai/stereo.mp3"},
            "messages": [],
        }
        span_attributes = {
            "conversation.recording.mono.combined": "https://fagi.s3.amazonaws.com/x/combined.mp3",
            "conversation.recording.stereo": "https://fagi.s3.amazonaws.com/x/stereo.mp3",
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.VAPI, span_attributes=span_attributes
        )

        assert result["recording_url"] == span_attributes[
            "conversation.recording.mono.combined"
        ]
        assert result["stereo_recording_url"] == span_attributes[
            "conversation.recording.stereo"
        ]

    def test_no_overlay_keeps_provider_urls(self):
        from tracer.services.observability_providers import ObservabilityService

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
