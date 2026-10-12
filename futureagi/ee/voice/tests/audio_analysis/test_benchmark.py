"""AC15: real CLI measurements, reproducible fixtures and independent stages."""

import json
import math
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ee.voice.benchmarks import audio_metrics as benchmark


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "ee.voice.benchmarks.audio_metrics", *map(str, args)],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )


def _assert_measurements(stage, repeat):
    assert stage["status"] == "measured"
    assert len(stage["runs"]) == repeat
    for index, run in enumerate(stage["runs"]):
        assert run["iteration"] == index + 1
        assert run["mode"] == ("cold" if index == 0 else "warm")
        for field in ("wall_seconds", "cpu_seconds", "peak_rss_bytes"):
            assert math.isfinite(run[field])
            assert run[field] >= 0
        assert run["peak_rss_bytes"] > 0


@pytest.fixture
def wav_fixture(tmp_path):
    path = tmp_path / "fixture.wav"
    sr = 8000
    t = np.arange(2 * sr) / sr
    y = (t < 1) * 0.3 * np.sin(2 * np.pi * 220 * t)
    y += np.random.default_rng(2094).normal(0, 0.002, len(t))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes((y * 32767).astype("<i2").tobytes())
    return path


def test_self_test_prints_measured_decode_pitch_snr_json():
    report = json.loads(_cli("--self-test").stdout)
    assert report["fixture"] == "synthetic_seed_2094"
    assert report["audio"]["duration_seconds"] == 10
    assert report["audio"]["sample_rate_hz"] == 16000
    assert report["repeat"] == 1
    assert list(report["stages"]) == ["decode", "pitch", "snr"]
    for stage in report["stages"].values():
        _assert_measurements(stage, 1)


def test_fixture_repeats_json_file_resample_and_vqi_skip(wav_fixture, tmp_path):
    output = tmp_path / "measurements.json"
    result = _cli(
        "--fixture",
        wav_fixture,
        "--stages",
        "decode,pitch,snr,resample,vqi",
        "--repeat",
        "3",
        "--json",
        output,
    )
    report = json.loads(result.stdout)
    assert json.loads(output.read_text()) == report
    assert report["audio"]["sample_rate_hz"] == 8000
    assert list(report["stages"]) == list(benchmark.STAGES)
    for name in ("decode", "pitch", "snr", "resample"):
        _assert_measurements(report["stages"][name], 3)
    assert report["stages"]["vqi"] == {"status": "skipped: no operator weights"}


def test_snr_selection_does_not_run_pitch(wav_fixture, monkeypatch, capsys):
    def unexpected_pitch(*args):
        pytest.fail("SNR benchmark must not include pYIN")

    monkeypatch.setattr(benchmark.helper, "_pitch_detail", unexpected_pitch)
    assert (
        benchmark.main(
            [
                "--fixture",
                str(wav_fixture),
                "--stages",
                "snr",
                "--repeat",
                "1",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert list(report["stages"]) == ["snr"]
    _assert_measurements(report["stages"]["snr"], 1)


def test_vqi_skip_does_not_decode_or_load_operator_weights(
    wav_fixture, monkeypatch, capsys
):
    def unexpected_decode(*args, **kwargs):
        pytest.fail("VQI skip must not run the analysis pipeline")

    monkeypatch.setattr(benchmark.decode, "decode_audio", unexpected_decode)
    monkeypatch.setenv(
        "VOICE_AUDIO_METRICS_DNSMOS_MODEL_PATH", "/missing/operator.onnx"
    )
    assert benchmark.main(["--fixture", str(wav_fixture), "--stages", "vqi"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stages"] == {"vqi": {"status": "skipped: no operator weights"}}


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--self-test", "--fixture", "fixture.wav"],
        ["--self-test", "--repeat", "0"],
        ["--self-test", "--repeat", "-1"],
        ["--self-test", "--stages", "unknown"],
        ["--self-test", "--stages", "decode,decode"],
        ["--self-test", "--stages", ""],
    ],
)
def test_invalid_arguments_fail_before_running(args):
    with pytest.raises(SystemExit) as exc:
        benchmark.main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize("platform,rss", [("darwin", 4096), ("linux", 4)])
def test_resource_accounting_normalizes_rss_and_sums_cpu(monkeypatch, platform, rss):
    usages = iter(
        [
            SimpleNamespace(ru_utime=10.0, ru_stime=2.0, ru_maxrss=0),
            SimpleNamespace(ru_utime=11.5, ru_stime=2.5, ru_maxrss=rss),
        ]
    )
    ticks = iter([100.0, 103.0])
    monkeypatch.setattr(benchmark.sys, "platform", platform)
    monkeypatch.setattr(benchmark.resource, "getrusage", lambda who: next(usages))
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: next(ticks))
    result, sample = benchmark._measure(lambda: "done", 1)
    assert result == "done"
    assert sample == {
        "iteration": 2,
        "mode": "warm",
        "wall_seconds": 3.0,
        "cpu_seconds": 2.0,
        "peak_rss_bytes": 4096,
    }
