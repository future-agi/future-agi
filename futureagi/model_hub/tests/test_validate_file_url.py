"""Unit tests for validate_file_url's SSRF-safe fetch (TH-5648).

These pin the behavior that motivated the fix (extension-less S3/CDN image
URLs must validate via Content-Type) as well as every bypass raised in code
review:

- redirects must be re-validated per hop, not just the original URL
  (an attacker only needs one public URL that 30x's into an internal one)
- the IP validated must be the exact IP connected to (no second, independent
  DNS resolution that a rebinding attack could answer differently)
- hostnames must be parsed with urllib.parse, not manual string splitting
- the extension fallback must inspect the post-redirect URL, not the
  pre-redirect one
- a redirect chain must be capped
"""

from unittest.mock import MagicMock, patch

import pytest

from model_hub.views.utils.utils import validate_file_url
from tfc.utils.ssrf_guard import _reject_unsafe_ip, _resolve_pinned_ip, _safe_head


def _fake_response(status, headers=None):
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# _reject_unsafe_ip / _resolve_pinned_ip — the IP allow/deny logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip_str",
    [
        "10.0.0.1",  # private
        "127.0.0.1",  # loopback
        "169.254.169.254",  # cloud metadata (link-local range)
        "192.168.1.1",  # private
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "100.64.0.1",  # CGNAT (RFC 6598) -- not covered by ipaddress.is_private
        "::ffff:169.254.169.254",  # IPv4-mapped IPv6 form of the metadata address
        "fd12:3456:789a::1",  # IPv6 unique local address
    ],
)
def test_reject_unsafe_ip_blocks_internal_ranges(ip_str):
    with pytest.raises(ValueError):
        _reject_unsafe_ip(ip_str, file_type="image", host="example.com")


def test_reject_unsafe_ip_allows_public_ip():
    _reject_unsafe_ip("8.8.8.8", file_type="image", host="example.com")  # no raise


def test_resolve_pinned_ip_rejects_if_any_record_is_private(monkeypatch):
    """A host with BOTH a public and a private A record must still be
    rejected — otherwise a multi-record host hides the private one.
    """
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ],
    )
    with pytest.raises(ValueError):
        _resolve_pinned_ip("multi-record.example.com", file_type="image")


def test_resolve_pinned_ip_returns_public_ip(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    assert _resolve_pinned_ip("example.com", file_type="image") == "8.8.8.8"


def test_resolve_pinned_ip_raises_on_dns_failure(monkeypatch):
    import socket

    def _raise(*a, **kw):
        raise socket.gaierror("nope")

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", _raise)
    with pytest.raises(ValueError):
        _resolve_pinned_ip("nonexistent.example.com", file_type="image")


# ---------------------------------------------------------------------------
# _safe_head — redirect handling must re-validate every hop
# ---------------------------------------------------------------------------


def test_safe_head_rejects_redirect_to_private_ip(monkeypatch):
    """The exact bypass flagged in review: a public URL that redirects to an
    internal target must be rejected, not just the original host.
    """

    def fake_getaddrinfo(host, port):
        if host == "public.example.com":
            return [(None, None, None, None, ("8.8.8.8", 0))]
        if host == "internal.example.com":
            return [(None, None, None, None, ("169.254.169.254", 0))]
        raise AssertionError(f"unexpected host {host}")

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", fake_getaddrinfo)

    redirect_response = _fake_response(
        302, {"Location": "http://internal.example.com/meta"}
    )

    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.return_value = redirect_response
        with pytest.raises(ValueError):
            _safe_head("http://public.example.com/redirect", file_type="image")


def test_safe_head_follows_valid_redirect_and_returns_final_url(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )

    redirect_response = _fake_response(
        302, {"Location": "http://public.example.com/final.jpg"}
    )
    final_response = _fake_response(200, {"Content-Type": "image/jpeg"})

    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.side_effect = [redirect_response, final_response]
        status, headers, final_url = _safe_head(
            "http://public.example.com/redirect", file_type="image"
        )

    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert final_url == "http://public.example.com/final.jpg"


def test_safe_head_caps_redirect_chain(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )

    def always_redirect(*args, **kwargs):
        return _fake_response(302, {"Location": "http://public.example.com/next"})

    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.side_effect = always_redirect
        with pytest.raises(ValueError, match="Too many redirects"):
            _safe_head("http://public.example.com/start", file_type="image")


def test_safe_head_rejects_redirect_with_no_location(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.return_value = _fake_response(302, {})
        with pytest.raises(ValueError):
            _safe_head("http://public.example.com/x", file_type="image")


def test_safe_head_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        _safe_head("ftp://public.example.com/x", file_type="image")


# ---------------------------------------------------------------------------
# validate_file_url — end-to-end behavior via a mocked _safe_head
# ---------------------------------------------------------------------------


def test_validate_file_url_accepts_extensionless_image_via_content_type(monkeypatch):
    """The original TH-5648 bug: S3/CDN image URLs with UUID keys (no
    extension) must validate purely off Content-Type.
    """
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (
            200,
            {"Content-Type": "image/png"},
            url,
        ),
    )
    validate_file_url(
        "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/uuid/uuid",
        "image",
    )  # must not raise


def test_validate_file_url_uses_final_url_for_extension_fallback(monkeypatch):
    """Bypass flagged in review: a URL with no extension that redirects to a
    URL with a bad extension (and a generic content-type) must be rejected —
    the fallback must inspect the POST-redirect URL.
    """
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (
            200,
            {"Content-Type": "application/octet-stream"},
            "https://public.example.com/payload.exe",
        ),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/download", "image")


def test_validate_file_url_rejects_explicit_mismatched_content_type(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (200, {"Content-Type": "text/html"}, url),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/page", "image")


def test_validate_file_url_accepts_generic_content_type_with_good_extension(
    monkeypatch,
):
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (200, {}, url),
    )
    validate_file_url("https://public.example.com/pic.png", "image")  # no raise


def test_validate_file_url_document_still_uses_extension_check(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (200, {}, url),
    )
    validate_file_url("https://public.example.com/report.pdf", "document")
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/report.exe", "document")


def test_validate_file_url_rejects_bad_status_code(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (404, {}, url),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/missing.png", "image")


# ---------------------------------------------------------------------------
# SVG stored-XSS rejection (review comment: SVG can embed <script>)
# ---------------------------------------------------------------------------


def test_validate_file_url_rejects_svg_content_type(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (200, {"Content-Type": "image/svg+xml"}, url),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/pic.svg", "image")


def test_validate_file_url_rejects_svg_extension_with_generic_content_type(
    monkeypatch,
):
    monkeypatch.setattr(
        "model_hub.views.utils.utils._safe_head",
        lambda url, file_type: (200, {}, url),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/pic.svg", "image")
