"""The seccomp media worker: real child processes, no mocks of the boundary."""

import io
import signal
import struct
import subprocess
import sys
import wave

import pytest

from tracer.services.attachments import media_worker, sandbox
from tracer.services.attachments.contract import AttachmentError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="seccomp is Linux-only"
    ),
]


def wav(path, *, channels=2, rate=24000, frames=2400, sample=None):
    sample = sample or (lambda frame, channel: (frame % 1000) * (1 if channel else -1))
    data = b"".join(
        struct.pack("<h", sample(frame, channel))
        for frame in range(frames)
        for channel in range(channels)
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(data)
    return path


@pytest.mark.parametrize("operation", ["deny-socket", "deny-open", "deny-exec"])
def test_negative_controls_are_killed_by_seccomp(operation):
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(sandbox.WORKER),
            operation,
            "-1",
            "-1",
            "1",
        ],
        env={},
        capture_output=True,
        check=False,
    )
    assert completed.returncode == -signal.SIGSYS
    assert completed.stdout == completed.stderr == b""


def test_self_test_passes_only_with_active_confinement(monkeypatch):
    sandbox.self_test()
    # Removing the filter is the negative control: readiness must then fail.
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0),
    )
    with pytest.raises(AttachmentError, match="sandbox_unavailable"):
        sandbox.self_test()


def test_filter_program_rejects_unknown_architecture():
    with pytest.raises(media_worker.Unavailable):
        media_worker.filter_program("riscv64")
    for machine in ("x86_64", "aarch64"):
        program = media_worker.filter_program(machine)
        assert program[-2][3] == media_worker._KILL_PROCESS
        assert program[-1][3] == media_worker._ALLOW


def test_probe_source_segment_split_and_peaks(tmp_path):
    source = wav(tmp_path / "source.wav", frames=24000 * 3)
    assert sandbox.probe(source) == {
        "mime_type": "audio/wav",
        "sample_rate": 24000,
        "channels": 2,
        "frame_count": 72000,
    }
    assert sandbox.run("source", source)["duration_ms"] == 3000
    with open(tmp_path / "part.wav", "wb") as part:
        facts = sandbox.run(
            "segment", source, 24000, 24000, output=part, output_limit=96044
        )
    assert facts == {
        "sample_rate": 24000,
        "channels": 2,
        "frame_count": 24000,
        "byte_length": 96044,
    }
    with wave.open(str(tmp_path / "part.wav")) as reader:
        frames = reader.readframes(24000)
    first = struct.unpack("<hh", frames[:4])
    assert first == (-(24000 % 1000), 24000 % 1000)
    for channel in (0, 1):
        target = tmp_path / f"channel-{channel}.wav"
        with open(target, "wb") as output:
            sandbox.run(
                "split",
                tmp_path / "part.wav",
                channel,
                output=output,
                output_limit=44 + 24000 * 2,
            )
        with wave.open(str(target)) as reader:
            assert reader.getnchannels() == 1
            samples = struct.unpack("<24000h", reader.readframes(24000))
        sign = 1 if channel else -1
        assert samples == tuple(
            sign * ((24000 + index) % 1000) for index in range(24000)
        )
    with open(tmp_path / "peaks.bin", "wb") as output:
        facts = sandbox.run(
            "peaks", tmp_path / "part.wav", output=output, output_limit=1024 * 1024
        )
    assert [level["buckets"] for level in facts["levels"]] == [100, 10, 1]
    raw = (tmp_path / "peaks.bin").read_bytes()
    assert len(raw) == (100 + 10 + 1) * 2 * 4
    level0_left = struct.unpack("<200h", raw[:400])
    assert level0_left[:2] == (-((24000 + 239) % 1000), -(24000 % 1000))


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"<svg onload=alert(1)>",
        b"RIFF\xff\xff\xff\xffWAVE",
        b"ID3\x04" + b"\x00" * 64,
    ],
)
def test_malformed_input_rejected_without_detail(tmp_path, payload):
    path = tmp_path / "input"
    path.write_bytes(payload)
    with pytest.raises(AttachmentError) as error:
        sandbox.probe(path)
    assert error.value.code == "validation_rejected"
    assert str(tmp_path) not in str(error.value)


def test_appended_metadata_and_oversized_segment_rejected(tmp_path):
    source = wav(tmp_path / "s.wav", frames=48000)
    tampered = tmp_path / "t.wav"
    tampered.write_bytes(source.read_bytes() + b"LIST\x04\x00\x00\x00INFO")
    with pytest.raises(AttachmentError, match="validation_rejected"):
        sandbox.probe(tampered)
    long = wav(tmp_path / "long.wav", channels=1, rate=8000, frames=8000 * 31)
    with pytest.raises(AttachmentError, match="validation_rejected"):
        sandbox.probe(long)  # 31 s exceeds the 30 s segment ceiling


def test_output_limit_bounds_what_a_worker_can_write(tmp_path):
    source = wav(tmp_path / "s.wav", frames=24000)
    with open(tmp_path / "o", "wb") as output, pytest.raises(AttachmentError):
        # Limit below the true size: RLIMIT_FSIZE stops the write (SIGXFSZ).
        sandbox.run("segment", source, 0, 24000, output=output, output_limit=1000)


def test_segment_range_outside_source_rejected(tmp_path):
    source = wav(tmp_path / "s.wav", frames=1000)
    with (
        open(tmp_path / "o", "wb") as output,
        pytest.raises(AttachmentError, match="validation_rejected"),
    ):
        sandbox.run("segment", source, 900, 200, output=output, output_limit=9000)


def test_missing_input_is_unavailable_not_permissive(tmp_path):
    with pytest.raises(AttachmentError, match="validation_unavailable"):
        sandbox.probe(tmp_path / "missing")


def test_symlinked_input_is_not_followed(tmp_path):
    target = wav(tmp_path / "real.wav")
    link = tmp_path / "link.wav"
    link.symlink_to(target)
    with pytest.raises(AttachmentError, match="validation_unavailable"):
        sandbox.probe(link)


def test_worker_environment_is_empty_and_credentials_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "canary-secret-value")
    seen = {}
    real = subprocess.run

    def spy(command, **kwargs):
        seen.update(kwargs)
        return real(command, **kwargs)

    monkeypatch.setattr(sandbox.subprocess, "run", spy)
    sandbox.probe(wav(tmp_path / "x.wav"))
    assert seen["env"] == {}
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["close_fds"] is True


def test_in_process_parser_double_matches(tmp_path):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 160)
    path = tmp_path / "m.wav"
    path.write_bytes(stream.getvalue())
    assert media_worker.probe(path) == sandbox.probe(path)
