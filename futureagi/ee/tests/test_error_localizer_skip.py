from unittest.mock import MagicMock, patch

import pytest

from ee.evals.localizer.error_localizer import ErrorLocalizer


def _localizer(input_data, input_type):
    el = ErrorLocalizer(
        eval_name="t",
        rule_prompt="r",
        input=input_data,
        input_type=input_type,
        evaluation_result="Failed",
        evaluation_explanation="",
        choices=[],
    )
    el._llm = MagicMock()
    # Default picker response — first key
    first_key = next(iter(input_data), "doc")
    el._llm._get_completion_content.return_value = f"<selected_input_key>{first_key}</selected_input_key>"
    return el


@pytest.mark.unit
@pytest.mark.parametrize("unsupported_type", ["pdf", "file"])
def test_unsupported_media_short_circuits_after_selection(unsupported_type):
    el = _localizer(
        input_data={"q": "hi", "doc": "x"},
        input_type={"q": "text", "doc": unsupported_type},
    )
    el._llm._get_completion_content.return_value = "<selected_input_key>doc</selected_input_key>"

    result = el.localize_errors()

    assert result.analysis == {}
    assert result.selected_key == "doc"
    assert result.skip_reason is not None
    assert "doc" in result.skip_reason
    assert unsupported_type in result.skip_reason


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
def test_text_target_dispatches_to_localize(mock_run):
    mock_run.return_value = {"content": "{\"entries\": []}"}

    with patch("ee.evals.localizer.error_localizer._split_into_sentences") as mock_split:
        mock_split.return_value = {"sentence_1": {"text": "hello world", "start_idx": 0, "end_idx": 11}}
        el = _localizer(input_data={"q": "hello world"}, input_type={"q": "text"})
        result = el.localize_errors()

    assert result.selected_key == "q"
    assert result.skip_reason is None
    assert "input_1" in result.analysis


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._create_audio_segments")
def test_audio_target_dispatches_to_localize(mock_segs, mock_run):
    mock_segs.return_value = {
        "segment_1": {"url": "s3://x", "duration": 5.0, "start_time": 0.0, "end_time": 5.0, "audio_bytes": "b64"},
    }
    mock_run.return_value = {"content": "{\"entries\": []}"}

    el = _localizer(
        input_data={"clip": "http://example.com/a.mp3"},
        input_type={"clip": "audio"},
    )
    result = el.localize_errors()

    assert result.selected_key == "clip"
    assert result.skip_reason is None
    mock_segs.assert_called_once()


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._build_full_image_block")
@patch("ee.evals.localizer.error_localizer._create_overlapping_patches")
def test_image_target_dispatches_to_localize(mock_patches, mock_thumb, mock_run):
    mock_patches.return_value = {
        "patch_1": {"url": "s3://x", "image_b64": "b64", "coordinates": {"top_left": (0, 0), "bottom_right": (100, 100)}},
    }
    mock_thumb.return_value = ([], 100, 100)
    mock_run.return_value = {"content": "{\"entries\": []}"}

    el = _localizer(
        input_data={"pic": "http://example.com/a.jpg"},
        input_type={"pic": "image"},
    )
    result = el.localize_errors()

    assert result.selected_key == "pic"
    assert result.skip_reason is None
    mock_patches.assert_called_once()


@pytest.mark.unit
@patch("ee.evals.localizer.error_localizer.asyncio.run")
@patch("ee.evals.localizer.error_localizer._build_full_image_block")
@patch("ee.evals.localizer.error_localizer._create_overlapping_patches")
def test_images_with_single_item_normalises_to_image(mock_patches, mock_thumb, mock_run):
    mock_patches.return_value = {
        "patch_1": {"url": "s3://x", "image_b64": "b64", "coordinates": {"top_left": (0, 0), "bottom_right": (100, 100)}},
    }
    mock_thumb.return_value = ([], 100, 100)
    mock_run.return_value = {"content": "{\"entries\": []}"}

    el = _localizer(
        input_data={"pic": ["http://example.com/a.jpg"]},
        input_type={"pic": "images"},
    )
    result = el.localize_errors()

    assert result.selected_key == "pic"
    assert result.skip_reason is None
    # normalised to image. patch creator called with the unwrapped URL.
    mock_patches.assert_called_once_with("http://example.com/a.jpg")


@pytest.mark.unit
@pytest.mark.parametrize(
    "multi_image_value",
    [
        pytest.param([], id="empty-list"),
        pytest.param(["a.jpg", "b.jpg"], id="two-items"),
        pytest.param(["a.jpg", "b.jpg", "c.jpg"], id="three-items"),
    ],
)
def test_images_multi_or_empty_is_not_chunkable(multi_image_value):
    el = _localizer(
        input_data={"pic": multi_image_value},
        input_type={"pic": "images"},
    )

    result = el.localize_errors()

    assert result.analysis == {}
    assert result.selected_key == "pic"
    assert result.skip_reason is not None
    assert "pic" in result.skip_reason


@pytest.mark.unit
def test_no_input_types_skips_cleanly():
    el = _localizer(input_data={}, input_type={})

    result = el.localize_errors()

    assert result.analysis == {}
    assert result.selected_key is None
    assert result.skip_reason is not None


@pytest.mark.unit
def test_single_input_bypasses_selection_llm():
    with patch("ee.evals.localizer.error_localizer.asyncio.run") as mock_run:
        mock_run.return_value = {"content": "{\"entries\": []}"}
        with patch("ee.evals.localizer.error_localizer._split_into_sentences") as mock_split:
            mock_split.return_value = {"sentence_1": {"text": "x", "start_idx": 0, "end_idx": 1}}
            el = _localizer(input_data={"only_input": "text"}, input_type={"only_input": "text"})
            el.localize_errors()

    el._llm._get_completion_content.assert_not_called()


@pytest.mark.unit
def test_multi_input_uses_selection_llm():
    with patch("ee.evals.localizer.error_localizer.asyncio.run") as mock_run:
        mock_run.return_value = {"content": "{\"entries\": []}"}
        with patch("ee.evals.localizer.error_localizer._split_into_sentences") as mock_split:
            mock_split.return_value = {"sentence_1": {"text": "r", "start_idx": 0, "end_idx": 1}}
            el = _localizer(
                input_data={"prompt": "p", "context": "c", "response": "r"},
                input_type={"prompt": "text", "context": "text", "response": "text"},
            )
            el._llm._get_completion_content.return_value = "<selected_input_key>response</selected_input_key>"
            el.localize_errors()

    el._llm._get_completion_content.assert_called()


@pytest.mark.unit
def test_non_chunkable_target_skips_when_only_input():
    el = _localizer(input_data={"score": 0.42}, input_type={"score": "number"})

    result = el.localize_errors()

    assert result.analysis == {}
    assert result.selected_key == "score"
    assert result.skip_reason is not None
    assert "score" in result.skip_reason
    assert "number" in result.skip_reason
