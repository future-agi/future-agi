"""
Tests for image-input preprocessors registered in evaluations.engine.preprocessing.

Pins three behaviors:
  - Passthrough: None / empty / data-URI / base64 / file path are not touched.
  - SSRF guard: private / loopback / metadata hosts return the original URL
    untouched (sandbox handles the error gracefully).
  - Fetch path: public http(s) URLs are downloaded and returned as base64.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from evaluations.engine.preprocessing import (
    PREPROCESSORS,
    _host_is_blocked,
    _resolve_image_input,
    preprocess_inputs,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_image_preprocessors_registered():
    for name in ("image_properties", "psnr", "ssim"):
        assert name in PREPROCESSORS, f"{name} preprocessor must be registered"


# ---------------------------------------------------------------------------
# Host blocklist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", [
    "localhost",
    "127.0.0.1",
    "10.0.0.5",
    "169.254.169.254",
    "192.168.1.1",
    "172.16.0.1",
    "172.31.255.254",
])
def test_blocked_hosts(host):
    assert _host_is_blocked(host) is True


@pytest.mark.parametrize("host", [
    "example.com",
    "fi-content.s3.ap-south-1.amazonaws.com",
    "172.15.0.1",
    "172.32.0.1",
])
def test_public_hosts_not_blocked(host):
    assert _host_is_blocked(host) is False


# ---------------------------------------------------------------------------
# Resolver passthroughs (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [
    None,
    "",
    "   ",
    "aGVsbG8=",
    "data:image/png;base64,iVBORw0KGgo...",
    "/tmp/img.png",
    42,
    b"bytes",
])
def test_resolver_does_not_touch_non_url_inputs(value):
    with patch("evaluations.engine.preprocessing.requests.get") as mock_get:
        out = _resolve_image_input(value)
        mock_get.assert_not_called()
        assert out == value


# ---------------------------------------------------------------------------
# SSRF guard — blocked URLs must not fetch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9999/img.png",
    "http://10.255.255.255/img.png",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.1/admin/screenshot.png",
    "http://localhost/file",
])
def test_blocked_urls_never_fetch(url):
    with patch("evaluations.engine.preprocessing.requests.get") as mock_get:
        out = _resolve_image_input(url)
        mock_get.assert_not_called()
        assert out == url


# ---------------------------------------------------------------------------
# Fetch path
# ---------------------------------------------------------------------------


def _make_mock_response(*, status=200, body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64):
    resp = MagicMock()
    resp.status_code = status

    def _iter(chunk_size=64 * 1024):
        if status != 200:
            return
        for i in range(0, len(body), chunk_size):
            yield body[i:i + chunk_size]

    resp.iter_content = _iter
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_successful_fetch_returns_base64():
    import base64
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
    with patch(
        "evaluations.engine.preprocessing.requests.get",
        return_value=_make_mock_response(body=body),
    ):
        out = _resolve_image_input("https://example.com/img.png")
    assert isinstance(out, str)
    assert base64.b64decode(out) == body


def test_non_200_returns_original_url():
    url = "https://example.com/missing.png"
    with patch(
        "evaluations.engine.preprocessing.requests.get",
        return_value=_make_mock_response(status=404),
    ):
        assert _resolve_image_input(url) == url


def test_fetch_exception_returns_original_url():
    url = "https://example.com/img.png"
    with patch(
        "evaluations.engine.preprocessing.requests.get",
        side_effect=Exception("connection refused"),
    ):
        assert _resolve_image_input(url) == url


def test_oversize_response_returns_original_url():
    """Responses bigger than the 25MB ceiling fall back to the URL."""
    url = "https://example.com/huge.png"
    # Construct a body larger than 25 MB via a generator-backed response.
    big = b"x" * (26 * 1024 * 1024)
    with patch(
        "evaluations.engine.preprocessing.requests.get",
        return_value=_make_mock_response(body=big),
    ):
        out = _resolve_image_input(url)
    assert out == url


# ---------------------------------------------------------------------------
# Preprocessor wiring — kwargs get replaced
# ---------------------------------------------------------------------------


def test_image_properties_replaces_text_kwarg():
    with patch(
        "evaluations.engine.preprocessing.requests.get",
        return_value=_make_mock_response(),
    ):
        out = preprocess_inputs("image_properties", {"text": "https://example.com/x.png"})
    assert out["text"] != "https://example.com/x.png"
    assert isinstance(out["text"], str) and len(out["text"]) > 0


def test_psnr_replaces_both_kwargs():
    with patch(
        "evaluations.engine.preprocessing.requests.get",
        return_value=_make_mock_response(),
    ):
        out = preprocess_inputs("psnr", {
            "output": "https://example.com/a.png",
            "expected": "https://example.com/b.png",
        })
    assert out["output"] != "https://example.com/a.png"
    assert out["expected"] != "https://example.com/b.png"


def test_ssim_replaces_both_kwargs():
    with patch(
        "evaluations.engine.preprocessing.requests.get",
        return_value=_make_mock_response(),
    ):
        out = preprocess_inputs("ssim", {
            "output": "https://example.com/a.png",
            "expected": "https://example.com/b.png",
        })
    assert out["output"] != "https://example.com/a.png"
    assert out["expected"] != "https://example.com/b.png"


# ---------------------------------------------------------------------------
# Data-URI resolver (clip / fid path)
# ---------------------------------------------------------------------------


from evaluations.engine.preprocessing import (
    _resolve_fid_input,
    _resolve_image_input_as_data_uri,
)


def test_data_uri_resolver_returns_data_uri_for_url():
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "image/png"}
    resp.iter_content = lambda chunk_size=64 * 1024: iter([body])
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("evaluations.engine.preprocessing.requests.get", return_value=resp):
        out = _resolve_image_input_as_data_uri("https://example.com/x.png")
    assert isinstance(out, str)
    assert out.startswith("data:image/png;base64,")


def test_data_uri_resolver_forces_image_mime_when_octet_stream():
    """S3 sometimes serves Content-Type: application/octet-stream; consumers
    rely on the `data:image/...` prefix to route — force it."""
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/octet-stream"}
    resp.iter_content = lambda chunk_size=64 * 1024: iter([body])
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("evaluations.engine.preprocessing.requests.get", return_value=resp):
        out = _resolve_image_input_as_data_uri("https://example.com/key-no-extension")
    assert out.startswith("data:image/jpeg;base64,")


def test_data_uri_resolver_passthrough_on_existing_data_uri():
    given = "data:image/png;base64,XXX"
    assert _resolve_image_input_as_data_uri(given) == given


def test_data_uri_resolver_blocks_private_hosts():
    url = "http://169.254.169.254/latest/meta-data/"
    with patch("evaluations.engine.preprocessing.requests.get") as mock_get:
        out = _resolve_image_input_as_data_uri(url)
    mock_get.assert_not_called()
    assert out == url


# ---------------------------------------------------------------------------
# FID list resolver
# ---------------------------------------------------------------------------


def test_fid_resolver_json_list_in_json_list_out():
    body = b"\x89PNG\r\n\x1a\n"
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "image/png"}
    resp.iter_content = lambda chunk_size=64 * 1024: iter([body])
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("evaluations.engine.preprocessing.requests.get", return_value=resp):
        out = _resolve_fid_input('["https://example.com/a.png", "https://example.com/b.png"]')
    import json
    parsed = json.loads(out)
    assert len(parsed) == 2
    assert all(item.startswith("data:image/png;base64,") for item in parsed)


def test_fid_resolver_python_list_passthrough_shape():
    body = b"\x89PNG\r\n\x1a\n"
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "image/png"}
    resp.iter_content = lambda chunk_size=64 * 1024: iter([body])
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    with patch("evaluations.engine.preprocessing.requests.get", return_value=resp):
        out = _resolve_fid_input(["https://example.com/a.png"])
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0].startswith("data:image/png;base64,")


def test_fid_resolver_preserves_non_url_items():
    items = ["data:image/png;base64,YYY", "/tmp/local.png"]
    out = _resolve_fid_input(items)
    assert out == items
