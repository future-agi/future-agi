"""Unit tests for validate_file_url + the SSRF-safe fetch guard (TH-5648)."""

from unittest.mock import MagicMock, patch

import pytest

from model_hub.views.utils.utils import validate_file_url
from tfc.utils.ssrf_guard import (
    SsrfResponse,
    _reject_unsafe_ip,
    _resolve_pinned_ip,
    safe_fetch,
)


def _fake_urllib3_response(status, headers=None):
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    return resp


@pytest.mark.parametrize(
    "ip_str",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "192.168.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "100.64.0.1",  # RFC 6598 CGNAT — not covered by ipaddress.is_private
        "::ffff:169.254.169.254",
        "fd12:3456:789a::1",
    ],
)
def test_reject_unsafe_ip_blocks_internal_ranges(ip_str):
    with pytest.raises(ValueError):
        _reject_unsafe_ip(ip_str, host="example.com")


def test_reject_unsafe_ip_allows_public_ip():
    _reject_unsafe_ip("8.8.8.8", host="example.com")


def test_resolve_pinned_ip_rejects_if_any_record_is_private(monkeypatch):
    """A multi-A-record host with any private address must be rejected."""
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ],
    )
    with pytest.raises(ValueError):
        _resolve_pinned_ip("multi-record.example.com")


def test_resolve_pinned_ip_returns_public_ip(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    assert _resolve_pinned_ip("example.com") == "8.8.8.8"


def test_resolve_pinned_ip_raises_on_dns_failure(monkeypatch):
    import socket

    def _raise(*a, **kw):
        raise socket.gaierror("nope")

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", _raise)
    with pytest.raises(ValueError):
        _resolve_pinned_ip("nonexistent.example.com")


def test_safe_fetch_rejects_redirect_to_private_ip(monkeypatch):
    def fake_getaddrinfo(host, port):
        if host == "public.example.com":
            return [(None, None, None, None, ("8.8.8.8", 0))]
        if host == "internal.example.com":
            return [(None, None, None, None, ("169.254.169.254", 0))]
        raise AssertionError(f"unexpected host {host}")

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", fake_getaddrinfo)
    redirect_response = _fake_urllib3_response(
        302, {"Location": "http://internal.example.com/meta"}
    )
    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.return_value = redirect_response
        with pytest.raises(ValueError):
            safe_fetch("http://public.example.com/redirect")


def test_safe_fetch_follows_valid_redirect_and_returns_final_url(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    redirect_response = _fake_urllib3_response(
        302, {"Location": "http://public.example.com/final.jpg"}
    )
    final_response = _fake_urllib3_response(200, {"Content-Type": "image/jpeg"})
    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.side_effect = [redirect_response, final_response]
        response = safe_fetch("http://public.example.com/redirect")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.final_url == "http://public.example.com/final.jpg"


def test_safe_fetch_caps_redirect_chain(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.side_effect = lambda *a, **kw: _fake_urllib3_response(
            302, {"Location": "http://public.example.com/next"}
        )
        with pytest.raises(ValueError, match="Too many redirects"):
            safe_fetch("http://public.example.com/start")


def test_safe_fetch_rejects_redirect_with_no_location(monkeypatch):
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.return_value = _fake_urllib3_response(302, {})
        with pytest.raises(ValueError):
            safe_fetch("http://public.example.com/x")


def test_safe_fetch_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        safe_fetch("ftp://public.example.com/x")


def _stub_safe_fetch(status, headers=None, final_url=None):
    def _stub(url, **_kwargs):
        return SsrfResponse(
            status_code=status,
            headers=headers or {},
            content=b"",
            final_url=final_url or url,
        )

    return _stub


def test_validate_file_url_accepts_extensionless_image_via_content_type(monkeypatch):
    """The original TH-5648 bug: extensionless S3/CDN URLs must validate off Content-Type."""
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(200, {"Content-Type": "image/png"}),
    )
    validate_file_url(
        "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/uuid/uuid", "image"
    )


def test_validate_file_url_uses_final_url_for_extension_fallback(monkeypatch):
    """Bypass flagged in review: fallback must inspect the POST-redirect URL."""
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(
            200,
            {"Content-Type": "application/octet-stream"},
            final_url="https://public.example.com/payload.exe",
        ),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/download", "image")


def test_validate_file_url_rejects_explicit_mismatched_content_type(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(200, {"Content-Type": "text/html"}),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/page", "image")


def test_validate_file_url_accepts_generic_content_type_with_good_extension(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch", _stub_safe_fetch(200, {})
    )
    validate_file_url("https://public.example.com/pic.png", "image")


def test_validate_file_url_document_still_uses_extension_check(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch", _stub_safe_fetch(200, {})
    )
    validate_file_url("https://public.example.com/report.pdf", "document")
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/report.exe", "document")


def test_validate_file_url_rejects_bad_status_code(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch", _stub_safe_fetch(404, {})
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/missing.png", "image")


def test_validate_file_url_rejects_svg_content_type(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(200, {"Content-Type": "image/svg+xml"}),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/pic.svg", "image")


def test_validate_file_url_rejects_svg_extension_with_generic_content_type(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch", _stub_safe_fetch(200, {})
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/pic.svg", "image")


def test_validate_file_url_ignores_fragment_when_checking_extension(monkeypatch):
    """URL fragment must not fool the extension check — the server only sees
    the pre-fragment path, so a `.exe#pretty.png` URL must be rejected.
    """
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(
            200,
            {"Content-Type": "application/octet-stream"},
            final_url="https://public.example.com/malware.exe#pretty.png",
        ),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/malware.exe#pretty.png", "image")


def test_validate_file_url_document_rejects_exe_with_png_fragment(monkeypatch):
    """Same fragment-bypass check for the document path (extension-only check)."""
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(200, {}, final_url="https://public.example.com/x.exe#a.pdf"),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://public.example.com/x.exe#a.pdf", "document")


def test_validate_file_url_rejects_extensionless_url_with_octet_stream(monkeypatch):
    """Extensionless URL + `application/octet-stream` must be rejected.

    Regression guard for the review finding where an SVG served with
    Content-Type: application/octet-stream from `/uploads/abc` slipped past
    validation because the URL carried no extension at all.
    """
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(
            200,
            {"Content-Type": "application/octet-stream"},
            final_url="https://cdn.example.com/uploads/abc",
        ),
    )
    with pytest.raises(ValueError):
        validate_file_url("https://cdn.example.com/uploads/abc", "image")


def test_validate_file_url_reads_content_type_case_insensitively(monkeypatch):
    """SsrfResponse.headers must be case-insensitive so callers using
    `.get('Content-Type')` catch responses sent with lowercase `content-type`
    (HTTP/2 canonical form).
    """
    monkeypatch.setattr(
        "model_hub.views.utils.utils.safe_fetch",
        _stub_safe_fetch(200, {"content-type": "image/png"}),  # lowercase
    )
    validate_file_url("https://public.example.com/pic", "image")


def test_safe_fetch_sends_host_header_with_port_for_non_default(monkeypatch):
    """Regression: on non-default ports (e.g. :8443), the Host header must
    include the port so virtual-host-routed backends match the correct
    server_name.
    """
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    captured = {}

    def fake_request(method, path, headers, redirect, preload_content):
        captured["headers"] = dict(headers)
        return _fake_urllib3_response(200, {"Content-Type": "image/jpeg"})

    with patch("tfc.utils.ssrf_guard.urllib3.HTTPSConnectionPool") as pool_cls:
        pool_cls.return_value.request.side_effect = fake_request
        safe_fetch("https://public.example.com:8443/x.jpg")
    assert captured["headers"]["Host"] == "public.example.com:8443"


def test_safe_fetch_strips_authorization_on_cross_origin_redirect(monkeypatch):
    """Authorization / Cookie / Proxy-Authorization must NOT leak to a
    redirect target on a different origin.
    """
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )
    redirect_response = _fake_urllib3_response(
        302, {"Location": "http://other.example.com/final"}
    )
    final_response = _fake_urllib3_response(200, {"Content-Type": "text/plain"})
    captured_headers = []

    def fake_request(method, path, headers, redirect, preload_content):
        captured_headers.append(dict(headers))
        return redirect_response if len(captured_headers) == 1 else final_response

    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls:
        pool_cls.return_value.request.side_effect = fake_request
        safe_fetch(
            "http://origin.example.com/x",
            headers={"Authorization": "Bearer secret", "Cookie": "session=abc"},
        )
    assert captured_headers[0].get("Authorization") == "Bearer secret"
    assert "Authorization" not in captured_headers[1]
    assert "Cookie" not in captured_headers[1]
