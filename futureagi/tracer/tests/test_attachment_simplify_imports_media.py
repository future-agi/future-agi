"""Real Linux seccomp/PyAV, bounded scratch and recording planning tests."""

import importlib.util
import os
import struct
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1] / "services" / "attachments"


@pytest.fixture
def media(monkeypatch, tmp_path):
    for name, path in (
        ("tracer", ROOT.parents[1]),
        ("tracer.services", ROOT.parent),
        ("tracer.services.attachments", ROOT),
    ):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, name, package)
    modules = {}
    for short in ("contract", "recordings", "sandbox"):
        name = "tracer.services.attachments." + short
        spec = importlib.util.spec_from_file_location(name, ROOT / (short + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[short] = module
    from django.conf import settings

    if not settings.configured:
        settings.configure()
    monkeypatch.setattr(
        settings, "FI_ATTACHMENTS_WORK_DIR", str(tmp_path), raising=False
    )
    return types.SimpleNamespace(**modules)


def wav(path, *, seconds, rate=16000, channels=1):
    size = seconds * rate * channels * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        size + 36,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        rate * channels * 2,
        channels * 2,
        16,
        b"data",
        size,
    )
    with open(path, "wb") as output:
        output.write(header)
        # Sparse zeros make the fixture cheap; decoder still reads actual PCM.
        output.truncate(44 + size)


def test_real_seccomp_negative_controls(media):
    media.sandbox.self_test()


def test_canonical_probe_source_and_segment(media, tmp_path):
    source = tmp_path / "input.wav"
    wav(source, seconds=31)
    facts = media.sandbox.run("source", source, "audio/wav")
    assert facts == dict(
        mime_type="audio/wav",
        sample_rate=16000,
        channels=1,
        frame_count=31 * 16000,
        duration_ms=31000,
    )
    limits = media.recordings.Limits(
        8 * 1024**2, 8 * 1024**2, 30000, 1024**3, 7200000, 1024**3
    )
    plans = media.recordings.plan_segments(
        frame_count=facts["frame_count"], rate=16000, channels=1, limits=limits
    )
    with media.sandbox.pcm_stream(
        source, "audio/wav", facts, deadline=datetime.now(UTC) + timedelta(minutes=2)
    ) as pcm:
        for plan in plans:
            with (tmp_path / f"part-{plan.index}.wav").open("w+b") as part:
                media.sandbox.write_wav_segment(pcm, part, plan, facts)
                assert os.fstat(part.fileno()).st_size <= 8 * 1024**2
                probe = media.sandbox.probe(part.name)
                assert probe["frame_count"] == plan.frame_count
    assert len(plans) == 2 and plans[-1].duration_ms == 1000


@pytest.mark.parametrize(
    "mime,container,codec",
    [
        ("audio/ogg", "ogg", "libopus"),
        ("audio/mpeg", "mp3", "libmp3lame"),
        ("audio/mp4", "mp4", "aac"),
        ("audio/webm", "webm", "libopus"),
        ("audio/flac", "flac", "flac"),
    ],
)
def test_agreed_encoded_formats_normalize_inside_seccomp(
    media, tmp_path, mime, container, codec
):
    import av

    source = tmp_path / ("input." + container)
    with av.open(str(source), "w", format=container) as output:
        stream = output.add_stream(codec, rate=48000)
        stream.layout = "mono"
        for n in range(50):
            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            frame.sample_rate, frame.pts = 48000, n * 960
            frame.planes[0].update(bytes(frame.planes[0].buffer_size))
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    facts = media.sandbox.run("source", source, mime)
    assert facts["mime_type"] == mime
    assert facts["sample_rate"] == 48000 and facts["channels"] == 1
    limits = media.recordings.Limits(
        8 * 1024**2, 8 * 1024**2, 30000, 1024**3, 7200000, 1024**3
    )
    plans = media.recordings.plan_segments(
        frame_count=facts["frame_count"], rate=48000, channels=1, limits=limits
    )
    with media.sandbox.pcm_stream(
        source, mime, facts, deadline=datetime.now(UTC) + timedelta(minutes=2)
    ) as pcm:
        with (tmp_path / "normalized.wav").open("w+b") as part:
            media.sandbox.write_wav_segment(pcm, part, plans[0], facts)
            assert media.sandbox.probe(part.name)["frame_count"] == facts["frame_count"]


def test_one_hour_120_real_parts_not_browser_wait(media, tmp_path):
    source = tmp_path / "one-hour.wav"
    wav(source, seconds=3600)
    facts = media.sandbox.run("source", source, "audio/wav")
    limits = media.recordings.Limits(
        8 * 1024**2, 8 * 1024**2, 30000, 1024**3, 7200000, 1024**3
    )
    plans = media.recordings.plan_segments(
        frame_count=facts["frame_count"], rate=16000, channels=1, limits=limits
    )
    assert len(plans) == 120
    occurrences = []
    with media.sandbox.pcm_stream(
        source, "audio/wav", facts, deadline=datetime.now(UTC) + timedelta(minutes=5)
    ) as pcm:
        for plan in plans:
            part_path = tmp_path / "window.wav"
            with part_path.open("w+b") as part:
                media.sandbox.write_wav_segment(pcm, part, plan, facts)
                observed = media.sandbox.probe(part.name)
                assert observed["frame_count"] == 480000
                assert (
                    os.fstat(part.fileno()).st_size == plan.byte_length <= 8 * 1024**2
                )
                assert plan.duration_ms <= 30000
            part_path.unlink()
            occurrences.append((uuid4(), plan))
    manifest = media.recordings.build_manifest(
        recording_id=uuid4(),
        rate=16000,
        channels=1,
        direction="mixed",
        track_ids=[uuid4()],
        segments=occurrences,
        duration_ms=3600000,
        capture_complete=True,
    )
    assert manifest["duration_ms"] == 3600000 and len(manifest["segments"]) == 120


def test_discoverable_scratch_guard_prevents_live_cleanup(media):
    job_id = uuid4()
    media.sandbox.prepare_import_scratch(job_id, 1)
    with media.sandbox.import_execution(job_id, 1) as directory:
        with media.sandbox.scratch_file(directory, "source") as source:
            source.write(b"test")
            source.flush()
            assert media.sandbox.cleanup_import_scratch(job_id, 1) is False
    assert media.sandbox.cleanup_import_scratch(job_id, 1) is True


def test_missing_termination_proof_never_releases(media):
    assert media.sandbox.cleanup_import_scratch(uuid4(), 1) is False


def test_no_follow_scratch_cleanup(media, tmp_path):
    job_id = uuid4()
    media.sandbox.prepare_import_scratch(job_id, 1)
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    (tmp_path / "import" / str(job_id) / "1" / "source").symlink_to(outside)
    assert media.sandbox.cleanup_import_scratch(job_id, 1) is False
    assert outside.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "rate,channels", [(8000, 1), (44100, 2), (48000, 2), (96000, 2)]
)
def test_both_caps_even_if_configuration_is_relaxed(media, rate, channels):
    limits = media.recordings.Limits(
        100 * 1024**2, 100 * 1024**2, 60000, 1024**3, 7200000, 1024**3
    )
    plans = media.recordings.plan_segments(
        frame_count=rate * 61, rate=rate, channels=channels, limits=limits
    )
    assert all(
        plan.byte_length <= 8 * 1024**2 and plan.duration_ms <= 30000 for plan in plans
    )
    assert sum(plan.frame_count for plan in plans) == rate * 61


def test_L16_without_trusted_rate_is_explicitly_unqualified(media, tmp_path):
    source = tmp_path / "raw"
    source.write_bytes(bytes(100))
    with pytest.raises(media.contract.AttachmentError):
        media.sandbox.run("source", source, "audio/L16")
