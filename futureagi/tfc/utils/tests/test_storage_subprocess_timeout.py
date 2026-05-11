"""
Tests for subprocess timeout handling in storage audio utilities.

Verifies that detect_audio_format and convert_to_mp3 enforce timeouts
on ffmpeg subprocess calls to prevent indefinite hangs.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest


class TestDetectAudioFormatTimeout:
    """detect_audio_format should timeout after 10 seconds."""

    def test_raises_timeout_error_on_hang(self):
        """Hanging ffmpeg process should be killed and TimeoutError raised."""
        from tfc.utils.storage import detect_audio_format

        mock_process = MagicMock()
        mock_process.communicate.side_effect = subprocess.TimeoutExpired(
            cmd="ffmpeg", timeout=10
        )
        mock_process.kill = MagicMock()

        with patch("subprocess.Popen", return_value=mock_process):
            with pytest.raises(TimeoutError, match="exceeded 10s"):
                detect_audio_format(b"fake audio bytes")

        mock_process.kill.assert_called_once()

    def test_passes_timeout_10s_to_communicate(self):
        """communicate() must be called with timeout=10."""
        from tfc.utils.storage import detect_audio_format

        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b"",
            b"Input #0, mp3, from 'pipe:':\n  Duration: 00:00:05.00",
        )
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            detect_audio_format(b"fake audio bytes")

        mock_process.communicate.assert_called_once_with(
            input=b"fake audio bytes", timeout=10
        )

    def test_normal_audio_returns_format(self):
        """Valid audio should return detected format string."""
        from tfc.utils.storage import detect_audio_format

        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b"",
            b"Input #0, mp3, from 'pipe:':\n  Duration: 00:00:05.00",
        )
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            result = detect_audio_format(b"fake audio bytes")

        assert result == "mp3"

    def test_kills_then_reaps_on_timeout(self):
        """After timeout, kill() then communicate() to prevent zombie."""
        from tfc.utils.storage import detect_audio_format

        mock_process = MagicMock()
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10),
            (b"", b""),  # second call after kill for reaping
        ]
        mock_process.kill = MagicMock()

        with patch("subprocess.Popen", return_value=mock_process):
            with pytest.raises(TimeoutError):
                detect_audio_format(b"fake audio bytes")

        mock_process.kill.assert_called_once()
        assert mock_process.communicate.call_count == 2


class TestConvertToMp3Timeout:
    """convert_to_mp3 should timeout after 60 seconds."""

    def test_raises_timeout_error_internally(self):
        """The inner TimeoutError should be raised before the outer except catches it.

        We patch at the Popen level and verify TimeoutError is raised with
        the correct message. The outer except re-wraps as ValueError, but
        we verify the __cause__ chain preserves our TimeoutError.
        """
        from tfc.utils.storage import convert_to_mp3

        mock_process = MagicMock()
        mock_process.communicate.side_effect = subprocess.TimeoutExpired(
            cmd="ffmpeg", timeout=60
        )
        mock_process.kill = MagicMock()

        with patch("subprocess.Popen", return_value=mock_process):
            with pytest.raises(ValueError) as exc_info:
                convert_to_mp3(b"fake audio bytes")

        # Verify the ValueError wraps our TimeoutError (exception chain)
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert "exceeded 60s" in str(exc_info.value.__cause__)

        mock_process.kill.assert_called_once()

    def test_passes_timeout_60s_to_communicate(self):
        """communicate() must be called with timeout=60."""
        from tfc.utils.storage import convert_to_mp3

        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"mp3 output", b"")
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            convert_to_mp3(b"fake audio bytes")

        mock_process.communicate.assert_called_once_with(
            input=b"fake audio bytes", timeout=60
        )

    def test_normal_conversion_succeeds(self):
        """Valid audio should convert and return (bytes, 'mp3')."""
        from tfc.utils.storage import convert_to_mp3

        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"mp3 output bytes", b"")
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            result, fmt = convert_to_mp3(b"fake audio bytes")

        assert result == b"mp3 output bytes"
        assert fmt == "mp3"

    def test_kills_then_reaps_on_timeout(self):
        """After timeout, kill() then communicate() to prevent zombie."""
        from tfc.utils.storage import convert_to_mp3

        mock_process = MagicMock()
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
            (b"", b""),  # second call after kill for reaping
        ]
        mock_process.kill = MagicMock()

        with patch("subprocess.Popen", return_value=mock_process):
            with pytest.raises(ValueError):
                convert_to_mp3(b"fake audio bytes")

        mock_process.kill.assert_called_once()
        assert mock_process.communicate.call_count == 2
