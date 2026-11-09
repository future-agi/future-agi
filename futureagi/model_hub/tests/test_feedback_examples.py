"""Formatting stale feedback must not leak partial examples into the prompt."""

from unittest.mock import Mock

import pytest

from agentic_eval.core.embeddings import embedding_manager


@pytest.fixture
def manager():
    # Formatting has no database dependency; retrieval supplies these types.
    instance = embedding_manager.EmbeddingManager.__new__(
        embedding_manager.EmbeddingManager
    )
    instance.input_types = ["text", "text"]
    return instance


def format_examples(manager, examples, **kwargs):
    return manager.process_examples(
        examples,
        inputs=["prompt", "response"],
        feedback_col_name="feedback_comment",
        corrected_label_col_name="feedback_value",
        **kwargs,
    )


def valid_metadata():
    return {
        "prompt": "valid question",
        "response": "valid response",
        "feedback_comment": "use the valid correction",
        "feedback_value": "Failed",
    }


@pytest.mark.parametrize(
    "stale",
    [
        [{"prompt": "partial must not leak", "feedback_value": "Failed"}],
        [
            {
                "prompt": "partial must not leak",
                "response": None,
                "feedback_value": "Failed",
            }
        ],
        [{"prompt": "partial must not leak", "response": "unlabelled"}],
        [
            {
                "prompt": "partial must not leak",
                "response": "bad label",
                "feedback_value": {},
            }
        ],
        [None],
        ["not metadata"],
        [],
        None,
    ],
)
def test_invalid_example_is_atomic_and_later_example_survives(
    manager, stale, monkeypatch
):
    logger = Mock()
    monkeypatch.setattr(embedding_manager, "logger", logger)
    content = format_examples(manager, [stale, [valid_metadata()]])
    assert "partial must not leak" not in str(content)
    assert "valid question" in str(content)
    assert "use the valid correction" in str(content)
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args == ("feedback_example_skipped",)
    assert logger.warning.call_args.kwargs["reason"] in {
        "invalid_metadata",
        "missing_input",
        "missing_feedback",
        "invalid_feedback",
    }


def test_all_invalid_examples_do_not_leave_a_prompt_header(manager):
    assert format_examples(manager, [[{"prompt": "partial"}]]) == []


@pytest.mark.parametrize("types", [["text"], ["text", "text", "image"]])
def test_misaligned_types_do_not_return_partial_content(manager, types):
    manager.input_types = types
    assert format_examples(manager, [[valid_metadata()]]) == []


def test_missing_optional_input_is_omitted_not_fabricated(manager):
    metadata = valid_metadata()
    del metadata["response"]
    content = format_examples(manager, [[metadata]], optional_inputs={"response"})
    assert "use the valid correction" in str(content)
    assert len(content) == 3  # instruction, present input, correction
    assert content[1] == {"type": "text", "text": "valid question"}


@pytest.mark.parametrize("missing", ["feedback_comment", "feedback_value"])
def test_one_feedback_field_can_be_absent(manager, missing):
    metadata = valid_metadata()
    del metadata[missing]
    content = format_examples(manager, [[metadata]])
    assert "valid question" in str(content)
    assert (
        "Corrected Label: Failed" if missing == "feedback_comment" else "Feedback:"
    ) in str(content)


@pytest.mark.parametrize("label", [0, False])
def test_falsey_correction_is_not_discarded(manager, label):
    metadata = valid_metadata()
    metadata.pop("feedback_comment")
    metadata["feedback_value"] = label
    assert f"Corrected Label: {label}" in str(format_examples(manager, [[metadata]]))


@pytest.mark.parametrize("value", [False, 0, {"answer": ["synthetic", 2]}])
def test_text_values_keep_str_representation(manager, value):
    metadata = valid_metadata()
    metadata["response"] = value
    assert {"type": "text", "text": str(value)} in format_examples(
        manager, [[metadata]]
    )


@pytest.mark.parametrize("bad_field", ["response", "feedback", "image"])
def test_invalid_image_example_is_checked_before_upload(
    manager, monkeypatch, bad_field
):
    manager.input_types = ["image", "text"]
    upload = Mock(return_value="https://example.test/feedback.png")
    monkeypatch.setattr(embedding_manager, "upload_image_to_s3", upload)
    metadata = valid_metadata()
    metadata["prompt"] = manager.encode_path("https://example.test/input.png")
    if bad_field == "response":
        metadata.pop("response")
    elif bad_field == "feedback":
        metadata.pop("feedback_comment")
        metadata.pop("feedback_value")
    else:
        metadata["prompt"] = None
    assert format_examples(manager, [[metadata]]) == []
    upload.assert_not_called()


def test_later_invalid_image_is_decoded_before_any_upload(manager, monkeypatch):
    manager.input_types = ["image", "image"]
    upload = Mock()
    monkeypatch.setattr(embedding_manager, "upload_image_to_s3", upload)
    metadata = valid_metadata()
    metadata["prompt"] = manager.encode_path("https://example.test/input.png")
    metadata["response"] = "a"  # impossible base64 length
    assert format_examples(manager, [[metadata]]) == []
    upload.assert_not_called()


@pytest.mark.parametrize("failure", [RuntimeError("test upload failure"), None])
def test_failed_image_upload_drops_only_that_example(manager, monkeypatch, failure):
    manager.input_types = ["text", "image"]
    upload = Mock(side_effect=[failure, "https://example.test/feedback.png"])
    monkeypatch.setattr(embedding_manager, "upload_image_to_s3", upload)
    stale = valid_metadata()
    stale["prompt"] = "partial must not leak"
    stale["response"] = manager.encode_path("https://example.test/first.png")
    valid = valid_metadata()
    valid["response"] = manager.encode_path("https://example.test/second.png")
    content = format_examples(manager, [[stale], [valid]])
    assert "partial must not leak" not in str(content)
    assert "valid question" in str(content)
    assert {
        "type": "image_url",
        "image_url": {"url": "https://example.test/feedback.png"},
    } in content
    assert upload.call_count == 2
