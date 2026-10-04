"""Seed-2094 synthetic audio; generate files and truth sidecars only in test temp dirs.

No customer speech or downloaded assets. Large benchmark fixtures are opt-in.
"""

import hashlib
import json
from pathlib import Path

import numpy as np

SEED = 2094


def voice(kind="A", *, sr=16000, seconds=10.0, continuous=False):
    t = np.arange(round(sr * seconds)) / sr
    f0, harmonics, vibrato, on, off = (
        (220.0, 12, 5.0, 1.2, 0.6) if kind == "A" else (140.0, 8, 6.0, 0.9, 0.8)
    )
    phase = (
        2
        * np.pi
        * f0
        * (t - 0.01 * np.cos(2 * np.pi * vibrato * t) / (2 * np.pi * vibrato))
    )
    y = sum(
        (1 if kind == "A" else (-1) ** n) * np.sin(n * phase) / n
        for n in range(1, harmonics + 1)
    )
    y *= 0.3
    offset = 0 if kind == "A" else 0.9  # a controlled 0.3 s overlap with A
    mask = ((t - offset) % (on + off) < on) & (t >= offset)
    if continuous:
        mask[:] = True
    y *= mask
    boundaries = np.diff(np.r_[False, mask, False].astype(int))
    spans = [
        (int(a) / sr, int(b) / sr)
        for a, b in zip(
            np.flatnonzero(boundaries == 1),
            np.flatnonzero(boundaries == -1),
            strict=True,
        )
    ]
    return y.astype(np.float32), {
        "seed": SEED,
        "f0_hz": f0,
        "speech_spans": spans,
        "sample_rate_hz": sr,
        "sample_count": len(y),
    }


def generate(directory, *, sr=16000, seconds=10.0, include_mp3=True):
    import soundfile as sf

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    a, truth_a = voice("A", sr=sr, seconds=seconds)
    b, truth_b = voice("B", sr=sr, seconds=seconds)
    rng = np.random.default_rng(SEED)
    cases = {
        "A_mono_16k": (a, truth_a),
        "B_mono_16k": (b, truth_b),
        "AB_stereo_c0A_c1B_16k": (
            np.column_stack((a, b)),
            {"channel_roles": ["A", "B"]},
        ),
        "AB_stereo_c0B_c1A_16k": (
            np.column_stack((b, a)),
            {"channel_roles": ["B", "A"]},
        ),
        "AB_opposite_phase_stereo": (
            np.column_stack((a, -a)),
            {"channel_roles": ["A", "negative_A"]},
        ),
        "AB_mixed_mono_16k": ((a + b) / 2, {"reason": "unknown_agent_track"}),
        "crosstalk_flagged": ((a + b) / 2, {"reason": "mixed_speakers"}),
        "digital_silence_10s": (np.zeros_like(a), {"reason": "no_voice"}),
        "dither_silence_10s": (
            rng.normal(0, 10 ** (-90 / 20), len(a)),
            {"reason": "no_voice"},
        ),
        "subsecond_0p9s": (a[: round(0.9 * sr)], {"reason": "insufficient_audio"}),
        "white_noise_10s": (
            rng.normal(0, 0.1, len(a)),
            {"pitch_reason": "no_voiced_frames"},
        ),
        "voice_clipped_10s": (np.clip(a * 3, -1, 1), {}),
        "zero_frames": (np.zeros(0), {"reason": "empty_audio"}),
        "steady_tone_10s": (
            0.5 * np.sin(2 * np.pi * 220 * np.arange(len(a)) / sr),
            {"snr_reason": "no_noise_floor"},
        ),
    }
    cases["voice_no_pause_10s"] = voice("A", sr=sr, seconds=seconds, continuous=True)
    for value, name in [(np.nan, "nan_sample"), (np.inf, "inf_sample")]:
        y = a.copy()
        y[len(y) // 2] = value
        cases[name] = (y, {"reason": "invalid_audio"})
    for name, (y, truth) in cases.items():
        path = directory / f"{name}.wav"
        sf.write(path, y, sr, subtype="FLOAT")
        metadata = {
            "seed": SEED,
            "generator_sha256": digest,
            "sample_rate_hz": sr,
            "sample_count": len(y),
            **truth,
        }
        path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
    (directory / "zero_bytes.wav").write_bytes(b"")
    (directory / "truncated_header.wav").write_bytes(
        (directory / "A_mono_16k.wav").read_bytes()[:20]
    )
    if include_mp3 and "MP3" in sf.available_formats():
        path = directory / "A_mono_16k.mp3"
        sf.write(
            path, a, sr, format="MP3", bitrate_mode="CONSTANT", compression_level=0.5
        )
        path.with_suffix(".mp3.json").write_text(
            json.dumps(
                {
                    **truth_a,
                    "encoder": f"libsndfile:{sf.__libsndfile_version__}",
                    "generator_sha256": digest,
                }
            )
        )
        (directory / "truncated_data.mp3").write_bytes(path.read_bytes()[:20])
    for duration, name in [
        (9.01, "speech_9p01s"),
        (9.01 - 1 / 16000, "speech_one_sample_short"),
    ]:
        y, truth = voice(sr=16000, seconds=duration)
        sf.write(directory / f"{name}.wav", y, 16000, subtype="FLOAT")
        (directory / f"{name}.json").write_text(json.dumps(truth))
    # Truth-labeled VQI admission cases use known target activity spans.
    for name, spans in {
        "speech_30s_coverage_90pct": [(0.0, 27.0)],
        "speech_30s_coverage_60pct": [(0.0, 3.0), (20.0, 22.0)],
        "window_2p9s_speech": [(0.0, 2.9)],
    }.items():
        duration = 9.01 if name.startswith("window") else 30.0
        y, truth = voice(sr=16000, seconds=duration, continuous=True)
        mask = np.zeros(len(y), dtype=bool)
        for start, end in spans:
            mask[round(start * 16000) : round(end * 16000)] = True
        y *= mask
        sf.write(directory / f"{name}.wav", y, 16000, subtype="FLOAT")
        (directory / f"{name}.json").write_text(
            json.dumps({**truth, "speech_spans": spans})
        )

    # Persist manifests alongside fixtures; real engine emission is a later slice.
    from dataclasses import asdict

    from ee.voice.services.audio_provenance import (
        ProvenanceArtifact,
        build_vapi_provenance,
        unsupported_provenance,
    )

    role_files = [
        ProvenanceArtifact(role, f"call-recordings/fixture/{role}.wav", "fixture-v1")
        for role in ("assistant", "customer")
    ]
    for direction, owner in (("inbound", "system"), ("outbound", "client")):
        manifest = build_vapi_provenance(
            call_id="fixture",
            artifacts=role_files,
            direction=direction,
            recording_owner_account=owner,
        )
        (directory / f"manifest_{direction}.json").write_text(
            json.dumps(asdict(manifest))
        )
    for engine in ("livekit", "retell"):
        manifest = unsupported_provenance("unknown_agent_track", system_engine=engine)
        (directory / f"manifest_{engine}.json").write_text(json.dumps(asdict(manifest)))
    # Limits are header stubs, not multi-hundred-megabyte committed assets.
    limits = {
        "encoded_bytes": [104857600, 104857601],
        "target_samples": [28800000, 28800001],
        "duration_seconds": [600.0, 600.02],
        "sample_rates": [7999, 8000, 48000, 48001],
    }
    (directory / "limit_headers.json").write_text(json.dumps(limits))
    return directory


def cached_fixtures(tmp_path_factory):
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    return generate(tmp_path_factory.mktemp(f"audio-{digest}"))
