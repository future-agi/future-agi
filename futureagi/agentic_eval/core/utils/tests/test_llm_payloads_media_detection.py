"""Media URL detection must not send video bytes to the image decoder."""

from unittest.mock import MagicMock, Mock

import pytest

from agentic_eval.core.utils import functions, llm_payloads
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from agentic_eval.core_evals.fi_utils.exceptions import MediaNotAccessibleError

_MP4_HEADER = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
_PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 16


@pytest.fixture
def media_io(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status_code = 200
    fetch = Mock(return_value=response)
    download_image = Mock(return_value=b"image-bytes")
    monkeypatch.setattr(functions.requests, "get", fetch)
    monkeypatch.setattr(llm_payloads, "download_image_from_url", download_image)
    return response, fetch, download_image


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/video.mp4",
        "https://cdn.example.com/video.MP4",
        "https://cdn.example.com/video.mp4?download=1",
        "https://cdn.example.com/video.MP4?download=1",
    ],
)
@pytest.mark.parametrize(
    "body,content_type",
    [
        (_MP4_HEADER, "application/octet-stream"),
        (_MP4_HEADER, "image/png"),
        (b"unrecognized header", "Video/MP4"),
    ],
    ids=["video-magic", "magic-over-image-header", "video-content-type"],
)
def test_mp4_url_is_sniffed_and_kept_as_text(media_io, url, body, content_type):
    response, fetch, download_image = media_io
    response.iter_content.return_value = [body]
    response.headers = {"Content-Type": content_type}
    download_image.side_effect = ValueError("Video bytes are not an image")
    inputs = {"clip": url}

    assert llm_payloads.detect_and_build_media_blocks(inputs, ["clip"]) == ([], {})

    fetch.assert_called_once_with(url, timeout=100, stream=True)
    download_image.assert_not_called()
    assert inputs == {"clip": url}


def test_mp4_extension_does_not_override_sniffed_image(media_io):
    response, fetch, download_image = media_io
    url = "https://cdn.example.com/image.mp4"
    response.iter_content.return_value = [_PNG_HEADER]
    response.headers = {"Content-Type": "application/octet-stream"}

    blocks, modalities = llm_payloads.detect_and_build_media_blocks(
        {"picture": url}, ["picture"]
    )

    assert modalities == {"picture": "image"}
    assert [block["type"] for block in blocks] == ["text", "image_url", "text"]
    fetch.assert_called_once_with(url, timeout=100, stream=True)
    download_image.assert_called_once_with(url)


@pytest.mark.parametrize(
    "extension", ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tiff"]
)
def test_image_extensions_keep_the_fast_path(media_io, extension):
    _, fetch, download_image = media_io
    url = f"https://cdn.example.com/picture.{extension}?download=1"

    blocks, modalities = llm_payloads.detect_and_build_media_blocks(
        {"picture": url}, ["picture"]
    )

    assert modalities == {"picture": "image"}
    assert [block["type"] for block in blocks] == ["text", "image_url", "text"]
    fetch.assert_not_called()
    download_image.assert_called_once_with(url)


def test_unreachable_mp4_url_still_reports_inaccessible_media(media_io):
    response, fetch, download_image = media_io
    url = "https://cdn.example.com/missing.mp4"
    response.status_code = 404

    with pytest.raises(MediaNotAccessibleError):
        llm_payloads.detect_and_build_media_blocks({"clip": url}, ["clip"])

    fetch.assert_called_once_with(url, timeout=100, stream=True)
    download_image.assert_not_called()


def test_non_video_unsupported_file_still_reports_inaccessible_media(media_io):
    response, fetch, download_image = media_io
    url = "https://cdn.example.com/archive.bin"
    response.iter_content.return_value = [b"unrecognized header"]
    response.headers = {"Content-Type": "application/octet-stream"}

    with pytest.raises(MediaNotAccessibleError):
        llm_payloads.detect_and_build_media_blocks({"file": url}, ["file"])

    fetch.assert_called_once_with(url, timeout=100, stream=True)
    download_image.assert_not_called()


def test_explicit_image_type_still_validates_mp4_url_as_image(media_io):
    _, fetch, download_image = media_io
    url = "https://cdn.example.com/video.mp4"
    download_image.side_effect = ValueError("Video bytes are not an image")

    with pytest.raises(MediaNotAccessibleError):
        llm_payloads.detect_and_build_media_blocks(
            {"clip": url}, ["clip"], input_data_types={"clip": "image"}
        )

    fetch.assert_not_called()
    download_image.assert_called_once_with(url)


@pytest.mark.parametrize(
    "body,content_type",
    [(_MP4_HEADER, "application/octet-stream"), (b"unknown", "video/mp4")],
    ids=["video-magic", "video-content-type"],
)
def test_video_url_reaches_eval_judge_as_text(
    media_io, monkeypatch, body, content_type
):
    response, fetch, download_image = media_io
    response.iter_content.return_value = [body]
    response.headers = {"Content-Type": content_type}
    url = "https://cdn.example.com/clip.mp4"
    evaluator = CustomPromptEvaluator(
        rule_prompt="Assess the URL text, not video content: {{clip}}",
        output_type="score",
        model="gpt-4o",
        provider="openai",
        api_key="synthetic-test-key",
    )
    judge = Mock(return_value='{"result": 0.75, "explanation": "URL text only"}')
    monkeypatch.setattr(evaluator, "call_llm", judge)
    download_image.side_effect = ValueError("Video bytes are not an image")

    result = evaluator._evaluate(clip=url, required_keys=["clip"])

    assert result["data"]["result"] == 0.75
    assert result["reason"] == "URL text only"
    judge.assert_called_once()
    user_message = judge.call_args.kwargs["prompt"][-1]
    assert user_message["role"] == "user"
    assert isinstance(user_message["content"], str)
    assert f"<clip>{url}</clip>" in user_message["content"]
    fetch.assert_called_once_with(url, timeout=100, stream=True)
    download_image.assert_not_called()
