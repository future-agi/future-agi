"""Eval-column hydration tests.

Covers the flat-key hydration path that spreads a pivoted ``(trace, config)``
eval score onto a list-grid row, plus the two config/output-type helpers:

* ``flatten_eval_score_into_entry`` — MULTI-config merge onto one row + the
  ``per_choice`` precedence branch. (Single-config cases are already covered by
  ``test_eval_score_rendering.py::TestFlattenEvalScoreIntoEntry`` and
  ``test_trace_list_ch.py::TestEvalAveragingAcrossSpans`` — not duplicated here.)
* ``eval_output_type_for_config`` — reads the template ``output`` type, every
  missing/None branch.
* ``_normalize_eval_output_type`` — tested directly (existing files only exercise
  it indirectly through flatten).

All pure logic (``getattr``/dict manipulation) — no DB, ``@pytest.mark.unit``.
Fake configs are ``SimpleNamespace`` so the file stays import-only.
"""

from types import SimpleNamespace

import pytest

from tracer.utils.helper import (
    _normalize_eval_output_type,
    eval_output_type_for_config,
    flatten_eval_score_into_entry,
)


def _flatten(entry, config_id, scores, output_type):
    """Thin wrapper; ``flatten_eval_score_into_entry`` mutates ``entry`` in place."""
    flatten_eval_score_into_entry(entry, config_id, scores, output_type)
    return entry


@pytest.mark.unit
class TestFlattenMultiConfigMerge:
    """A single list-grid row carries columns for EVERY applied eval config.
    The view calls ``flatten_eval_score_into_entry`` once per config against the
    SAME ``entry`` dict, so each config must write its own key(s) without
    clobbering the others or any pre-existing non-eval columns."""

    def test_score_passfail_choices_merged_onto_one_entry(self):
        # Pre-existing non-eval column must survive the eval merge.
        entry = {"trace_id": "t1", "latency": 12.0}
        _flatten(entry, "score_cfg", {"avg_score": 60.0, "pass_rate": None}, "score")
        # Folds in the PASS_FAIL regression: avg_score=0 must NOT win over pass_rate.
        _flatten(entry, "pf_cfg", {"avg_score": 0.0, "pass_rate": 100.0}, "Pass/Fail")
        _flatten(entry, "ch_cfg", {"per_choice": {"joy": 50.0, "sad": 50.0}}, "choices")

        assert entry == {
            "trace_id": "t1",
            "latency": 12.0,
            "score_cfg": 60.0,
            "pf_cfg": 100.0,
            "ch_cfg**joy": 50.0,
            "ch_cfg**sad": 50.0,
        }

    def test_same_config_id_reused_across_calls_overwrites(self):
        # Idempotent re-hydration of the same column overwrites, never appends.
        entry: dict = {}
        _flatten(entry, "cfg", {"avg_score": 10.0, "pass_rate": None}, "score")
        _flatten(entry, "cfg", {"avg_score": 20.0, "pass_rate": None}, "score")
        assert entry == {"cfg": 20.0}

    def test_multi_config_mixes_terminal_and_error_markers(self):
        entry: dict = {}
        _flatten(entry, "ok_cfg", {"avg_score": 42.0, "pass_rate": None}, "score")
        _flatten(entry, "err_cfg", {"error": True}, "Pass/Fail")
        _flatten(entry, "skip_cfg", {"status": "skipped"}, "score")
        assert entry == {
            "ok_cfg": 42.0,
            "err_cfg": {"error": True},
            "skip_cfg": {"status": "skipped"},
        }

    def test_two_choice_configs_keep_distinct_flat_keys(self):
        # Same choice label under two configs must not collide (config_id prefix).
        entry: dict = {}
        _flatten(entry, "cfgA", {"per_choice": {"yes": 100.0}}, "choices")
        _flatten(entry, "cfgB", {"per_choice": {"yes": 0.0}}, "choices")
        assert entry == {"cfgA**yes": 100.0, "cfgB**yes": 0.0}


@pytest.mark.unit
class TestFlattenPerChoicePrecedence:
    """``per_choice`` is checked BEFORE the output_type branch, so a payload
    carrying ``per_choice`` always spreads into flat keys regardless of the
    declared output_type."""

    def test_per_choice_wins_over_passfail_output_type(self):
        entry: dict = {}
        _flatten(
            entry,
            "cfg",
            {"per_choice": {"a": 30.0, "b": 70.0}, "pass_rate": 99.0},
            "Pass/Fail",
        )
        assert entry == {"cfg**a": 30.0, "cfg**b": 70.0}

    def test_per_choice_wins_over_score_output_type(self):
        entry: dict = {}
        _flatten(entry, "cfg", {"per_choice": {"x": 100.0}, "avg_score": 5.0}, "score")
        assert entry == {"cfg**x": 100.0}

    def test_empty_per_choice_falls_through(self):
        # Falsy per_choice ({}) does not short-circuit; avg_score/pass_rate branch runs.
        entry: dict = {}
        _flatten(entry, "cfg", {"per_choice": {}, "avg_score": 7.0}, "score")
        assert entry == {"cfg": 7.0}


@pytest.mark.unit
class TestEvalOutputTypeForConfig:
    """``eval_output_type_for_config`` reads ``config.eval_template.config['output']``,
    tolerating every missing link in that chain (returns None)."""

    def test_reads_output_from_template_config(self):
        config = SimpleNamespace(
            eval_template=SimpleNamespace(config={"output": "score"})
        )
        assert eval_output_type_for_config(config) == "score"

    def test_reads_passfail_output(self):
        config = SimpleNamespace(
            eval_template=SimpleNamespace(config={"output": "Pass/Fail"})
        )
        assert eval_output_type_for_config(config) == "Pass/Fail"

    def test_none_config_returns_none(self):
        assert eval_output_type_for_config(None) is None

    def test_config_without_eval_template_attr_returns_none(self):
        # Object that simply lacks an ``eval_template`` attribute.
        assert eval_output_type_for_config(SimpleNamespace()) is None

    def test_none_eval_template_returns_none(self):
        assert eval_output_type_for_config(SimpleNamespace(eval_template=None)) is None

    def test_none_template_config_returns_none(self):
        config = SimpleNamespace(eval_template=SimpleNamespace(config=None))
        assert eval_output_type_for_config(config) is None

    def test_template_config_missing_output_key_returns_none(self):
        config = SimpleNamespace(
            eval_template=SimpleNamespace(config={"type": "custom"})
        )
        assert eval_output_type_for_config(config) is None

    def test_template_without_config_attr_returns_none(self):
        # eval_template present but with no ``config`` attribute at all.
        assert eval_output_type_for_config(
            SimpleNamespace(eval_template=SimpleNamespace())
        ) is None


@pytest.mark.unit
class TestNormalizeEvalOutputType:
    """``_normalize_eval_output_type`` upper-cases and swaps ``/`` and space for
    ``_`` so ``Pass/Fail`` / ``pass fail`` / ``PASS_FAIL`` all compare equal."""

    def test_none_normalizes_to_empty(self):
        assert _normalize_eval_output_type(None) == ""

    def test_empty_stays_empty(self):
        assert _normalize_eval_output_type("") == ""

    @pytest.mark.parametrize(
        "raw",
        ["Pass/Fail", "pass/fail", "pass_fail", "PASS_FAIL", "pass fail", "Pass Fail"],
    )
    def test_passfail_variants_all_normalize_equal(self, raw):
        assert _normalize_eval_output_type(raw) == "PASS_FAIL"

    def test_score_normalizes(self):
        assert _normalize_eval_output_type("score") == "SCORE"

    def test_choices_normalizes(self):
        assert _normalize_eval_output_type("choices") == "CHOICES"

    def test_only_passfail_matches_passfail_marker(self):
        # Guards the flatten branch: "score"/"choices" must NOT be read as PASS_FAIL.
        assert _normalize_eval_output_type("score") != "PASS_FAIL"
        assert _normalize_eval_output_type("choices") != "PASS_FAIL"
