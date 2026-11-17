"""Media URL detection must not send video bytes to the image decoder."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from agentic_eval.core.utils import functions, llm_payloads
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


# --------------------------------------------------------------------------
# The result of an evaluation whose input was a video link says the judge was
# given only the link: video is not an eval modality.
# --------------------------------------------------------------------------

VIDEO_URL = "https://cdn.example.com/video.mp4"
VIDEO_NOTE = "Note (clip): the video was passed as a link and not viewed."
JUDGE_PASSES = '{"result": "Pass", "explanation": "judged"}'


def _serve_video(response, body=_MP4_HEADER, content_type="application/octet-stream"):
    response.iter_content.return_value = [body]
    response.headers = {"Content-Type": content_type}


@pytest.mark.parametrize(
    "body,content_type",
    [
        (_MP4_HEADER, "application/octet-stream"),
        (b"unrecognized header", "video/mp4"),
    ],
    ids=["video-magic", "video-content-type"],
)
def test_video_link_is_listed_for_a_caller_that_asks(media_io, body, content_type):
    response, _, download_image = media_io
    _serve_video(response, body, content_type)
    video_links = []

    result = llm_payloads.detect_and_build_media_blocks(
        {"clip": VIDEO_URL}, ["clip"], video_links=video_links
    )

    assert result == ([], {})
    assert video_links == ["clip"]
    download_image.assert_not_called()


def test_a_list_of_video_links_is_listed(media_io):
    response, _, download_image = media_io
    _serve_video(response)
    video_links = []

    result = llm_payloads.detect_and_build_media_blocks(
        {"clips": ["https://cdn.example.com/a.mp4", "https://cdn.example.com/b.mp4"]},
        ["clips"],
        video_links=video_links,
    )

    assert result == ([], {})
    assert video_links == ["clips"]
    download_image.assert_not_called()


def test_an_image_link_is_not_listed_as_video(media_io):
    _, fetch, _ = media_io
    video_links = []

    _, modalities = llm_payloads.detect_and_build_media_blocks(
        {"picture": "https://cdn.example.com/picture.png"},
        ["picture"],
        video_links=video_links,
    )

    assert modalities == {"picture": "image"}
    assert video_links == []
    fetch.assert_not_called()


def test_detect_input_type_still_reports_video_as_file_to_other_callers(media_io):
    response, _, _ = media_io
    _serve_video(response)

    assert functions.detect_input_type({"clip": VIDEO_URL}) == {"clip": "file"}
    assert functions.detect_input_type(VIDEO_URL) == {"type": "file"}


def test_function_eval_parameter_check_still_sees_a_video_link_as_file(media_io):
    # EvaluationRunner._run_evaluation checks a function eval's param_modalities
    # with eval_runner.detect_input_type(value) (no keyword): it still sees "file".
    from model_hub.views import eval_runner

    response, _, _ = media_io
    _serve_video(response)

    assert eval_runner.detect_input_type(VIDEO_URL) == {"type": "file"}


def test_deterministic_parameter_check_still_sees_a_video_link_as_file(media_io):
    module = pytest.importorskip("ee.evals.futureagi.eval_deterministic.evaluator")
    response, _, _ = media_io
    _serve_video(response)
    # Built without LLM.__init__ (as test_cpe_clamp): the check reads only
    # input_type, and detects each value it is given.
    evaluator = module.DeterministicEvaluator.__new__(module.DeterministicEvaluator)
    evaluator.input_type = []

    accepted = evaluator._validate_param_modalities(
        required_keys=["clip"],
        param_modalities={"clip": ["FILE"]},
        param_values={"clip": VIDEO_URL},
    )
    with pytest.raises(ValueError, match="but received File"):
        evaluator._validate_param_modalities(
            required_keys=["clip"],
            param_modalities={"clip": ["TEXT"]},
            param_values={"clip": VIDEO_URL},
        )

    assert accepted is None


def test_recorded_input_types_still_say_file_for_a_video_link(media_io):
    from model_hub.tasks import user_evaluation
    from model_hub.views.eval_runner import EvaluationRunner

    response, _, _ = media_io
    _serve_video(response)

    assert user_evaluation._get_input_type({"clip": VIDEO_URL}) == {"clip": "file"}
    assert EvaluationRunner._get_input_type(None, {"clip": VIDEO_URL}) == {
        "clip": {"type": "file"}
    }


def test_detect_input_type_reports_video_to_a_caller_that_asks(media_io):
    response, _, _ = media_io
    _serve_video(response)

    assert functions.detect_input_type({"clip": VIDEO_URL}, video_type="video") == {
        "clip": "video"
    }


def _evaluator(cls):
    """A custom prompt evaluator built without LLM.__init__ (as test_cpe_clamp)."""
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.template_sandbox import (
        build_prompt_environment,
    )
    from agentic_eval.core_evals.fi_utils.utils import PreserveUndefined

    ev = cls.__new__(cls)
    ev.system_prompt = None
    ev._output_type = "Pass/Fail"
    ev._model = "stub-model"
    ev._choices = []
    ev._multi_choice = False
    ev._choice_scores = None
    ev._few_shot_examples = None
    ev._messages = None
    ev.provider = "openai"
    ev._is_turing = False
    ev.system_template_value = "Pass/Fail"
    ev.temperature = 0.0
    ev.max_tokens = 256
    ev.knowledge_base_id = None
    ev.check_internet = False
    ev.template_format = "mustache"
    ev.token_usage = {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0}
    ev.cost = {"total_cost": 0.0, "prompt_cost": 0.0, "completion_cost": 0.0}
    ev.env = build_prompt_environment(PreserveUndefined)
    return ev


def _oss_evaluator():
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
        CustomPromptEvaluator,
    )

    return _evaluator(CustomPromptEvaluator)


def _ee_evaluator():
    module = pytest.importorskip("ee.evals.llm.custom_prompt_evaluator.evaluator")
    return _evaluator(module.CustomPromptEvaluator)


@pytest.mark.parametrize("make", [_oss_evaluator, _ee_evaluator], ids=["oss", "ee"])
def test_custom_prompt_result_says_a_video_link_was_not_viewed(media_io, make):
    response, _, download_image = media_io
    _serve_video(response)
    ev = make()
    ev.rule_prompt = "Does {{clip}} show a cat?"

    with patch.object(ev, "call_llm", return_value=JUDGE_PASSES) as judge:
        result = ev._evaluate(required_keys=["clip"], clip=VIDEO_URL)

    assert result["reason"] == "judged\n\n" + VIDEO_NOTE
    assert result["data"]["result"] == "Pass"
    user_turn = judge.call_args.kwargs["prompt"][-1]["content"]
    assert isinstance(user_turn, str)
    assert VIDEO_URL in user_turn
    download_image.assert_not_called()


@pytest.mark.parametrize("make", [_oss_evaluator, _ee_evaluator], ids=["oss", "ee"])
def test_custom_prompt_result_without_a_video_link_is_the_judges_explanation(
    media_io, make
):
    ev = make()
    ev.rule_prompt = "Does {{picture}} show a cat?"

    with patch.object(ev, "call_llm", return_value=JUDGE_PASSES):
        result = ev._evaluate(
            required_keys=["picture"], picture="https://cdn.example.com/cat.png"
        )

    assert result["reason"] == "judged"
