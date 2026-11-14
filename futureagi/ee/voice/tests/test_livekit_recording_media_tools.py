"""The LiveKit channel split names a missing ffmpeg before it reads the
recording; a stereo copy needs no ffmpeg."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tfc.utils.media_tools import MediaToolUnavailable

pytestmark = pytest.mark.unit


@pytest.fixture
def no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))


def test_channel_split_names_ffmpeg_before_reading_the_recording(no_ffmpeg):
    from ee.voice.services.livekit import recording

    with (
        patch.object(recording, "_s3_stream_iter") as source,
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("ffmpeg must not start")
        ),
        pytest.raises(MediaToolUnavailable) as caught,
    ):
        recording.stream_egress_to_s3(
            MagicMock(), "egress/call.ogg", "calls/c.mp3", ["-af", "pan=mono|c0=c0"]
        )
    assert str(caught.value).startswith("ffmpeg is not installed in this backend")
    assert "call recording channel split" in str(caught.value)
    source.assert_not_called()


def test_stereo_copy_needs_no_ffmpeg(no_ffmpeg):
    from ee.voice.services.livekit import recording

    body = MagicMock()
    with (
        patch.object(recording, "_create_s3_client") as client,
        patch.object(
            recording, "upload_stream_to_s3", return_value="s3://calls/c.ogg"
        ) as upload,
    ):
        client.return_value.get_object.return_value = {"Body": body}
        url = recording.stream_egress_to_s3(
            MagicMock(), "egress/call.ogg", "calls/c.ogg"
        )
    assert url == "s3://calls/c.ogg"
    upload.assert_called_once_with(body, "calls/c.ogg")
