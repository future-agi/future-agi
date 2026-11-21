"""Credential-free sandbox entrypoint. Accept only bounded PCM WAV segments.

No imports from Django/application code. Reject ancillary chunks: metadata is
not silently exported in a supposedly sanitized playable representation.
"""

import json
import os
import struct
import sys


def probe(path):
    length = os.stat(path).st_size
    if not 44 <= length <= 8 * 1024 * 1024:
        raise ValueError
    with open(path, "rb") as source:
        header = source.read(12)
        magic, size, kind = struct.unpack("<4sI4s", header)
        if magic != b"RIFF" or kind != b"WAVE" or size + 8 != length:
            raise ValueError
        if source.read(8) != b"fmt \x10\x00\x00\x00":
            raise ValueError
        encoding, channels, rate, byte_rate, align, bits = struct.unpack(
            "<HHIIHH", source.read(16)
        )
        if (
            encoding != 1
            or channels not in {1, 2}
            or bits != 16
            or not 8000 <= rate <= 96000
            or align != channels * 2
            or byte_rate != rate * align
        ):
            raise ValueError
        tag, data_size = struct.unpack("<4sI", source.read(8))
        if (
            tag != b"data"
            or data_size + 44 != length
            or data_size % align
            or data_size == 0
        ):
            raise ValueError
        frames = data_size // align
        if frames > rate * 30:
            raise ValueError
        return {
            "mime_type": "audio/wav",
            "sample_rate": rate,
            "channels": channels,
            "frame_count": frames,
        }


if __name__ == "__main__":
    try:
        print(json.dumps(probe(sys.argv[1]), separators=(",", ":")))
    except Exception:
        # Do not include filenames, parser details or input fragments.
        sys.exit(2)
