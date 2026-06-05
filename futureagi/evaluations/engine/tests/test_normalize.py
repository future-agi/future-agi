"""Unit tests for the canonical eval-output normalization helpers."""

import json

import pytest

from evaluations.engine.normalize import (
    build_simulate_eval_payload,
    coerce_to_legacy_scalar,
    dual_write_eval_value,
    eval_config_output,
)


# ── coerce_to_legacy_scalar ──────────────────────────────────────────────


class TestCoerceToLegacyScalar:
    def test_pass_fail_passed_string(self):
        assert coerce_to_legacy_scalar("Passed", "Pass/Fail") == "Passed"

    def test_pass_fail_failed_string(self):
        assert coerce_to_legacy_scalar("Failed", "Pass/Fail") == "Failed"

    def test_pass_fail_bool_true(self):
        assert coerce_to_legacy_scalar(True, "Pass/Fail") == "Passed"

    def test_pass_fail_bool_false(self):
        assert coerce_to_legacy_scalar(False, "Pass/Fail") == "Failed"

    def test_score_plain_float(self):
        assert coerce_to_legacy_scalar(0.7, "score") == "0.7"

    def test_score_dict_with_choice_prefers_score(self):
        """For score-output evals the FE shows the number — pick score from
        the hybrid dict shape."""
        assert (
            coerce_to_legacy_scalar({"score": 0.7, "choice": "Yes"}, "score") == "0.7"
        )

    def test_score_dict_with_choices_prefers_score(self):
        assert (
            coerce_to_legacy_scalar(
                {"score": 0.4, "choices": ["A", "B"]}, "score"
            )
            == "0.4"
        )

    def test_choices_string(self):
        assert coerce_to_legacy_scalar("Yes", "choices") == "Yes"

    def test_choices_list(self):
        # JSON-serialized so the FE parser unwraps it into multiple chips.
        assert coerce_to_legacy_scalar(["A", "B"], "choices") == '["A", "B"]'

    def test_choices_dict_with_choice_prefers_choice(self):
        """For choices-output evals the FE shows the label — pick choice from
        the hybrid dict shape."""
        assert (
            coerce_to_legacy_scalar({"score": 0.7, "choice": "Yes"}, "choices")
            == "Yes"
        )

    def test_choices_dict_with_choices_returns_json_array(self):
        assert (
            coerce_to_legacy_scalar(
                {"score": 0.4, "choices": ["A", "B"]}, "choices"
            )
            == '["A", "B"]'
        )

    def test_none_returns_none(self):
        assert coerce_to_legacy_scalar(None, "score") is None

    def test_unknown_config_output_passes_through_str(self):
        assert coerce_to_legacy_scalar("anything", "reason") == "anything"

    def test_score_zero_renders_as_string(self):
        assert coerce_to_legacy_scalar(0.0, "score") == "0.0"

    def test_score_dict_with_zero_score(self):
        assert coerce_to_legacy_scalar({"score": 0.0, "choice": "No"}, "score") == "0.0"

    def test_choices_dict_only_score_falls_back_to_score(self):
        """When the dict has only ``score``, return it (better than a JSON dump)."""
        assert coerce_to_legacy_scalar({"score": 0.5}, "choices") == "0.5"


# ── build_simulate_eval_payload ──────────────────────────────────────────


class TestBuildSimulateEvalPayload:
    def test_score_with_choice_dict_produces_both_projections(self):
        p = build_simulate_eval_payload(
            name="my_eval",
            output={"score": 0.7, "choice": "Yes"},
            reason="because",
            output_type="choices",
            config_output="score",
        )
        assert p["name"] == "my_eval"
        assert p["output"] == {"score": 0.7, "choice": "Yes"}
        assert p["output_scalar"] == "0.7"
        assert p["output_dict"] == {"score": 0.7, "choice": "Yes"}
        assert p["reason"] == "because"
        assert p["output_type"] == "choices"

    def test_plain_choice_string(self):
        p = build_simulate_eval_payload(
            name="tone",
            output="annoyed",
            reason="user said",
            output_type="choices",
            config_output="choices",
        )
        assert p["output"] == "annoyed"
        assert p["output_scalar"] == "annoyed"
        assert p["output_dict"] is None

    def test_pass_fail(self):
        p = build_simulate_eval_payload(
            name="task_complete",
            output="Failed",
            reason="missing step",
            output_type="Pass/Fail",
            config_output="Pass/Fail",
        )
        assert p["output_scalar"] == "Failed"
        assert p["output_dict"] is None

    def test_extra_keys_merged_but_do_not_overwrite_core(self):
        p = build_simulate_eval_payload(
            name="e",
            output=None,
            reason=None,
            output_type=None,
            config_output="score",
            extra={"status": "skipped", "output_scalar": "ignored"},
        )
        assert p["status"] == "skipped"
        # Core key was set by the helper and must not be clobbered by extra.
        assert p["output_scalar"] is None

    def test_none_output_keeps_dict_none(self):
        p = build_simulate_eval_payload(
            name="e",
            output=None,
            reason=None,
            output_type=None,
            config_output="score",
        )
        assert p["output"] is None
        assert p["output_scalar"] is None
        assert p["output_dict"] is None

    def test_multi_choice_dict(self):
        p = build_simulate_eval_payload(
            name="topics",
            output={"score": 0.5, "choices": ["A", "B"]},
            reason="r",
            output_type="choices",
            config_output="choices",
        )
        assert p["output_scalar"] == '["A", "B"]'
        assert p["output_dict"] == {"score": 0.5, "choices": ["A", "B"]}


# ── eval_config_output ───────────────────────────────────────────────────


class TestEvalConfigOutput:
    def test_reads_from_eval_template_via_custom_eval_config(self):
        class _Template:
            config = {"output": "choices"}

        class _CustomEvalConfig:
            eval_template = _Template()

        assert eval_config_output(_CustomEvalConfig()) == "choices"

    def test_reads_directly_from_eval_template(self):
        class _Template:
            config = {"output": "score"}

        assert eval_config_output(_Template()) == "score"

    def test_returns_score_default_when_chain_broken(self):
        assert eval_config_output(None) == "score"


# ── dual_write_eval_value: re-export sanity ──────────────────────────────


def test_dual_write_imported_from_canonical_module_matches_observe_contract():
    kw: dict = {}
    dual_write_eval_value({"score": 0.7, "choice": "X"}, "score", kw)
    assert kw["output_float"] == 0.7
    assert json.loads(kw["output_str"]) == {"score": 0.7, "choice": "X"}
    assert "output_str_list" not in kw


def test_dual_write_choices_dict_routes_to_str_list():
    kw: dict = {}
    dual_write_eval_value({"score": 1.0, "choice": "X"}, "choices", kw)
    assert kw["output_str_list"] == ["X"]
    assert json.loads(kw["output_str"]) == {"score": 1.0, "choice": "X"}


def test_dual_write_choices_multi_dict_routes_to_str_list():
    kw: dict = {}
    dual_write_eval_value(
        {"score": 0.5, "choices": ["A", "B", "A"]}, "choices", kw
    )
    # Dedupe in first-seen order.
    assert kw["output_str_list"] == ["A", "B"]
