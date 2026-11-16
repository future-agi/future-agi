"""Feedback text values retain their existing string representation."""

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


@pytest.mark.parametrize("value", [False, 0, {"answer": ["synthetic", 2]}])
def test_text_values_keep_str_representation(manager, value):
    metadata = valid_metadata()
    metadata["response"] = value
    assert {"type": "text", "text": str(value)} in format_examples(
        manager, [[metadata]]
    )
