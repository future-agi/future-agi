"""Decode an AAC recording to PCM for the dead-air preprocessor's second try."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from tfc.utils.media_tools import MediaToolUnavailable

FEATURE = "dead-air audio decoding"


class RecordingDecodeError(ValueError):
    """A specific reason not to score this recording, rather than a format miss."""


def _require_tools() -> str:
    """Return the ffmpeg path, or name every missing tool in one hint.

    The retry runs both ffprobe and ffmpeg. An image built without either
    needs both, so don't stop at the first one."""
    ffmpeg = shutil.which("ffmpeg")
    missing = [] if ffmpeg else ["ffmpeg"]
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe")
    if missing or ffmpeg is None:
        error = MediaToolUnavailable(FEATURE, " and ".join(missing))
        if len(missing) > 1:
            hint = str(error).replace(" is not installed", " are not installed", 1)
            error.args = (hint,)
        raise error
    return ffmpeg


def aac_to_wav(audio_bytes: bytes) -> bytes:
    from tfc.utils.lazy_extras import soundfile
    from tfc.utils.storage import MAX_AUDIO_FILE_SIZE

    ffmpeg = _require_tools()
    if len(audio_bytes) > MAX_AUDIO_FILE_SIZE:
        raise RecordingDecodeError("Audio recording is too large")

    # A normal M4A can put its index at the end. Use a seekable input rather
    # than piping the recording through stdin. Both files disappear on error.
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "recording"
        wav = Path(directory) / "decoded.wav"
        source.write_bytes(audio_bytes)
        input_options = ["-protocol_whitelist", "file", "-format_whitelist", "mov,aac"]
        info = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                *input_options,
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=duration:format=format_name",
                "-of",
                "json",
                str(source),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        metadata = json.loads(info.stdout)
        streams = metadata.get("streams")
        if not streams:
            raise ValueError("No audio stream in recording")
        # AAC frames can extend past the end of an MP4 audio track. Use its
        # declared playback length; raw ADTS has no such end marker. Never
        # pad or use an estimated bitrate-based duration to shorten raw AAC.
        duration = (
            streams[0].get("duration")
            if "mov" in metadata["format"]["format_name"].split(",")
            else None
        )
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-xerror",
                "-v",
                "error",
                *input_options,
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                *(["-t", duration] if duration else []),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-fs",
                str(MAX_AUDIO_FILE_SIZE),
                str(wav),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        # -fs can finish successfully after writing a final packet across the
        # limit. Do not load or score a truncated recording.
        if wav.stat().st_size >= MAX_AUDIO_FILE_SIZE:
            raise RecordingDecodeError("Decoded audio recording is too large")
        if duration:
            # A front-indexed M4A can end cleanly between packets but before
            # its declared end. Allow 1 ms for container time-base rounding.
            pcm = soundfile.info(wav)
            if pcm.frames / pcm.samplerate + 0.001 < float(duration):
                raise RecordingDecodeError(
                    "Audio recording ends before its declared duration"
                )
        return wav.read_bytes()
