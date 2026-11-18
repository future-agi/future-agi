"""Decode an AAC recording to PCM for the dead-air preprocessor's second try."""

import json
import subprocess
import tempfile
from pathlib import Path

from tfc.utils.media_tools import require_ffmpeg


def aac_to_wav(audio_bytes: bytes) -> bytes:
    from tfc.utils.storage import MAX_AUDIO_FILE_SIZE

    ffmpeg = require_ffmpeg("dead-air audio decoding", ffprobe=True)
    if len(audio_bytes) > MAX_AUDIO_FILE_SIZE:
        raise ValueError("Audio recording is too large")

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
        duration = streams[0].get("duration")
        # AAC frames can extend past the end of an MP4 audio track. Use its
        # declared playback length; raw ADTS has no such end marker. Never
        # pad or use an estimated bitrate-based duration to shorten raw AAC.
        end = (
            ["-t", duration]
            if duration and "mov" in metadata["format"]["format_name"].split(",")
            else []
        )
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
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
                *end,
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
            raise ValueError("Decoded audio recording is too large")
        return wav.read_bytes()
