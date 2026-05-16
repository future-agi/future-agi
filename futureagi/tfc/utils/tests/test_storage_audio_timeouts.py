import subprocess
from unittest.mock import Mock, patch

import pytest

from tfc.utils.storage import convert_to_mp3, detect_audio_format


def test_detect_audio_format_kills_and_reaps_ffmpeg_on_timeout():
    process = Mock()
    process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10),
        (b"", b""),
    ]

    with patch("tfc.utils.storage.subprocess.Popen", return_value=process):
        with pytest.raises(TimeoutError):
            detect_audio_format(b"audio-bytes")

    process.kill.assert_called_once()
    process.communicate.assert_any_call(input=b"audio-bytes", timeout=10)
    process.communicate.assert_any_call()


def test_convert_to_mp3_kills_and_reaps_ffmpeg_on_timeout():
    process = Mock()
    process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
        (b"", b""),
    ]

    with patch("tfc.utils.storage.subprocess.Popen", return_value=process):
        with pytest.raises(ValueError) as exc_info:
            convert_to_mp3(b"audio-bytes")

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    process.kill.assert_called_once()
    process.communicate.assert_any_call(input=b"audio-bytes", timeout=60)
    process.communicate.assert_any_call()
