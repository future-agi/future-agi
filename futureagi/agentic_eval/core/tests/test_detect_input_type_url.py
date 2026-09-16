"""``detect_input_type`` only needs the leading bytes of a URL target to sniff
its media type. It must not pull the whole object: eval workers call it for
every entry, and a full download of a multi-megabyte recording per eval is
what turned an S3 bucket into a $65/day egress bill."""

import pytest

from agentic_eval.core.utils import functions as functions_module


class _FakeResponse:
    """A streaming response whose full body is off-limits."""

    status_code = 200
    headers = {"Content-Type": "audio/wav"}

    def __init__(self):
        self.chunks_read = 0

    @property
    def content(self):
        raise AssertionError("detect_input_type read the entire body")

    def iter_content(self, chunk_size=1):
        # RIFF/WAVE header, then an endless body that must never be consumed.
        yield b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24
        self.chunks_read += 1
        while True:
            self.chunks_read += 1
            yield b"\x00" * chunk_size

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


@pytest.mark.unit
def test_url_sniff_reads_only_leading_bytes(monkeypatch):
    calls = []
    response = _FakeResponse()

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(functions_module.requests, "get", fake_get)

    result = functions_module.detect_input_type("https://bucket.example/rec.wav")

    assert result == {"type": "audio"}
    assert calls and calls[0].get("stream") is True
    assert response.chunks_read <= 2
