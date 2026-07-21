"""Tests for read-path integration wiring the Vapi migration set up.

Covers:
 - replay_session._extract_recording_urls_from_spans mono-preferred ordering.
 - CallExecutionDetailSerializer.get_recordings preserving customer + assistant.
 - session_comparison.fetch_simulated_call_recordings model-first ordering.
 - tfc.utils.storage.download_audio_from_url delegating Vapi to the service.
 - simulate.temporal.utils.async_storage.download_audio_from_url_async delegating Vapi to the service.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestExtractRecordingUrlsFromSpans:
    def _span(self, mono, stereo, trace_id="t1"):
        return {
            "trace_id": trace_id,
            "span_attributes": {
                "conversation.recording.mono.combined": mono,
                "conversation.recording.stereo": stereo,
            },
        }

    def test_prefers_mono_over_stereo(self):
        from tracer.utils import replay_session

        spans = [self._span(
            mono="https://bucket.s3.amazonaws.com/mono.mp3",
            stereo="https://bucket.s3.amazonaws.com/stereo.mp3",
        )]
        with patch.object(replay_session, "merge_span_attrs", side_effect=lambda span: span["span_attributes"]):
            out = replay_session._extract_recording_urls_from_spans(spans)
        assert out == {"t1": "https://bucket.s3.amazonaws.com/mono.mp3"}

    def test_falls_back_to_stereo_when_mono_missing(self):
        from tracer.utils import replay_session

        spans = [self._span(
            mono=None,
            stereo="https://bucket.s3.amazonaws.com/stereo.mp3",
        )]
        with patch.object(replay_session, "merge_span_attrs", side_effect=lambda span: span["span_attributes"]):
            out = replay_session._extract_recording_urls_from_spans(spans)
        assert out == {"t1": "https://bucket.s3.amazonaws.com/stereo.mp3"}


class TestCallExecutionDetailSerializerGetRecordings:
    """Locks in customer + assistant preservation in get_recordings."""

    def _serializer(self, *, obj, context=None):
        from simulate.serializers.test_execution import CallExecutionDetailSerializer

        serializer = CallExecutionDetailSerializer()
        serializer._context = context or {"detail_mode": True}
        return serializer, obj

    def test_returns_all_four_keys_when_shortcut_and_model_present(self):
        """Model fields cover combined + stereo; the shortcut supplies customer + assistant."""
        obj = SimpleNamespace(
            recording_url="https://bucket.s3.amazonaws.com/combined.mp3",
            stereo_recording_url="https://bucket.s3.amazonaws.com/stereo.mp3",
            provider_call_data={
                "vapi": {
                    "recording": {
                        "combined": "https://bucket.s3.amazonaws.com/combined.mp3",
                        "stereo": "https://bucket.s3.amazonaws.com/stereo.mp3",
                        "customer": "https://bucket.s3.amazonaws.com/customer.mp3",
                        "assistant": "https://bucket.s3.amazonaws.com/assistant.mp3",
                    }
                }
            },
        )
        serializer, obj = self._serializer(obj=obj)
        result = serializer.get_recordings(obj)
        assert set(result.keys()) == {"combined", "stereo", "customer", "assistant"}
        assert result["combined"] == "https://bucket.s3.amazonaws.com/combined.mp3"
        assert result["stereo"] == "https://bucket.s3.amazonaws.com/stereo.mp3"
        assert result["customer"] == "https://bucket.s3.amazonaws.com/customer.mp3"
        assert result["assistant"] == "https://bucket.s3.amazonaws.com/assistant.mp3"

    def test_prefers_model_recording_url_over_shortcut(self):
        """The mirror on the model is authoritative for combined + stereo."""
        obj = SimpleNamespace(
            recording_url="https://model.s3.amazonaws.com/combined.mp3",
            stereo_recording_url="https://model.s3.amazonaws.com/stereo.mp3",
            provider_call_data={
                "vapi": {
                    "recording": {
                        "combined": "https://shortcut.s3.amazonaws.com/combined.mp3",
                        "stereo": "https://shortcut.s3.amazonaws.com/stereo.mp3",
                    }
                }
            },
        )
        serializer, obj = self._serializer(obj=obj)
        result = serializer.get_recordings(obj)
        assert result["combined"] == "https://model.s3.amazonaws.com/combined.mp3"
        assert result["stereo"] == "https://model.s3.amazonaws.com/stereo.mp3"

    def test_returns_only_shortcut_when_model_absent(self):
        obj = SimpleNamespace(
            recording_url=None,
            stereo_recording_url=None,
            provider_call_data={
                "vapi": {
                    "recording": {
                        "combined": "https://bucket.s3.amazonaws.com/combined.mp3",
                        "customer": "https://bucket.s3.amazonaws.com/customer.mp3",
                    }
                }
            },
        )
        serializer, obj = self._serializer(obj=obj)
        result = serializer.get_recordings(obj)
        assert result["combined"] == "https://bucket.s3.amazonaws.com/combined.mp3"
        assert result["customer"] == "https://bucket.s3.amazonaws.com/customer.mp3"
        assert "stereo" not in result
        assert "assistant" not in result

    def test_returns_empty_in_list_mode(self):
        obj = SimpleNamespace(
            recording_url="https://bucket.s3.amazonaws.com/x.mp3",
            stereo_recording_url=None,
            provider_call_data={},
        )
        serializer, obj = self._serializer(obj=obj, context={"detail_mode": False})
        assert serializer.get_recordings(obj) == {}


class TestFetchSimulatedCallRecordingsMirrorFirst:
    """Model fields should win over provider_call_data when both are present."""

    def test_model_fields_beat_shortcut_when_both_present(self):
        from simulate.utils import session_comparison

        call_exec = SimpleNamespace(
            provider_call_data={
                "vapi": {
                    "artifact": {
                        "recording": {
                            "mono": {"combinedUrl": "https://shortcut.s3.amazonaws.com/mono.mp3"},
                            "stereoUrl": "https://shortcut.s3.amazonaws.com/stereo.mp3",
                        }
                    }
                }
            },
            recording_url="https://model.s3.amazonaws.com/mono.mp3",
            stereo_recording_url="https://model.s3.amazonaws.com/stereo.mp3",
        )
        result = session_comparison.fetch_simulated_call_recordings(call_exec)
        assert result["mono_combined"] == "https://model.s3.amazonaws.com/mono.mp3"
        assert result["stereo"] == "https://model.s3.amazonaws.com/stereo.mp3"


class TestDownloadAudioFromUrlSyncRouting:
    """tfc.utils.storage.download_audio_from_url delegates to the service for Vapi."""

    def test_delegates_to_service_when_authenticated(self):
        from tfc.utils import storage
        from tracer.utils.vapi_recording import VapiRecordingService

        with patch.object(VapiRecordingService, "download_artifact_sync", return_value=b"mp3-bytes") as mock_download:
            out = storage.download_audio_from_url(
                audio_url="https://storage.vapi.ai/x.mp3",
                provider="vapi",
                api_key="k",
                call_id="cid",
                artifact_type="mono-recording",
                min_duration_seconds=None,
            )
        mock_download.assert_called_once()
        assert out == b"mp3-bytes"

    def test_does_not_delegate_without_provider(self):
        from tfc.utils import storage
        from tracer.utils.vapi_recording import VapiRecordingService

        fake_response = Mock(status_code=200)
        fake_response.iter_content = Mock(return_value=[b"legacy-bytes"])
        with patch.object(VapiRecordingService, "download_artifact_sync") as mock_download, \
             patch("tfc.utils.storage._ssrf_safe_get") as mock_get:
            mock_get.return_value.__enter__ = Mock(return_value=fake_response)
            mock_get.return_value.__exit__ = Mock(return_value=False)
            with patch("tfc.utils.storage._ensure_min_duration", side_effect=lambda b, _: b):
                storage.download_audio_from_url(
                    audio_url="https://other-host.com/x.mp3",
                    api_key=None,
                    call_id=None,
                    artifact_type=None,
                    min_duration_seconds=None,
                )
        mock_download.assert_not_called()


class TestDownloadAudioFromUrlAsyncRouting:
    """simulate.temporal.utils.async_storage.download_audio_from_url_async delegates for Vapi."""

    @pytest.mark.asyncio
    async def test_delegates_to_service_when_authenticated(self):
        from simulate.temporal.utils import async_storage
        from tracer.utils.vapi_recording import VapiRecordingService

        async_mock = MagicMock()

        async def _fake_download(**kwargs):
            return b"mp3-bytes"

        with patch.object(VapiRecordingService, "download_artifact_async", side_effect=_fake_download) as mock_download:
            out = await async_storage.download_audio_from_url_async(
                audio_url=None,
                provider="vapi",
                api_key="k",
                call_id="cid",
                artifact_type="mono-recording",
            )
        mock_download.assert_called_once()
        assert out == b"mp3-bytes"

    @pytest.mark.asyncio
    async def test_raises_when_no_audio_url_and_not_authenticated(self):
        from simulate.temporal.utils import async_storage

        with pytest.raises(ValueError):
            await async_storage.download_audio_from_url_async(
                audio_url=None,
                provider=None,
                api_key=None,
                call_id=None,
                artifact_type=None,
            )
