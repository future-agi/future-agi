"""SSRF guard on tfc.utils.storage's URL-download helpers (TH-5648 follow-up)."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from tfc.utils.ssrf_guard import SsrfResponse
from tfc.utils.storage import (
    _ssrf_safe_get,
    convert_image_from_url_to_base64,
    download_audio_from_url,
    download_document_from_url,
    download_image_from_url,
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
