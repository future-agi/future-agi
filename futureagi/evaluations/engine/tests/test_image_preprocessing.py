"""
Tests for image-input preprocessors registered in evaluations.engine.preprocessing.

Pins three behaviors:
  - Passthrough: None / empty / data-URI / base64 / file path are not touched.
  - SSRF guard: private / loopback / metadata hosts return the original URL
    untouched (sandbox handles the error gracefully). The guard itself
    (IP-pinning, redirect re-validation) is exercised via `_safe_get`
    (`tfc.utils.ssrf_guard`), which this module now delegates to (TH-5648
    follow-up: this used to have its own weaker, prefix-based blocklist).
  - Fetch path: public http(s) URLs are downloaded and returned as base64.
"""

from __future__ import annotations

import base64
import json

from unittest.mock import patch

import pytest

from evaluations.engine.preprocessing import (
    PREPROCESSORS,
    _resolve_fid_input,
    _resolve_image_input,
    _resolve_image_input_as_data_uri,
    preprocess_inputs,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_image_preprocessors_registered():
    for name in ("image_properties", "psnr", "ssim"):
        assert name in PREPROCESSORS, f"{name} preprocessor must be registered"


# ---------------------------------------------------------------------------
# Resolver passthroughs (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "aGVsbG8=",
        "data:image/png;base64,iVBORw0KGgo...",
        "/tmp/img.png",
        42,
        b"bytes",
    ],
)
def test_resolver_does_not_touch_non_url_inputs(value):
    with patch("evaluations.engine.preprocessing.safe_fetch") as mock_get:
        out = _resolve_image_input(value)
        mock_get.assert_not_called()
        assert out == value


# ---------------------------------------------------------------------------
# SSRF guard — blocked URLs must not fetch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9999/img.png",
        "http://10.255.255.255/img.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin/screenshot.png",
        "http://localhost/file",
    ],
)
def test_blocked_urls_never_fetch(url):
    """No mocking needed: `_safe_get` rejects these via `_reject_unsafe_ip`
    before it ever opens a connection, so the fetch fails and the original
    URL is returned untouched.
    """
    out = _resolve_image_input(url)
    assert out == url


# ---------------------------------------------------------------------------
# Fetch path
# ---------------------------------------------------------------------------


def _safe_get_result(*, status=200, body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png", url="https://example.com/img.png"):
    from tfc.utils.ssrf_guard import SsrfResponse
    return SsrfResponse(status, {"Content-Type": content_type}, body, url)


def test_successful_fetch_returns_base64():
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body),
    ):
        out = _resolve_image_input("https://example.com/img.png")
    assert isinstance(out, str)
    assert base64.b64decode(out) == body


def test_non_200_returns_original_url():
    url = "https://example.com/missing.png"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(status=404, body=b"", url=url),
    ):
        assert _resolve_image_input(url) == url


def test_fetch_exception_returns_original_url():
    url = "https://example.com/img.png"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        side_effect=Exception("connection refused"),
    ):
        assert _resolve_image_input(url) == url


def test_oversize_response_rejected_by_safe_get_returns_original_url():
    """The 25MB ceiling is enforced inside `_safe_get` itself; it surfaces as
    a ValueError which `_fetch_url_bytes` must catch and fall back on."""
    url = "https://example.com/huge.png"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        side_effect=ValueError("Image URL body exceeds byte limit."),
    ):
        out = _resolve_image_input(url)
    assert out == url


# ---------------------------------------------------------------------------
# Preprocessor wiring — kwargs get replaced
# ---------------------------------------------------------------------------


def test_image_properties_replaces_text_kwarg():
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(),
    ):
        out = preprocess_inputs("image_properties", {"text": "https://example.com/x.png"})
    assert out["text"] != "https://example.com/x.png"
    assert isinstance(out["text"], str) and len(out["text"]) > 0


def test_psnr_replaces_both_kwargs():
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(),
    ):
        out = preprocess_inputs(
            "psnr",
            {
                "output": "https://example.com/a.png",
                "expected": "https://example.com/b.png",
            },
        )
    assert out["output"] != "https://example.com/a.png"
    assert out["expected"] != "https://example.com/b.png"


def test_ssim_replaces_both_kwargs():
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(),
    ):
        out = preprocess_inputs(
            "ssim",
            {
                "output": "https://example.com/a.png",
                "expected": "https://example.com/b.png",
            },
        )
    assert out["output"] != "https://example.com/a.png"
    assert out["expected"] != "https://example.com/b.png"


# ---------------------------------------------------------------------------
# Data-URI resolver (clip / fid path)
# ---------------------------------------------------------------------------


def test_data_uri_resolver_returns_data_uri_for_url():
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/png"),
    ):
        out = _resolve_image_input_as_data_uri("https://example.com/x.png")
    assert isinstance(out, str)
    assert out.startswith("data:image/png;base64,")


def test_data_uri_resolver_forces_image_mime_when_octet_stream():
    """S3 sometimes serves Content-Type: application/octet-stream; consumers
    rely on the `data:image/...` prefix to route — force it."""
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="application/octet-stream"),
    ):
        out = _resolve_image_input_as_data_uri("https://example.com/key-no-extension")
    assert out.startswith("data:image/jpeg;base64,")


def test_data_uri_resolver_passthrough_on_existing_data_uri():
    given = "data:image/png;base64,XXX"
    assert _resolve_image_input_as_data_uri(given) == given


def test_data_uri_resolver_blocks_private_hosts():
    url = "http://169.254.169.254/latest/meta-data/"
    out = _resolve_image_input_as_data_uri(url)
    assert out == url


def test_data_uri_resolver_rejects_svg_content_type():
    """SVG can carry <script>; if the preprocessor emitted
    `data:image/svg+xml;base64,...` any downstream inline renderer would
    execute it. Reject at fetch time — fall through to the original URL so
    the sandbox produces its own load error, no data URI is returned.
    """
    body = b"<svg onload=\"alert(1)\"></svg>"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/svg+xml"),
    ):
        out = _resolve_image_input_as_data_uri("https://example.com/x.svg")
    # On rejection, _fetch_url_bytes returns None → resolver returns original
    # URL string, not a data URI carrying the SVG.
    assert out == "https://example.com/x.svg"
    assert not out.startswith("data:")


# ---------------------------------------------------------------------------
# FID list resolver
# ---------------------------------------------------------------------------


def test_fid_resolver_json_list_in_json_list_out():
    body = b"\x89PNG\r\n\x1a\n"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/png"),
    ):
        out = _resolve_fid_input(
            '["https://example.com/a.png", "https://example.com/b.png"]'
        )
    parsed = json.loads(out)
    assert len(parsed) == 2
    assert all(item.startswith("data:image/png;base64,") for item in parsed)


def test_fid_resolver_python_list_passthrough_shape():
    body = b"\x89PNG\r\n\x1a\n"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/png"),
    ):
        out = _resolve_fid_input(["https://example.com/a.png"])
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0].startswith("data:image/png;base64,")


def test_fid_resolver_preserves_non_url_items():
    items = ["data:image/png;base64,YYY", "/tmp/local.png"]
    out = _resolve_fid_input(items)
    assert out == items
