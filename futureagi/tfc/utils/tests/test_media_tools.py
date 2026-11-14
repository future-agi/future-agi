"""Features that need ffmpeg fail with a named error when it is not installed.

The backend image can be built without ffmpeg (``FFMPEG_FLAVOR=none``). Each
test empties PATH, the way such an image looks to ``subprocess``, and checks
that the feature raises ``MediaToolUnavailable`` naming the tool and the image
to use, instead of a ``FileNotFoundError`` that callers turn into "corrupt
file" or "try again later". ``subprocess`` is patched to fail the test if a
call site still runs ffmpeg after the check.
"""

import base64
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tfc.utils import storage
from tfc.utils.media_tools import (
    FFMPEG_UNAVAILABLE,
    MediaToolUnavailable,
    ffmpeg_available,
    raise_non_retryable_if_media_tool_missing,
    require_ffmpeg,
)


@pytest.fixture
def no_ffmpeg(tmp_path, monkeypatch):
    """PATH holds only an empty directory: no ffmpeg, no ffprobe."""
    monkeypatch.setenv("PATH", str(tmp_path))
    ran = MagicMock(side_effect=AssertionError("ffmpeg must not be started"))
    with (
        patch.object(subprocess, "Popen", ran),
        patch.object(subprocess, "run", ran),
    ):
        yield ran


@pytest.fixture
def only_ffmpeg(tmp_path, monkeypatch):
    """An executable ``ffmpeg`` on PATH, but no ``ffprobe``."""
    tool = tmp_path / "ffmpeg"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    return str(tool)


def _assert_named(exc: BaseException, tool: str = "ffmpeg") -> None:
    message = str(exc)
    assert isinstance(exc, MediaToolUnavailable)
    assert isinstance(exc, ValueError)
    assert exc.code == FFMPEG_UNAVAILABLE == "ffmpeg_unavailable"
    assert message.startswith(f"{tool} is not installed in this backend image")
    assert "FFMPEG_FLAVOR=minimal" in message
    assert "try again" not in message.lower()
    assert "corrupt" not in message.lower()


class TestRequireFfmpeg:
    def test_missing_ffmpeg_names_tool_and_feature(self, no_ffmpeg):
        assert ffmpeg_available() is False
        with pytest.raises(MediaToolUnavailable) as caught:
            require_ffmpeg("audio format detection")
        _assert_named(caught.value)
        assert "so audio format detection is unavailable" in str(caught.value)
        assert caught.value.feature == "audio format detection"
        assert caught.value.tool == "ffmpeg"

    def test_missing_ffprobe_is_named_when_asked_for(self, only_ffmpeg):
        assert require_ffmpeg("x") == only_ffmpeg
        assert ffmpeg_available() is True
        assert ffmpeg_available(ffprobe=True) is False
        with pytest.raises(MediaToolUnavailable) as caught:
            require_ffmpeg("audio splitting", ffprobe=True)
        _assert_named(caught.value, tool="ffprobe")
        assert caught.value.tool == "ffprobe"


class TestStorageCallSites:
    def test_detect_audio_format(self, no_ffmpeg):
        with pytest.raises(MediaToolUnavailable) as caught:
            storage.detect_audio_format(b"RIFF....WAVE")
        _assert_named(caught.value)
        no_ffmpeg.assert_not_called()

    def test_convert_to_mp3_does_not_say_try_again_later(self, no_ffmpeg):
        with pytest.raises(MediaToolUnavailable) as caught:
            storage.convert_to_mp3(b"RIFF....WAVE")
        _assert_named(caught.value)
        no_ffmpeg.assert_not_called()

    def test_extract_video_thumbnail(self, no_ffmpeg):
        with pytest.raises(MediaToolUnavailable) as caught:
            storage.extract_video_thumbnail(b"\x00\x00\x00\x18ftypmp42")
        _assert_named(caught.value)
        no_ffmpeg.assert_not_called()

    def test_upload_audio_to_s3_keeps_the_named_error(self, no_ffmpeg):
        """Before: wrapped as "Audio upload failed. Try again." around a
        FileNotFoundError; the type is kept so Temporal can stop retrying."""
        with (
            patch.object(storage, "get_storage_client") as client,
            pytest.raises(MediaToolUnavailable) as caught,
        ):
            storage.upload_audio_to_s3({"bytes": b"RIFF....WAVE"})
        _assert_named(caught.value)
        client.assert_not_called()

    def test_upload_audio_to_s3_duration_reaches_named_error(self, no_ffmpeg):
        """With the `audio` extra installed (PyAV reads the duration),
        format detection is the first ffmpeg call and fails by name."""
        container = MagicMock(duration=2_000_000)
        fake_av = MagicMock()
        fake_av.open.return_value = container
        with (
            patch.object(storage, "av", fake_av),
            patch.object(storage, "get_storage_client") as client,
            pytest.raises(MediaToolUnavailable) as caught,
        ):
            storage.upload_audio_to_s3_duration(
                base64.b64encode(b"RIFF....WAVE").decode()
            )
        _assert_named(caught.value)
        client.assert_not_called()

    def test_upload_video_to_s3_still_stores_video_without_thumbnail(self, no_ffmpeg):
        """A thumbnail is optional: the video is stored, no thumbnail URL,
        and the log names ffmpeg."""
        with (
            patch.object(storage, "get_storage_client") as client,
            patch.object(storage, "ensure_bucket"),
            patch.object(storage, "get_object_url", return_value="http://s/v.mp4"),
            patch.object(storage.logger, "warning") as warning,
        ):
            result = storage.upload_video_to_s3(b"video-bytes", thumbnail=True)
        assert result == ("http://s/v.mp4", None)
        assert client.return_value.put_object.call_count == 1
        logged = " ".join(str(c.args[0]) for c in warning.call_args_list)
        assert "ffmpeg is not installed in this backend image" in logged
        no_ffmpeg.assert_not_called()


class TestMissingAudioExtra:
    def test_upload_audio_to_s3_duration_names_the_extra(self):
        """Without the `audio` extra (the default image), the duration step
        used to report every file as corrupted."""
        from tfc.utils.lazy_extras import _MissingExtra

        with (
            patch.object(storage, "av", _MissingExtra("av", "audio")),
            pytest.raises(ValueError) as caught,
        ):
            storage.upload_audio_to_s3_duration(
                base64.b64encode(b"RIFF....WAVE").decode()
            )
        message = str(caught.value)
        assert "optional `audio` dependency group" in message
        assert "corrupted" not in message


class TestTemporalMapping:
    def test_named_error_becomes_non_retryable(self):
        from temporalio.exceptions import ApplicationError

        cause = MediaToolUnavailable("call recording channel split")
        try:
            raise RuntimeError("activity failed") from cause
        except RuntimeError as wrapped:
            with pytest.raises(ApplicationError) as caught:
                raise_non_retryable_if_media_tool_missing(wrapped)
        assert caught.value.non_retryable is True
        assert caught.value.type == "MediaToolUnavailable"
        assert caught.value.message.startswith("ffmpeg is not installed")

    def test_other_errors_keep_their_retry_policy(self):
        raise_non_retryable_if_media_tool_missing(ValueError("network blip"))
