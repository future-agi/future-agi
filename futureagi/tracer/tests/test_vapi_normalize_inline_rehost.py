"""
Tests for the inline Vapi recording rehost in ``normalize_vapi_data``.

The inline rehost is best-effort / non-blocking: on failure the original
R2 URL is left in place and no exception propagates.

Run with: pytest tracer/tests/test_vapi_normalize_inline_rehost.py -v
"""

from unittest.mock import patch

import pytest

from tracer.utils.vapi import normalize_vapi_data


# Example R2 provider URLs (the kind Vapi returns)
R2_COMBINED = "https://storage.vapi.ai/combined.mp3"
R2_CUSTOMER = "https://storage.vapi.ai/customer.mp3"
R2_ASSISTANT = "https://storage.vapi.ai/assistant.mp3"
R2_STEREO = "https://storage.vapi.ai/stereo.mp3"

# Fake S3 response from mock — uses a bucket recognized by
# VapiRecordingService.is_fagi_s3_url (fi-customer-data is in the default
# OWN_S3_BUCKETS list), so already-S3 URLs are correctly detected and skipped.
S3_COMBINED = "https://fi-customer-data.s3.amazonaws.com/call-123/mono_combined.mp3"
S3_CUSTOMER = "https://fi-customer-data.s3.amazonaws.com/call-123/mono_customer.mp3"
S3_ASSISTANT = "https://fi-customer-data.s3.amazonaws.com/call-123/mono_assistant.mp3"
S3_STEREO = "https://fi-customer-data.s3.amazonaws.com/call-123/stereo.mp3"

VAPI_LOG = {
    "id": "call-123",
    "status": "ended",
    "createdAt": "2026-07-17T12:00:00Z",
    "endedAt": "2026-07-17T12:05:00Z",
    "artifact": {
        "recording": {
            "mono": {
                "combinedUrl": R2_COMBINED,
                "customerUrl": R2_CUSTOMER,
                "assistantUrl": R2_ASSISTANT,
            },
            "stereoUrl": R2_STEREO,
        },
    },
    "costs": [],
    "messages": [],
    "customer": {},
}


def _make_fake_convert(url_map):
    """Build a side-effect for convert_audio_url_to_s3_sync that maps URL->S3."""

    def _fake_convert(call_id, audio_url, url_type, **kwargs):
        if audio_url in url_map:
            return url_map[audio_url], 1024
        return audio_url, 0

    return _fake_convert


class TestInlineRehostReplace:
    """Happy path: inline rehost replaces R2 URLs with S3 URLs."""

    def test_replaces_four_recording_keys(self):
        """All 4 Vapi recording URLs are replaced when convert succeeds."""
        url_map = {
            R2_COMBINED: S3_COMBINED,
            R2_CUSTOMER: S3_CUSTOMER,
            R2_ASSISTANT: S3_ASSISTANT,
            R2_STEREO: S3_STEREO,
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=_make_fake_convert(url_map),
        ):
            result = normalize_vapi_data(VAPI_LOG, api_key="test-key")

        attrs = result["span_attributes"]
        assert attrs["conversation.recording.mono.combined"] == S3_COMBINED
        assert attrs["conversation.recording.mono.customer"] == S3_CUSTOMER
        assert attrs["conversation.recording.mono.assistant"] == S3_ASSISTANT
        assert attrs["conversation.recording.stereo"] == S3_STEREO

    def test_sets_flat_aliases(self):
        """Flat recording_url / stereo_recording_url aliases are set."""
        url_map = {
            R2_COMBINED: S3_COMBINED,
            R2_STEREO: S3_STEREO,
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=_make_fake_convert(url_map),
        ):
            result = normalize_vapi_data(VAPI_LOG, api_key="test-key")

        attrs = result["span_attributes"]
        assert attrs["recording_url"] == S3_COMBINED
        assert attrs["stereo_recording_url"] == S3_STEREO

    def test_api_key_passthrough(self):
        """api_key is passed through to convert_audio_url_to_s3_sync."""
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
        ) as mock_convert:
            mock_convert.return_value = (S3_COMBINED, 1024)
            normalize_vapi_data(VAPI_LOG, api_key="super-secret-key")

        # At least one call should have received api_key="super-secret-key"
        for call_args in mock_convert.call_args_list:
            assert call_args.kwargs.get("api_key") == "super-secret-key"

    def test_project_id_scopes_each_rehosted_object(self):
        """The same Vapi call ID in two projects cannot share an S3 object."""
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            return_value=(S3_COMBINED, 1024),
        ) as mock_convert:
            normalize_vapi_data(VAPI_LOG, api_key="test-key", project_id="project-1")

        for call_args in mock_convert.call_args_list:
            assert call_args.kwargs["project_id"] == "project-1"

    def test_info_logs_do_not_include_call_payload_values(self):
        sensitive_log = {
            **VAPI_LOG,
            "customer": {"number": "+15551234567"},
            "transcript": "private conversation",
        }
        events = []

        def capture(event, **kwargs):
            events.append((event, kwargs))

        with patch("tracer.utils.vapi.logger.info", side_effect=capture), patch(
            "tracer.utils.vapi._extract_eval_attributes", return_value={}
        ), patch("tracer.utils.vapi._rehost_recording_urls_sync", return_value=(0, {})):
            normalize_vapi_data(sensitive_log, api_key="api-secret")

        rendered = str(events)
        assert "+15551234567" not in rendered
        assert "private conversation" not in rendered
        assert "api-secret" not in rendered


class TestInlineRehostSkipS3:
    """Already-S3 URLs are skipped — no rehost attempted."""

    def test_already_s3_urls_untouched(self):
        """When URLs are already S3, convert_audio_url_to_s3_sync is not called."""
        s3_log = {
            **VAPI_LOG,
            "artifact": {
                "recording": {
                    "mono": {
                        "combinedUrl": S3_COMBINED,
                        "customerUrl": S3_CUSTOMER,
                        "assistantUrl": S3_ASSISTANT,
                    },
                    "stereoUrl": S3_STEREO,
                },
            },
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
        ) as mock_convert:
            result = normalize_vapi_data(s3_log, api_key="test-key")

        mock_convert.assert_not_called()
        attrs = result["span_attributes"]
        # URLs unchanged
        assert attrs["conversation.recording.mono.combined"] == S3_COMBINED
        assert attrs["conversation.recording.stereo"] == S3_STEREO

    def test_mixed_s3_and_r2(self):
        """Only non-S3 URLs are rehosted; S3 URLs skipped."""
        r2_stereo = "https://storage.vapi.ai/stereo.mp3"
        s3_stereo = "https://fagi.s3.amazonaws.com/call-123/stereo.mp3"
        mixed_log = {
            **VAPI_LOG,
            "artifact": {
                "recording": {
                    "mono": {
                        "combinedUrl": S3_COMBINED,  # already S3
                    },
                    "stereoUrl": r2_stereo,  # needs rehost
                },
            },
        }
        url_map = {r2_stereo: s3_stereo}
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=_make_fake_convert(url_map),
        ) as mock_convert:
            result = normalize_vapi_data(mixed_log, api_key="test-key")

        # convert should have been called only for stereo
        assert mock_convert.call_count == 1
        args, kwargs = mock_convert.call_args
        assert kwargs.get("url_type") == "stereo"

        attrs = result["span_attributes"]
        assert attrs["conversation.recording.mono.combined"] == S3_COMBINED  # untouched
        assert attrs["conversation.recording.stereo"] == s3_stereo  # replaced


class TestInlineRehostFailure:
    """On failure (exception / network error), original URL is kept; no exception propagates."""

    def test_failure_keeps_original_url(self):
        """When convert raises, original URL is preserved and normalize_vapi_data does not raise."""
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=Exception("Network error"),
        ):
            # Must not raise
            result = normalize_vapi_data(VAPI_LOG, api_key="test-key")

        attrs = result["span_attributes"]
        # All original R2 URLs preserved
        assert attrs["conversation.recording.mono.combined"] == R2_COMBINED
        assert attrs["conversation.recording.mono.customer"] == R2_CUSTOMER
        assert attrs["conversation.recording.mono.assistant"] == R2_ASSISTANT
        assert attrs["conversation.recording.stereo"] == R2_STEREO

    def test_convert_returns_original_url(self):
        """When convert returns original URL (failure), it is left unchanged."""
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            return_value=(R2_COMBINED, 0),  # returned original
        ):
            result = normalize_vapi_data(VAPI_LOG, api_key="test-key")

        attrs = result["span_attributes"]
        assert attrs["conversation.recording.mono.combined"] == R2_COMBINED

    def test_no_api_key_still_works(self):
        """normalize_vapi_data works without api_key (non-authenticated fallback)."""
        url_map = {R2_COMBINED: S3_COMBINED}
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=_make_fake_convert(url_map),
        ):
            result = normalize_vapi_data(VAPI_LOG)  # no api_key

        attrs = result["span_attributes"]
        assert attrs["conversation.recording.mono.combined"] == S3_COMBINED


class TestInlineRehostNoRecordingKeys:
    """Edge cases: no recording URLs in the log."""

    def test_no_artifact(self):
        """Log without artifact key does not crash."""
        log = {"id": "no-artifact", "status": "ended", "costs": [], "messages": []}
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
        ) as mock_convert:
            result = normalize_vapi_data(log, api_key="test-key")

        mock_convert.assert_not_called()
        assert result["id"] == "no-artifact"

    def test_no_recording_attribute(self):
        """Log with artifact but no recording does not crash."""
        log = {
            **VAPI_LOG,
            "artifact": {"logUrl": "https://vapi.ai/logs/123"},
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
        ) as mock_convert:
            result = normalize_vapi_data(log, api_key="test-key")

        mock_convert.assert_not_called()
        assert result["id"] == "call-123"


class TestInlineRehostBytesAndMirror:
    """Tests for rehost_bytes_uploaded in output dict and inline mirror call."""

    def test_rehost_bytes_uploaded_in_output(self):
        """rehost_bytes_uploaded is populated with sum of convert return values."""
        url_map = {
            R2_COMBINED: S3_COMBINED,
            R2_STEREO: S3_STEREO,
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=_make_fake_convert(url_map),
        ):
            result = normalize_vapi_data(VAPI_LOG, api_key="test-key")

        # 2 URLs converted × 1024 bytes each = 2048
        assert result.get("rehost_bytes_uploaded") == 2048

    def test_rehost_bytes_uploaded_zero_when_no_urls(self):
        """rehost_bytes_uploaded is 0 when there are no recording URLs."""
        log = {"id": "no-urls", "status": "ended", "costs": [], "messages": []}
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
        ) as mock_convert:
            result = normalize_vapi_data(log, api_key="test-key")

        mock_convert.assert_not_called()
        assert result.get("rehost_bytes_uploaded") == 0

    def test_rehost_bytes_uploaded_zero_when_all_already_s3(self):
        """rehost_bytes_uploaded is 0 when all URLs are already S3."""
        s3_log = {
            **VAPI_LOG,
            "artifact": {
                "recording": {
                    "mono": {"combinedUrl": S3_COMBINED},
                    "stereoUrl": S3_STEREO,
                },
            },
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
        ) as mock_convert:
            result = normalize_vapi_data(s3_log, api_key="test-key")

        mock_convert.assert_not_called()
        assert result.get("rehost_bytes_uploaded") == 0

    def test_mirror_called_inline_when_urls_converted(self):
        """mirror_s3_url_to_consumer_fields is called with correct args when URLs are converted."""
        url_map = {
            R2_COMBINED: S3_COMBINED,
            R2_STEREO: S3_STEREO,
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=_make_fake_convert(url_map),
        ), patch(
            "tracer.utils.vapi_recording.VapiRecordingService.mirror_s3_url_to_consumer_fields",
        ) as mock_mirror:
            result = normalize_vapi_data(VAPI_LOG, api_key="test-key")

        mock_mirror.assert_called_once()
        call_args = mock_mirror.call_args[1]
        assert "call_id" in call_args
        assert call_args["call_id"] == "call-123"
        assert "attrs" in call_args
        assert "s3_url_by_url_type" in call_args
        assert call_args["s3_url_by_url_type"] == {
            "mono_combined": S3_COMBINED,
            "stereo": S3_STEREO,
        }

    def test_mirror_not_called_when_no_conversions(self):
        """mirror_s3_url_to_consumer_fields is NOT called when no URLs are converted."""
        log = {"id": "no-urls", "status": "ended", "costs": [], "messages": []}
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
        ), patch(
            "tracer.utils.vapi_recording.VapiRecordingService.mirror_s3_url_to_consumer_fields",
        ) as mock_mirror:
            result = normalize_vapi_data(log, api_key="test-key")

        mock_mirror.assert_not_called()

    def test_rehost_uploads_maps_url_type_to_bytes(self):
        """rehost_uploads carries per-url-type byte counts for idempotent billing."""
        url_map = {
            R2_COMBINED: S3_COMBINED,
            R2_STEREO: S3_STEREO,
        }
        with patch(
            "tracer.utils.vapi.convert_audio_url_to_s3_sync",
            side_effect=_make_fake_convert(url_map),
        ):
            result = normalize_vapi_data(VAPI_LOG, api_key="test-key")

        assert result.get("rehost_uploads") == {
            "mono_combined": 1024,
            "stereo": 1024,
        }
        # total is the sum of the per-type map
        assert result.get("rehost_bytes_uploaded") == sum(result["rehost_uploads"].values())
