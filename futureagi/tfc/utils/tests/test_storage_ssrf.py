"""SSRF guard on tfc.utils.storage's URL-download helpers (TH-5648 follow-up)."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from tfc.utils.ssrf_guard import SsrfBlocked, SsrfResponse, assert_url_host_public
from tfc.utils.storage import (
    _ssrf_safe_get,
    convert_image_from_url_to_base64,
    download_audio_from_url,
    download_document_from_url,
    download_image_from_url,
    is_own_storage_url,
    upload_video_to_s3,
)


def _valid_png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color="red").save(buf, format="PNG")
    return buf.getvalue()


def _ok_response(body, content_type="image/png", url="https://example.com/x.png"):
    return SsrfResponse(200, {"Content-Type": content_type}, body, url)


def test_ssrf_safe_get_returns_response_from_safe_fetch():
    with patch(
        "tfc.utils.storage.safe_fetch",
        return_value=_ok_response(b"\x89PNG"),
    ):
        response = _ssrf_safe_get("https://example.com/x.png")
    assert response.status_code == 200
    assert response.content == b"\x89PNG"
    assert response.headers["Content-Type"] == "image/png"
    assert list(response.iter_content(chunk_size=2)) == [b"\x89P", b"NG"]


def test_ssrf_safe_get_converts_guard_rejection_to_request_exception():
    """Guard ValueError must surface as RequestException so retry loops catch it."""
    from requests.exceptions import RequestException

    with patch(
        "tfc.utils.storage.safe_fetch",
        side_effect=ValueError("URL host resolves to a private address."),
    ):
        with pytest.raises(RequestException):
            _ssrf_safe_get("http://169.254.169.254/")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/secret",
        "http://10.0.0.5/internal",
        "http://100.64.0.1/cgnat",
    ],
)
def test_download_image_from_url_never_connects_to_blocked_target(url):
    with patch("tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool") as pool_cls, patch(
        "tfc.utils.ssrf_guard.urllib3.HTTPSConnectionPool"
    ) as https_pool_cls:
        with pytest.raises(ValueError):
            download_image_from_url(url, max_retries=1)
        pool_cls.assert_not_called()
        https_pool_cls.assert_not_called()


def test_download_document_from_url_rejects_metadata_endpoint():
    with pytest.raises(ValueError):
        download_document_from_url(
            "http://169.254.169.254/latest/meta-data/", max_retries=1
        )


def test_download_audio_from_url_rejects_private_ip():
    with pytest.raises(ValueError):
        download_audio_from_url("http://10.0.0.5/audio.mp3", max_retries=1)


def test_upload_video_to_s3_rejects_private_ip():
    with pytest.raises(ValueError):
        upload_video_to_s3("http://169.254.169.254/video.mp4")


def test_download_image_from_url_success():
    body = _valid_png_bytes()
    with patch("tfc.utils.storage.safe_fetch", return_value=_ok_response(body)):
        out = download_image_from_url("https://example.com/x.png")
    assert out == body


def test_convert_image_from_url_to_base64_success():
    body = _valid_png_bytes()
    with patch("tfc.utils.storage.safe_fetch", return_value=_ok_response(body)):
        out = convert_image_from_url_to_base64("https://example.com/x.png")
    assert out.startswith("data:image/png;base64,")


def test_ssrf_safe_get_preserves_ssrf_blocked():
    """SsrfBlocked must NOT be wrapped as RequestException so retry loops can
    catch it specifically and fail fast (rather than burning ~30s of backoff
    on a permanently blocked URL).
    """
    with patch(
        "tfc.utils.storage.safe_fetch",
        side_effect=SsrfBlocked("private IP"),
    ):
        with pytest.raises(SsrfBlocked):
            _ssrf_safe_get("http://169.254.169.254/")


def test_download_image_from_url_fails_fast_on_ssrf_blocked():
    """The image retry loop must NOT sleep-and-retry on a permanent SSRF rejection."""
    with patch(
        "tfc.utils.storage.safe_fetch",
        side_effect=SsrfBlocked("private IP"),
    ), patch("tfc.utils.storage.time.sleep") as mock_sleep:
        with pytest.raises(ValueError, match="ERROR_DOWNLOADING_IMAGE"):
            download_image_from_url("http://169.254.169.254/img.png", max_retries=5)
    mock_sleep.assert_not_called()


def test_download_document_from_url_fails_fast_on_ssrf_blocked():
    with patch(
        "tfc.utils.storage.safe_fetch",
        side_effect=SsrfBlocked("private IP"),
    ), patch("tfc.utils.storage.time.sleep") as mock_sleep:
        with pytest.raises(ValueError, match="ERROR_DOWNLOADING_DOCUMENT"):
            download_document_from_url("http://169.254.169.254/x.pdf", max_retries=5)
    mock_sleep.assert_not_called()


def test_download_image_from_url_passes_explicit_max_bytes():
    """Regression: download_image_from_url must pass MAX_IMAGE_FILE_SIZE so it
    doesn't silently truncate at safe_fetch's 25 MiB default.
    """
    body = _valid_png_bytes()
    with patch(
        "tfc.utils.storage.safe_fetch",
        return_value=_ok_response(body),
    ) as mock_fetch:
        download_image_from_url("https://example.com/x.png")
    _, kwargs = mock_fetch.call_args
    from tfc.utils.storage import MAX_IMAGE_FILE_SIZE
    assert kwargs.get("max_bytes") == MAX_IMAGE_FILE_SIZE


def test_download_document_from_url_passes_explicit_max_bytes():
    with patch(
        "tfc.utils.storage.safe_fetch",
        return_value=SsrfResponse(
            200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\n%%EOF", "https://example.com/x.pdf"
        ),
    ) as mock_fetch:
        download_document_from_url("https://example.com/x.pdf")
    _, kwargs = mock_fetch.call_args
    from tfc.utils.storage import MAX_DOCUMENT_FILE_SIZE
    assert kwargs.get("max_bytes") == MAX_DOCUMENT_FILE_SIZE


class TestIsOwnStorageUrl:
    """is_own_storage_url must use hostname/path parsing, not substring, so an
    attacker-controlled URL that merely contains the bucket name cannot spoof
    the own-bucket skip.
    """

    def test_matches_s3_virtual_hosted_style_regional(self):
        assert is_own_storage_url(
            "https://fi-customer-data.s3.us-east-1.amazonaws.com/x", "fi-customer-data"
        )

    def test_matches_s3_virtual_hosted_style_no_region(self):
        assert is_own_storage_url(
            "https://fi-customer-data.s3.amazonaws.com/x", "fi-customer-data"
        )

    def test_matches_gcs_path_style(self):
        assert is_own_storage_url(
            "https://storage.googleapis.com/fi-customer-data/x", "fi-customer-data"
        )

    def test_rejects_attacker_subdomain_with_bucket_prefix(self):
        # THE substring bypass Rishav flagged: `<bucket>.attacker.com`
        # matches the old substring check but must NOT match now.
        assert not is_own_storage_url(
            "https://fi-customer-data.attacker.com/x", "fi-customer-data"
        )

    def test_rejects_attacker_path_containing_bucket(self):
        assert not is_own_storage_url(
            "https://evil.com/fi-customer-data/x", "fi-customer-data"
        )

    def test_rejects_bucket_lookalike_in_path(self):
        assert not is_own_storage_url(
            "https://storage.googleapis.com/fi-customer-data-fake/x",
            "fi-customer-data",
        )

    def test_rejects_non_http_scheme(self):
        assert not is_own_storage_url(
            "file:///fi-customer-data/x", "fi-customer-data"
        )

    def test_rejects_empty_bucket(self):
        assert not is_own_storage_url("https://example.com/x", "")

    def test_rejects_non_string_value(self):
        assert not is_own_storage_url(None, "fi-customer-data")
        assert not is_own_storage_url(123, "fi-customer-data")

    def test_rejects_amazonaws_lookalike_domain(self):
        # `fi-customer-data.s3.attacker-amazonaws.com` (parent is not
        # amazonaws.com) must not match.
        assert not is_own_storage_url(
            "https://fi-customer-data.s3.attacker-amazonaws.com/x",
            "fi-customer-data",
        )


# ---------------------------------------------------------------------------
# assert_url_host_public — pre-flight guard reused by the async recording rehost
# (simulate.temporal.utils.async_storage). Provider recording URLs come back
# inside an API response, so the converter validates the host before fetching.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1/secret",  # loopback
        "http://10.0.0.5/internal",  # private
        "http://100.64.0.1/cgnat",  # carrier-grade NAT
        "http://[::1]/x",  # ipv6 loopback
    ],
)
def test_assert_url_host_public_blocks_private_and_metadata(url):
    with pytest.raises(SsrfBlocked):
        assert_url_host_public(url)


def test_assert_url_host_public_rejects_non_http_scheme():
    with pytest.raises(SsrfBlocked):
        assert_url_host_public("ftp://example.com/x")


def test_assert_url_host_public_rejects_missing_host():
    with pytest.raises(SsrfBlocked):
        assert_url_host_public("http:///nohost")


def test_assert_url_host_public_allows_public_ip_literal():
    # 8.8.8.8 is public; getaddrinfo on a literal doesn't hit DNS, so this stays
    # hermetic while proving a legit provider host is not rejected.
    assert_url_host_public("https://8.8.8.8/recording.mp3")  # no raise


def test_assert_url_host_public_allows_public_hostname():
    # A normal provider hostname (e.g. a CDN) must pass. Mock the resolver so the
    # test stays hermetic — no real DNS — while proving hostnames, not just IP
    # literals, go through the guard.
    with patch(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
    ) as mock_resolve:
        assert_url_host_public("https://recordings.example.com/rec.mp3")  # no raise
    assert mock_resolve.call_args.args[0] == "recordings.example.com"


def test_assert_url_host_public_blocks_hostname_resolving_to_private():
    # The real SSRF threat: a public-looking hostname that resolves to an
    # internal address. The guard must reject on the RESOLVED IP, not the name.
    with patch(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("10.0.0.5", 0))],
    ):
        with pytest.raises(SsrfBlocked):
            assert_url_host_public("https://totally-legit.example.com/rec.mp3")


async def test_recording_rehost_blocks_ssrf_url_and_fails_open():
    """The converter must not fetch a recording URL that points at an internal
    host: it blocks before the download and fails open to the original URL with
    0 bytes (nothing rehosted, nothing metered), never calling the downloader."""
    from simulate.temporal.utils import async_storage

    blocked = "http://169.254.169.254/latest/meta-data/"
    with patch.object(
        async_storage, "_existing_rehosted_audio", return_value=None
    ), patch.object(async_storage, "download_audio_from_url_async") as mock_dl:
        s3_url, size = await async_storage.convert_audio_url_to_s3_async_with_size(
            "call-1", blocked, "recording", provider="bland"
        )

    assert s3_url == blocked
    assert size == 0
    mock_dl.assert_not_called()
