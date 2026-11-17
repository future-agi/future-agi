"""An agent evaluation given a video link must not send it as an image.

Video is not an eval modality: the link is kept as text, as the custom prompt
evaluator does since 1673. These tests read the agent evaluator's own media
classification (detect_eval_media, which _evaluate uses before the agent runs),
the media blocks it then builds, and the placeholder its prompt rendering uses.
"""

from unittest.mock import MagicMock, Mock

import pytest

from agentic_eval.core.utils import functions
from ee.evals.llm.agent_evaluator import evaluator as agent_module
from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

_MP4_HEADER = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


@pytest.fixture
def served(monkeypatch):
    """Serve every URL as an MP4 to the content sniff, with no network."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status_code = 200
    response.iter_content.return_value = [_MP4_HEADER]
    response.headers = {"Content-Type": "video/mp4"}
    fetch = Mock(return_value=response)
    monkeypatch.setattr(functions.requests, "get", fetch)
    return fetch


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/video.mp4",
        "https://cdn.example.com/video.MP4?download=1",
    ],
)
def test_agent_evaluation_does_not_take_a_video_link_as_an_image(served, url):
    urls, media_types, _ = AgentEvaluator.detect_eval_media(
        {"clip": url}, raise_on_unfetchable=True
    )

    assert media_types.get(url) != "image"
    assert url not in urls


def test_agent_evaluation_sends_no_image_block_for_a_video_link(served, monkeypatch):
    url = "https://cdn.example.com/video.mp4"
    downloaded = MagicMock()
    downloaded.read.return_value = _MP4_HEADER
    downloaded.headers = {"Content-Type": "video/mp4"}
    monkeypatch.setattr("urllib.request.urlopen", Mock(return_value=downloaded))

    urls, media_types, url_to_key = AgentEvaluator.detect_eval_media({"clip": url})
    blocks = agent_module._build_openai_media_blocks(urls, media_types, url_to_key)

    assert [b for b in blocks if b.get("type") == "image_url"] == []


def test_agent_prompt_does_not_call_a_video_link_an_image():
    placeholder = AgentEvaluator._media_placeholder("https://cdn.example.com/video.mp4")

    assert placeholder is None or "image" not in placeholder


@pytest.mark.parametrize("extension", ["png", "jpg", "jpeg", "gif", "webp", "svg"])
def test_image_links_still_take_the_image_path(served, extension):
    url = f"https://cdn.example.com/picture.{extension}"

    urls, media_types, _ = AgentEvaluator.detect_eval_media({"picture": url})

    assert urls == [url]
    assert media_types == {url: "image"}
    served.assert_not_called()
    assert AgentEvaluator._media_placeholder(url) == (
        "[image content provided as input — see the attached image block]"
    )


def test_agent_evaluation_lists_a_video_link_for_its_note(served):
    video_links = []

    urls, _, _ = AgentEvaluator.detect_eval_media(
        {"clip": "https://cdn.example.com/video.mp4"}, video_links=video_links
    )

    assert urls == []
    assert video_links == ["clip"]


@pytest.fixture
def evaluator():
    """A bare AgentEvaluator (as test_evaluator_failure_paths)."""
    return AgentEvaluator(
        rule_prompt="check that the response is correct",
        model="turing_large",
        output_type="Pass/Fail",
        organization_id="org-1",
        workspace_id="ws-1",
    )


JUDGED = {"content": '{"result": "Pass", "explanation": "judged"}', "tool_calls": []}


def test_agent_result_says_a_video_link_was_not_viewed(evaluator):
    evaluator._video_links = ["clip"]

    out = evaluator._build_result(JUDGED, runtime_ms=10)

    assert out["reason"] == (
        "judged\n\nNote (clip): the video was passed as a link and not viewed."
    )
    assert out["data"]["result"] == "Pass"


def test_agent_result_without_a_video_link_is_the_judges_explanation(evaluator):
    out = evaluator._build_result(JUDGED, runtime_ms=10)

    assert out["reason"] == "judged"
