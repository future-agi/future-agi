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


class _FragmentedResponse:
    """A response whose magic header is split across several small chunks.

    ``iter_content(chunk_size=n)`` promises chunks of *at most* n bytes, not
    exactly n — chunked transfer encoding and content decoding both yield
    short pieces. ``Content-Type`` is generic here, so a sniff that read only
    the first yield would have nothing to fall back on and misclassify.
    """

    status_code = 200
    headers = {"Content-Type": "application/octet-stream"}

    def __init__(self, body, fragment_size):
        self._body = body
        self._fragment_size = fragment_size
        self.bytes_read = 0

    @property
    def content(self):
        raise AssertionError("detect_input_type read the entire body")

    def iter_content(self, chunk_size=1):
        for offset in range(0, len(self._body), self._fragment_size):
            fragment = self._body[offset : offset + self._fragment_size]
            self.bytes_read += len(fragment)
            yield fragment

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


@pytest.mark.unit
def test_url_sniff_reassembles_fragmented_magic_header(monkeypatch):
    # A 1 KB PNG delivered two bytes at a time: the 8-byte PNG signature alone
    # spans four yields, so only an accumulated buffer can identify it.
    body = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 1000
    response = _FragmentedResponse(body, fragment_size=2)

    monkeypatch.setattr(
        functions_module.requests, "get", lambda url, **kwargs: response
    )

    assert functions_module.detect_input_type("https://bucket.example/x") == {
        "type": "image"
    }


@pytest.mark.unit
def test_url_sniff_stops_at_the_byte_budget(monkeypatch):
    # The budget still caps the read: a body far larger than 8 KB must not be
    # pulled in full just to learn its type.
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * (512 * 1024)
    response = _FragmentedResponse(body, fragment_size=1024)

    monkeypatch.setattr(
        functions_module.requests, "get", lambda url, **kwargs: response
    )

    assert functions_module.detect_input_type("https://bucket.example/x") == {
        "type": "image"
    }
    assert response.bytes_read <= functions_module._URL_SNIFF_BYTES + 1024
