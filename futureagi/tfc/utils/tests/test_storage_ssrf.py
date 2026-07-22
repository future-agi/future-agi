"""SSRF guard on tfc.utils.storage's URL-download helpers (TH-5648 follow-up)."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from tfc.utils.ssrf_guard import SsrfBlocked, SsrfResponse
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
