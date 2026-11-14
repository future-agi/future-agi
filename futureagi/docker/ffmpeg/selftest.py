#!/usr/bin/env python3
"""Build-time self-test for the minimal FFmpeg build (stdlib only).

Replays the exact command lines the backend runs (see the header of
configure-minimal.sh for the call sites) against the sample files next to
this script, and fails the image build if any of them regresses:

  * every format tfc/utils/storage.py maps in _FORMAT_TO_MIME is sniffed by
    detect_audio_format() and converted by convert_to_mp3(), including inputs
    that carry cover art;
  * pydub's probe + decode commands work for every audio sample;
  * pydub's mp3 export, the LiveKit channel split and audioread's s16le
    output work;
  * video thumbnails work for H.264, HEVC and VP9.

The samples (samples/, ~100 KB, 0.4 s tones and 2 s test patterns) were made
with a full FFmpeg build; regenerate them with any FFmpeg that has the
encoders named in the SAMPLES table.

Usage: selftest.py [SAMPLES_DIR]   (ffmpeg and ffprobe must be on PATH)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# file -> demuxer names detect_audio_format() may return for it (the first
# comma-separated name FFmpeg prints on the "Input #0" line). The keys cover
# every _FORMAT_TO_MIME format of tfc/utils/storage.py.
SAMPLES = {
    "audio.mp3": {"mp3"},  # mp3 (libmp3lame)
    "audio-cover.mp3": {"mp3"},  # + ID3 APIC cover (PNG)
    "audio.mpeg": {"mpeg"},  # MPEG-PS, mp2 audio ("mpeg")
    "audio.wav": {"wav"},  # pcm_s16le
    "audio-alaw.wav": {"wav"},  # pcm_alaw
    "audio-opus.ogg": {"ogg"},  # opus
    "audio-vorbis.ogg": {"ogg"},  # vorbis
    "audio.flac": {"flac"},
    "audio.aac": {"aac"},  # ADTS
    "audio.m4a": {"mov"},  # AAC in MP4 ("m4a")
    "audio-cover.m4a": {"mov"},  # + cover art
    "audio.webm": {"matroska"},  # opus in WebM ("webm")
    "audio.wma": {"asf"},  # wmav2 ("wma")
    "audio.aiff": {"aiff"},  # pcm_s16be ("aiff", "aif")
    "audio.au": {"au"},  # Sun AU, pcm_mulaw ("au")
}
VIDEOS = ("video-h264.mp4", "video-hevc.mp4", "video-vp9.webm")


def run(cmd, data=None, *, timeout=60):
    proc = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        sys.exit(
            f"FAIL {' '.join(cmd)}\n{proc.stderr.decode(errors='replace')[-2000:]}"
        )
    return proc


def detect_format(data):  # tfc/utils/storage.py detect_audio_format
    proc = subprocess.run(
        [FFMPEG, "-i", "-", "-f", "ffmetadata", "-"],
        input=data,
        capture_output=True,
        timeout=10,
    )
    if proc.returncode != 0:
        sys.exit(f"FAIL detect_audio_format\n{proc.stderr.decode()[-1500:]}")
    for line in proc.stderr.decode().split("\n"):
        if "Input #0" in line:
            return line.split(",")[1].strip().split(" ")[-1]
    return None


def convert_to_mp3(data):  # tfc/utils/storage.py convert_to_mp3
    out = run(
        [FFMPEG, "-i", "-", "-f", "mp3", "-acodec", "libmp3lame", "-ab", "192k", "-"],
        data,
    ).stdout
    if len(out) < 500 or detect_format(out) != "mp3":
        sys.exit(f"FAIL convert_to_mp3 produced {len(out)} bytes of non-mp3")
    return out


def pydub_decode(data):  # pydub AudioSegment.from_file(BytesIO(...))
    info = json.loads(
        run(
            [
                FFPROBE,
                "-of",
                "json",
                "-v",
                "info",
                "-show_format",
                "-show_streams",
                "-read_ahead_limit",
                "-1",
                "cache:pipe:0",
            ],
            data,
        ).stdout
    )
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not audio:
        sys.exit(f"FAIL ffprobe found no audio stream: {info}")
    bits = audio[0].get("bits_per_sample") or 16
    if audio[0].get("sample_fmt") == "fltp":
        bits = 16
    codec = "pcm_u8" if bits == 8 else f"pcm_s{bits}le"
    out = run(
        [
            FFMPEG,
            "-y",
            "-read_ahead_limit",
            "-1",
            "-i",
            "cache:pipe:0",
            "-acodec",
            codec,
            "-vn",
            "-f",
            "wav",
            "-",
        ],
        data,
    ).stdout
    if out[:4] != b"RIFF":
        sys.exit(f"FAIL pydub decode ({codec}) did not produce WAV")


def main(samples_dir):
    if not (FFMPEG and FFPROBE):
        sys.exit("FAIL ffmpeg/ffprobe not on PATH")

    for name, expected in SAMPLES.items():
        with open(os.path.join(samples_dir, name), "rb") as f:
            data = f.read()
        detected = detect_format(data)
        if detected not in expected:
            sys.exit(f"FAIL {name}: detected {detected!r}, expected {expected}")
        convert_to_mp3(data)
        pydub_decode(data)
        print(f"ok  {name}: {detected}")

    with open(os.path.join(samples_dir, "audio.wav"), "rb") as f:
        wav = f.read()
    # ee/voice/services/livekit/recording.py (channel extraction + VBR mp3)
    for pan in ("pan=mono|c0=c0", "pan=mono|c0=c1"):
        stereo = run(
            [FFMPEG, "-i", "-", "-ac", "2", "-f", "wav", "-acodec", "pcm_s16le", "-"],
            wav,
        ).stdout
        run(
            [
                FFMPEG,
                "-i",
                "-",
                "-filter_complex",
                pan,
                "-f",
                "mp3",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                "-",
            ],
            stereo,
        )

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.wav"), os.path.join(tmp, "out.mp3")
        with open(src, "wb") as f:
            f.write(wav)
        # pydub AudioSegment.export(format="mp3")
        run([FFMPEG, "-y", "-f", "wav", "-i", src, "-f", "mp3", dst])
        with open(dst, "rb") as f:
            if detect_format(f.read()) != "mp3":
                sys.exit("FAIL pydub export did not write mp3")
        # librosa -> audioread FFmpegAudioFile (EXTRAS=audio): raw s16le
        if len(run([FFMPEG, "-i", src, "-f", "s16le", "-"]).stdout) < 1000:
            sys.exit("FAIL s16le output is empty")

        # tfc/utils/storage.py video thumbnail
        for name in VIDEOS:
            thumb = os.path.join(tmp, f"{name}.jpg")
            run(
                [
                    FFMPEG,
                    "-i",
                    os.path.join(samples_dir, name),
                    "-ss",
                    "00:00:01",
                    "-vframes",
                    "1",
                    "-vf",
                    "scale=320:240",
                    "-f",
                    "image2",
                    "-y",
                    thumb,
                ]
            )
            with open(thumb, "rb") as f:
                if f.read(2) != b"\xff\xd8":
                    sys.exit(f"FAIL {name}: thumbnail is not a JPEG")
            print(f"ok  {name}: thumbnail")

    print("ffmpeg minimal self-test: OK")


if __name__ == "__main__":
    main(
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
    )
