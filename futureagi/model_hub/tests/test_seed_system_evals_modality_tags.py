"""Seeder tags an eval "PDF" when it accepts a PDF input
(config.param_modalities), and only PDF."""

from model_hub.management.commands.seed_system_evals import (
    _eval_accepts_pdf,
    _yaml_to_template_fields,
)


def test_accepts_pdf_when_a_param_lists_pdf():
    assert _eval_accepts_pdf({"param_modalities": {"input": ["TEXT", "PDF"]}}) is True


def test_accepts_pdf_is_case_insensitive():
    assert _eval_accepts_pdf({"param_modalities": {"input": ["pdf"]}}) is True


def test_accepts_pdf_checks_all_params():
    config = {"param_modalities": {"input": ["TEXT"], "context": ["PDF"]}}
    assert _eval_accepts_pdf(config) is True


def test_not_pdf_when_no_pdf_modality():
    config = {"param_modalities": {"input": ["TEXT", "AUDIO", "IMAGE", "JSON"]}}
    assert _eval_accepts_pdf(config) is False


def test_not_pdf_when_missing_or_empty():
    assert _eval_accepts_pdf({}) is False
    assert _eval_accepts_pdf(None) is False
    assert _eval_accepts_pdf({"param_modalities": {}}) is False
    assert _eval_accepts_pdf({"param_modalities": {"input": None}}) is False


def test_yaml_fields_add_pdf_tag_and_preserve_existing():
    eval_def = {
        "eval_id": 999001,
        "name": "th5162_pdf_eval",
        "_track": "agent",
        "eval_tags": ["Data Leakage", "Safety"],
        "config": {"param_modalities": {"input": ["TEXT", "PDF"]}},
    }
    tags = _yaml_to_template_fields(eval_def)["eval_tags"]
    assert "PDF" in tags
    assert "Data Leakage" in tags and "Safety" in tags


def test_yaml_fields_only_touch_pdf_never_other_modalities():
    # Accepts TEXT/AUDIO/IMAGE but NOT PDF → no tag should be added at all.
    eval_def = {
        "eval_id": 999002,
        "name": "th5162_non_pdf_eval",
        "_track": "agent",
        "eval_tags": ["Safety"],
        "config": {"param_modalities": {"input": ["TEXT", "AUDIO", "IMAGE"]}},
    }
    tags = _yaml_to_template_fields(eval_def)["eval_tags"]
    assert tags == ["Safety"]
    assert "Text" not in tags and "Audio" not in tags and "Image" not in tags


def test_yaml_fields_dedupe_when_already_pdf_tagged():
    eval_def = {
        "eval_id": 999003,
        "name": "th5162_already_pdf",
        "_track": "agent",
        "eval_tags": ["PDF"],
        "config": {"param_modalities": {"input": ["PDF"]}},
    }
    tags = _yaml_to_template_fields(eval_def)["eval_tags"]
    assert tags.count("PDF") == 1
