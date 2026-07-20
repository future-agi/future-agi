"""Tests for Stage-1 media-extension detection in llm_payloads.

`_IMAGE_EXT_PAT` drives the fast-path modality guess in
``detect_and_build_media_blocks``: a URL matching it is routed straight to
the image content builder (which downloads the bytes and validates them with
Pillow). Only genuine still-image extensions belong here — a video URL that
matches would be forced down the image path and crash on Pillow validation.
"""

import pytest

from agentic_eval.core.utils.llm_payloads import _IMAGE_EXT_PAT

IMAGE_EXTS = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tiff"]
VIDEO_EXTS = ["mp4", "mov", "avi", "webm", "mkv"]


@pytest.mark.parametrize("ext", IMAGE_EXTS)
def test_image_url_is_classified_as_image(ext):
    assert _IMAGE_EXT_PAT.search(f"https://cdn.example.com/asset.{ext}")


@pytest.mark.parametrize("ext", IMAGE_EXTS)
def test_image_url_with_query_string_is_classified_as_image(ext):
    assert _IMAGE_EXT_PAT.search(f"https://cdn.example.com/asset.{ext}?v=2")


@pytest.mark.parametrize("ext", VIDEO_EXTS)
def test_video_url_is_not_classified_as_image(ext):
    # A video URL must not match the image extension pattern, otherwise it is
    # routed to the image builder and crashes on Pillow validation.
    assert _IMAGE_EXT_PAT.search(f"https://cdn.example.com/clip.{ext}") is None
