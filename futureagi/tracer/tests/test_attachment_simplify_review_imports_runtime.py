"""Real Linux validation executor check; a missing sandbox is a failure, not a skip."""

import struct

from tracer.tests import test_attachment_simplify_upload_accounting_ledger as shared

env = shared.env


def test_actual_bubblewrap_probe_accepts_known_pcm(env, tmp_path):
    path = tmp_path / "known.wav"
    path.write_bytes(
        struct.pack(
            "<4sI4s4sIHHIIHH4sIh",
            b"RIFF",
            38,
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            8000,
            16000,
            2,
            16,
            b"data",
            2,
            0,
        )
    )
    assert env.v.sandbox_probe(path) == {
        "mime_type": "audio/wav",
        "sample_rate": 8000,
        "channels": 1,
        "frame_count": 1,
    }
