"""Unit tests for the runtime GT injection module.

Embedding + retrieval is exercised end-to-end by
``test_ground_truth_service.py`` (with a mocked EmbeddingManager) and
by the live ``gt_roundtrip_test`` management command. This file covers
the pure-Python helpers in ``model_hub/utils/ground_truth_retrieval.py``:
the skip gate, the few-shot formatter, the label-column lookup, and
the output-type validator.
"""

from __future__ import annotations

import pytest

from model_hub.utils.eval_input_validation import is_empty_value
from model_hub.utils.ground_truth_retrieval import (
    build_ground_truth_blocks,
    detect_input_column_types,
    get_label_columns,
    has_usable_inputs_for_gt,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("\n\t  ", True),
        ([], True),
        ({}, True),
        ((), True),
        (set(), True),
        (0, False),
        (0.0, False),
        (False, False),
        ("hello", False),
        ("  hello  ", False),
        ([1], False),
        ({"k": "v"}, False),
        (42, False),
    ],
)
def test_is_empty_value(value, expected):
    assert is_empty_value(value) is expected


# has_usable_inputs_for_gt: the eval-runner skip rule


def test_skip_when_variable_mapping_is_empty():
    """Evals that declare no template variables (or haven't been mapped) should never get GT injection; there's nothing to query against."""
    assert has_usable_inputs_for_gt({}, {"question": "hi"}) is False
    assert has_usable_inputs_for_gt(None, {"question": "hi"}) is False


def test_skip_when_runtime_inputs_missing():
    assert has_usable_inputs_for_gt({"q": "col"}, None) is False
    assert has_usable_inputs_for_gt({"q": "col"}, {}) is False
    assert has_usable_inputs_for_gt({"q": "col"}, "not a dict") is False


def test_skip_when_every_mapped_value_is_empty():
    mapping = {"question": "q_col", "context": "ctx_col"}
    assert (
        has_usable_inputs_for_gt(mapping, {"question": "", "context": "   "})
        is False
    )
    assert (
        has_usable_inputs_for_gt(mapping, {"question": None, "context": []})
        is False
    )


def test_proceed_when_any_mapped_value_is_present():
    mapping = {"question": "q_col", "context": "ctx_col"}
    assert (
        has_usable_inputs_for_gt(
            mapping, {"question": "what time is it", "context": ""}
        )
        is True
    )
    # Falsy-but-legitimate scalars still gate-open.
    assert has_usable_inputs_for_gt({"score": "s_col"}, {"score": 0}) is True
    assert has_usable_inputs_for_gt({"flag": "f_col"}, {"flag": False}) is True


def test_accepts_runtime_keyed_by_gt_column_name():
    """Legacy callers sometimes key runtime values by the GT column name
    rather than the template variable. The gate accepts either keying."""
    assert has_usable_inputs_for_gt({"question": "q_col"}, {"q_col": "hi"}) is True


def test_list_mapping_opens_gate_on_any_present_column():
    mapping = {"input": ["text_col", "image_col"]}
    assert (
        has_usable_inputs_for_gt(
            mapping, {"text_col": "", "image_col": "https://x/y.png"}
        )
        is True
    )
    assert (
        has_usable_inputs_for_gt(
            mapping, {"text_col": "", "image_col": ""}
        )
        is False
    )


# ─────────────────────────────────────────────────────────────────────
# get_label_columns
# ─────────────────────────────────────────────────────────────────────


def test_label_cols_empty_mapping():
    assert get_label_columns(None) == ("", "")
    assert get_label_columns({}) == ("", "")


def test_label_cols_canonical_keys():
    assert get_label_columns({"output": "v", "explanation": "r"}) == ("v", "r")


def test_label_cols_legacy_keys_accepted():
    assert get_label_columns(
        {"expected_output": "v", "reasoning": "r"}
    ) == ("v", "r")
    assert get_label_columns({"expected_output": "v", "reason": "r"}) == (
        "v",
        "r",
    )


def test_label_cols_canonical_wins_over_legacy():
    assert get_label_columns(
        {"output": "new", "expected_output": "old"}
    ) == ("new", "")


def test_label_cols_explanation_is_optional():
    assert get_label_columns({"output": "v"}) == ("v", "")


def test_label_cols_list_value_picks_first():
    assert get_label_columns({"output": ["first", "second"]}) == (
        "first",
        "",
    )


# ─────────────────────────────────────────────────────────────────────
# build_ground_truth_blocks
# ─────────────────────────────────────────────────────────────────────


def _texts(blocks):
    return [b["text"] for b in blocks if b.get("type") == "text"]


def test_detect_input_column_types_empty():
    assert detect_input_column_types([], {"q": "q_col"}) == {}
    assert detect_input_column_types([{"q_col": "hi"}], None) == {}


def test_detect_input_column_types_stamps_modalities(monkeypatch):
    def fake_detect(*_args, **_kwargs):
        return [], {"img_col": "image", "q_col": "text", "noisy_col": ""}

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.detect_and_build_media_blocks",
        fake_detect,
    )
    types = detect_input_column_types(
        [{"img_col": "https://x/y.png", "q_col": "hi"}],
        {"screenshot": "img_col", "question": "q_col"},
    )
    assert types == {"img_col": "image", "q_col": "text"}


def test_build_ground_truth_blocks_empty():
    assert build_ground_truth_blocks(
        [], variable_mapping=None, role_mapping=None
    ) == []


def test_build_ground_truth_blocks_text_only_uses_labelled_framing():
    blocks = build_ground_truth_blocks(
        [{"q": "hi", "verdict": "Pass", "reason": "polite"}],
        variable_mapping={"question": "q"},
        role_mapping={"output": "verdict", "explanation": "reason"},
    )
    texts = _texts(blocks)
    assert texts[0] == "## Reference example 1"
    assert "Inputs:" in texts
    assert "- question: hi" in texts
    assert "Expected output: Pass" in texts
    assert "Explanation: polite" in texts


def test_build_ground_truth_blocks_omits_explanation_when_not_mapped():
    blocks = build_ground_truth_blocks(
        [{"q": "hi", "verdict": "Pass"}],
        variable_mapping={"question": "q"},
        role_mapping={"output": "verdict"},
    )
    texts = _texts(blocks)
    assert "Expected output: Pass" in texts
    assert not any(t.startswith("Explanation:") for t in texts)


def test_build_ground_truth_blocks_image_column_emits_image_url_block(monkeypatch):
    # Patch the shared detector so we don't hit the network for the sniff
    # fallback; pretend the image column was detected via the fast regex.
    import model_hub.utils.ground_truth_retrieval as gt_mod

    def fake_detect(*_args, **_kwargs):
        return [], {"img_col": "image"}

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.detect_and_build_media_blocks",
        fake_detect,
    )

    def fake_media(value, media_type, key):
        return [
            {"type": "text", "text": f"<{key}>"},
            {"type": "image_url", "image_url": {"url": value}},
            {"type": "text", "text": f"</{key}>"},
        ]

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.build_media_content_block",
        fake_media,
    )

    blocks = build_ground_truth_blocks(
        [{"img_col": "https://example.com/a.png", "verdict": "Pass"}],
        variable_mapping={"screenshot": "img_col"},
        role_mapping={"output": "verdict"},
    )
    image_blocks = [b for b in blocks if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"] == "https://example.com/a.png"


def test_build_ground_truth_blocks_uses_supplied_column_types_skipping_sniff(monkeypatch):
    sniffed: list = []

    def boom_detect(*_args, **_kwargs):
        sniffed.append(1)
        return [], {}

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.detect_and_build_media_blocks",
        boom_detect,
    )

    def fake_media(value, media_type, key):
        return [
            {"type": "text", "text": f"<{key}>"},
            {"type": "image_url", "image_url": {"url": value}},
        ]

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.build_media_content_block",
        fake_media,
    )

    blocks = build_ground_truth_blocks(
        [{"img_col": "https://example.com/a.png", "verdict": "Pass"}],
        variable_mapping={"screenshot": "img_col"},
        role_mapping={"output": "verdict"},
        column_types={"img_col": "image"},
    )

    assert sniffed == [], "sniff must be skipped when column_types is supplied"
    image_blocks = [b for b in blocks if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"] == "https://example.com/a.png"


def test_build_ground_truth_blocks_falls_back_to_sniff_when_column_types_missing(monkeypatch):
    sniffed: list = []

    def fake_detect(inputs, required_keys):
        sniffed.append(set(required_keys))
        return [], {"img_col": "image"}

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.detect_and_build_media_blocks",
        fake_detect,
    )

    def fake_media(value, media_type, key):
        return [{"type": "image_url", "image_url": {"url": value}}]

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.build_media_content_block",
        fake_media,
    )

    blocks = build_ground_truth_blocks(
        [{"img_col": "https://example.com/a.png", "verdict": "Pass"}],
        variable_mapping={"screenshot": "img_col"},
        role_mapping={"output": "verdict"},
        column_types=None,
    )

    assert sniffed == [{"img_col"}]
    assert any(b.get("type") == "image_url" for b in blocks)


def test_build_ground_truth_blocks_falls_back_to_sniff_when_column_types_empty(monkeypatch):
    sniffed: list = []

    def fake_detect(inputs, required_keys):
        sniffed.append(1)
        return [], {}

    monkeypatch.setattr(
        "agentic_eval.core.utils.llm_payloads.detect_and_build_media_blocks",
        fake_detect,
    )

    build_ground_truth_blocks(
        [{"q": "hi", "verdict": "Pass"}],
        variable_mapping={"question": "q"},
        role_mapping={"output": "verdict"},
        column_types={},
    )

    assert sniffed == [1], "empty dict must trigger the sniff fallback (legacy vectors)"


def test_build_ground_truth_blocks_multiple_examples_keep_per_example_headers():
    blocks = build_ground_truth_blocks(
        [
            {"q": "first", "verdict": "Pass"},
            {"q": "second", "verdict": "Fail"},
        ],
        variable_mapping={"question": "q"},
        role_mapping={"output": "verdict"},
    )
    texts = _texts(blocks)
    assert texts.count("## Reference example 1") == 1
    assert texts.count("## Reference example 2") == 1
    assert "- question: first" in texts
    assert "- question: second" in texts
