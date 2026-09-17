"""
Tests for the preprocessing completeness guard.

``preprocess_inputs`` warns when a preprocessor returns without the
``_``-prefixed kwargs the eval body reads back, so a no-op or a swallowed
failure shows up in the logs instead of only as a wrong score.
"""

from __future__ import annotations

from unittest.mock import patch

from evaluations.engine.preprocessing import (
    PREPROCESSOR_OUTPUTS,
    PREPROCESSORS,
    preprocess_inputs,
    register_preprocessor,
)


def test_in_place_resolvers_declare_no_outputs():
    """psnr / ssim / image_properties rewrite existing keys, so nothing to assert."""
    for eval_name in ("psnr", "ssim", "image_properties"):
        assert PREPROCESSOR_OUTPUTS[eval_name] == ()


def test_injecting_preprocessors_declare_their_outputs():
    assert PREPROCESSOR_OUTPUTS["clip_score"] == (
        "_image_embeddings",
        "_text_embeddings",
    )
    assert PREPROCESSOR_OUTPUTS["fid_score"] == ("_fid_precomputed_score",)
    assert PREPROCESSOR_OUTPUTS["meteor_score"] == ("_meteor_precomputed_score",)


def test_warns_when_clip_preprocessing_produces_no_embeddings():
    """The no-op path: clip returns early when images or text is missing."""
    with patch("evaluations.engine.preprocessing.logger") as mock_logger:
        out = preprocess_inputs("clip_score", {"images": "", "text": "a caption"})

    assert "_image_embeddings" not in out
    mock_logger.warning.assert_called_once_with(
        "preprocessing_incomplete",
        eval_name="clip_score",
        missing_keys=["_image_embeddings", "_text_embeddings"],
    )


def test_no_warning_when_expected_keys_are_present():
    @register_preprocessor("_test_produces", produces=("_computed",))
    def _produces(inputs):
        inputs["_computed"] = 1
        return inputs

    try:
        with patch("evaluations.engine.preprocessing.logger") as mock_logger:
            out = preprocess_inputs("_test_produces", {})

        assert out["_computed"] == 1
        mock_logger.warning.assert_not_called()
    finally:
        PREPROCESSORS.pop("_test_produces", None)
        PREPROCESSOR_OUTPUTS.pop("_test_produces", None)


def test_no_warning_when_preprocessor_reports_its_own_error():
    """A handled failure already surfaces through `_..._error`."""
    with patch("evaluations.engine.preprocessing.logger") as mock_logger:
        out = preprocess_inputs("meteor_score", {"reference": "a", "hypothesis": ""})

    assert out["_meteor_error"] == "Missing reference or hypothesis"
    warned = [
        call
        for call in mock_logger.warning.call_args_list
        if call.args and call.args[0] == "preprocessing_incomplete"
    ]
    assert warned == []


def test_warns_when_preprocessor_raises():
    @register_preprocessor("_test_raises", produces=("_computed",))
    def _raises(inputs):
        raise RuntimeError("boom")

    try:
        with patch("evaluations.engine.preprocessing.logger") as mock_logger:
            out = preprocess_inputs("_test_raises", {"a": 1})

        assert out == {"a": 1}
        mock_logger.warning.assert_any_call(
            "preprocessing_incomplete",
            eval_name="_test_raises",
            missing_keys=["_computed"],
        )
    finally:
        PREPROCESSORS.pop("_test_raises", None)
        PREPROCESSOR_OUTPUTS.pop("_test_raises", None)


def test_unregistered_eval_is_untouched():
    with patch("evaluations.engine.preprocessing.logger") as mock_logger:
        out = preprocess_inputs("not_a_registered_eval", {"a": 1})

    assert out == {"a": 1}
    mock_logger.warning.assert_not_called()
