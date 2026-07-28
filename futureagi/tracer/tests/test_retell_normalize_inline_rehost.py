"""Regression tests for Retell's inline recording rehost normalization."""

from unittest.mock import patch

from tracer.utils.retell import normalize_retell_data


RETELL_MONO_URL = "https://retell-cdn.example.test/call-123-mono.wav"
RETELL_STEREO_URL = "https://retell-cdn.example.test/call-123-stereo.wav"
DURABLE_MONO_URL = (
    "https://fi-customer-data.s3.amazonaws.com/call-recordings/project-1/"
    "retell/call-123/mono_combined.wav"
)
DURABLE_STEREO_URL = (
    "https://fi-customer-data.s3.amazonaws.com/call-recordings/project-1/"
    "retell/call-123/stereo.wav"
)


def _retell_log(**overrides):
    log = {
        "call_id": "call-123",
        "call_status": "ended",
        "recording_url": RETELL_MONO_URL,
        "recording_multi_channel_url": RETELL_STEREO_URL,
        "call_cost": {"combined_cost": 0.01, "product_costs": []},
    }
    log.update(overrides)
    return log


def test_rehosts_both_retell_recordings_with_provider_and_project_scope():
    def convert(*, audio_url, **kwargs):
        return {
            RETELL_MONO_URL: (DURABLE_MONO_URL, 100),
            RETELL_STEREO_URL: (DURABLE_STEREO_URL, 200),
        }[audio_url]

    with patch(
        "tracer.utils.retell.convert_audio_url_to_s3_sync", side_effect=convert
    ) as mock_convert:
        result = normalize_retell_data(_retell_log(), project_id="project-1")

    attrs = result["span_attributes"]
    assert attrs["conversation.recording.mono.combined"] == DURABLE_MONO_URL
    assert attrs["conversation.recording.stereo"] == DURABLE_STEREO_URL
    assert result["rehost_uploads"] == {"mono_combined": 100, "stereo": 200}
    assert result["rehost_bytes_uploaded"] == 300

    assert mock_convert.call_count == 2
    assert {call.kwargs["url_type"] for call in mock_convert.call_args_list} == {
        "mono_combined",
        "stereo",
    }
    for call in mock_convert.call_args_list:
        assert call.kwargs["call_id"] == "call-123"
        assert call.kwargs["provider"] == "retell"
        assert call.kwargs["project_id"] == "project-1"
        assert call.kwargs["artifact_type"] == call.kwargs["url_type"]


def test_partial_rehost_failure_preserves_source_and_continues_other_artifact():
    def convert(*, audio_url, **kwargs):
        if audio_url == RETELL_MONO_URL:
            raise RuntimeError("download failed")
        return DURABLE_STEREO_URL, 200

    with patch("tracer.utils.retell.convert_audio_url_to_s3_sync", side_effect=convert):
        result = normalize_retell_data(_retell_log(), project_id="project-1")

    attrs = result["span_attributes"]
    assert attrs["conversation.recording.mono.combined"] == RETELL_MONO_URL
    assert attrs["conversation.recording.stereo"] == DURABLE_STEREO_URL
    assert result["rehost_uploads"] == {"stereo": 200}


def test_no_project_or_recording_urls_performs_no_conversion():
    with patch("tracer.utils.retell.convert_audio_url_to_s3_sync") as mock_convert:
        no_project = normalize_retell_data(_retell_log())
        no_urls = normalize_retell_data(
            _retell_log(recording_url=None, recording_multi_channel_url=None),
            project_id="project-1",
        )

    mock_convert.assert_not_called()
    assert no_project["rehost_uploads"] == {}
    assert no_urls["rehost_uploads"] == {}


def test_already_owned_recording_url_is_skipped():
    log = _retell_log(
        recording_url=DURABLE_MONO_URL,
        recording_multi_channel_url=None,
    )
    with patch("tracer.utils.retell.convert_audio_url_to_s3_sync") as mock_convert:
        result = normalize_retell_data(log, project_id="project-1")

    mock_convert.assert_not_called()
    assert result["span_attributes"]["conversation.recording.mono.combined"] == DURABLE_MONO_URL
    assert result["rehost_uploads"] == {}
