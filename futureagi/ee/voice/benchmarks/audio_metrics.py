"""Measure local audio stages without downloading weights or running inference.

Run from futureagi/: python -m ee.voice.benchmarks.audio_metrics --self-test
The pitch/SNR stages call the same DSP primitives as pitch_snr's combined
wrapper, separately, so an SNR measurement does not include another pYIN pass.
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import resource
import sys
import time
import wave
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from ee.voice.services import audio_metrics as helper
from ee.voice.services.audio_analysis import decode, pitch_snr

STAGES = ("decode", "pitch", "snr", "resample", "vqi")
_T = TypeVar("_T")


def _stage_list(value: str) -> list[str]:
    stages = [stage.strip() for stage in value.split(",")]
    if any(stage not in STAGES for stage in stages):
        raise argparse.ArgumentTypeError(f"stages must be from {','.join(STAGES)}")
    if len(set(stages)) != len(stages):
        raise argparse.ArgumentTypeError("stages must not contain duplicates")
    return stages


def _positive_int(value: str) -> int:
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("repeat must be at least 1")
    return count


def _synthetic_fixture() -> bytes:
    """Ten seconds of seeded noise, voiced harmonics and pauses, entirely in RAM."""
    sr = 16000
    t = np.arange(10 * sr) / sr
    voiced = (t % 2) < 1
    y = voiced * (0.3 * np.sin(2 * np.pi * 220 * t))
    y += voiced * (0.05 * np.sin(2 * np.pi * 440 * t))
    y += np.random.default_rng(2094).normal(0, 0.002, len(t))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes((y * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _measure(operation: Callable[[], _T], iteration: int) -> tuple[_T, dict[str, Any]]:
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.perf_counter()
    result = operation()
    wall_seconds = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_SELF)
    # macOS reports bytes; Linux reports KiB. This is a process high-water mark,
    # not an allocation delta or an independently measured per-stage maximum.
    rss_bytes = int(after.ru_maxrss * (1 if sys.platform == "darwin" else 1024))
    return result, {
        "iteration": iteration + 1,
        "mode": "cold" if iteration == 0 else "warm",
        "wall_seconds": wall_seconds,
        "cpu_seconds": (after.ru_utime - before.ru_utime)
        + (after.ru_stime - before.ru_stime),
        "peak_rss_bytes": rss_bytes,
    }


def _pitch(audio: decode.DecodedAudio) -> object:
    import librosa

    return helper._average_pitch_hz(librosa, audio.waveform, audio.sample_rate_hz)


def _snr(audio: decode.DecodedAudio) -> object:
    power = helper._frame_power(audio.waveform, audio.sample_rate_hz)
    return pitch_snr._estimated_snr_db_detail(power)[0]


def _resample(audio: decode.DecodedAudio) -> object:
    from ee.voice.services.audio_analysis.vqi import resample_for_vqi

    return resample_for_vqi(audio.waveform, audio.sample_rate_hz)


def main(argv: Sequence[str] | None = None) -> int:
    """Run selected stages and write measured results as JSON, always to stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, help="local encoded audio file")
    source.add_argument(
        "--self-test", action="store_true", help="run synthetic decode/pitch/snr once"
    )
    parser.add_argument(
        "--stages",
        type=_stage_list,
        default="decode,pitch,snr,resample",
        help="comma-separated stages; self-test always uses decode,pitch,snr",
    )
    parser.add_argument(
        "--repeat",
        type=_positive_int,
        default=3,
        help="calls per stage, including the cold call (self-test always uses 1)",
    )
    parser.add_argument(
        "--channel-index",
        type=int,
        choices=(0, 1),
        help="explicit target channel, required for stereo fixtures",
    )
    parser.add_argument("--json", type=Path, help="also write the report to this path")
    args = parser.parse_args(argv)
    stages = ["decode", "pitch", "snr"] if args.self_test else args.stages
    repeat = 1 if args.self_test else args.repeat
    if args.self_test:
        data = _synthetic_fixture()
    else:
        decode.validate_encoded_size(args.fixture.stat().st_size)
        with args.fixture.open("rb") as fixture:
            data = fixture.read(decode.MAX_ENCODED_BYTES + 1)
        decode.validate_encoded_size(len(data))

    report: dict[str, Any] = {
        "fixture": "synthetic_seed_2094" if args.self_test else str(args.fixture),
        "repeat": repeat,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "measurement_notes": {
            "cold": "First call of each stage in this CLI process; later calls are warm. "
            "Imports/JIT inside the call are included; disk caches are not cleared. "
            "Earlier stages may have loaded shared dependencies.",
            "cpu_seconds": "Process user + system CPU deltas from getrusage(RUSAGE_SELF).",
            "peak_rss_bytes": "Process lifetime high-water RSS after each call, "
            "including earlier stages; ru_maxrss normalized to bytes.",
            "preparation": "Fixture generation/read is untimed. Decode runs first "
            "as preparation if needed, and is only timed when selected.",
            "scope": "Observed measurements, not performance targets.",
        },
        "stages": {},
    }
    audio: decode.DecodedAudio | None = None
    # Decode once as prerequisite, or measure all its repetitions before DSP.
    if any(stage != "vqi" for stage in stages):
        operation = partial(decode.decode_audio, data, channel_index=args.channel_index)
        if "decode" in stages:
            runs = []
            for iteration in range(repeat):
                audio, sample = _measure(operation, iteration)
                runs.append(sample)
            report["stages"]["decode"] = {
                "status": "measured",
                "operation": "decode.decode_audio",
                "runs": runs,
            }
        else:
            audio = operation()
        assert audio is not None
        report["audio"] = {
            "duration_seconds": audio.duration_seconds,
            "sample_rate_hz": audio.sample_rate_hz,
            "sample_count": audio.target_sample_count,
            "original_channel_count": audio.original_channel_count,
            "sha256": audio.sha256,
        }

    operations = {"pitch": _pitch, "snr": _snr, "resample": _resample}
    descriptions = {
        "pitch": "audio_metrics._average_pitch_hz (pYIN used by pitch_snr)",
        "snr": "audio_metrics._frame_power + pitch_snr._estimated_snr_db_detail",
        "resample": "vqi.resample_for_vqi (soxr_hq, 16000 Hz)",
    }
    for stage in stages:
        if stage == "vqi":
            report["stages"][stage] = {"status": "skipped: no operator weights"}
        elif stage != "decode":
            assert audio is not None
            runs = []
            for iteration in range(repeat):
                result, sample = _measure(partial(operations[stage], audio), iteration)
                del result
                runs.append(sample)
            report["stages"][stage] = {
                "status": "measured",
                "operation": descriptions[stage],
                "runs": runs,
            }

    output = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.json is not None:
        args.json.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
