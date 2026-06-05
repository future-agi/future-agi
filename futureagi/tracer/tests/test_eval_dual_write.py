"""
Unit tests for :func:`tracer.utils.eval._dual_write_eval_value`.

The helper is the only place in ``tracer/utils/eval.py`` allowed to assign
``output_float`` / ``output_str_list`` on ``logger_kwargs``; every assertion
here is keyed on the stored ``config_output`` so the gating contract is
exercised directly.
"""

import json
import re
from pathlib import Path

import pytest

# Break the import cycle (tracer.utils.eval_tasks -> tracer.utils.eval ->
# model_hub.tasks.__init__ -> tracer.utils.eval_tasks) by importing the
# package first. See test_eval_logger_schema.py for the canonical comment.
import model_hub.tasks  # noqa: F401
from tracer.utils.eval import _dual_write_eval_value  # noqa: E402

EVAL_PY = (Path(__file__).resolve().parent.parent / "utils" / "eval.py").read_text()


# ── score output_type ────────────────────────────────────────────────────


def test_score_dict_routes_to_output_float_and_json_output_str():
    kw = {}
    _dual_write_eval_value({"score": 0.7, "choice": "Choice 1"}, "score", kw)
    assert json.loads(kw["output_str"]) == {"score": 0.7, "choice": "Choice 1"}
    assert kw["output_float"] == 0.7
    assert "output_str_list" not in kw


def test_score_plain_float_routes_to_output_float():
    kw = {}
    _dual_write_eval_value(0.7, "score", kw)
    assert kw["output_float"] == 0.7
    assert "output_str_list" not in kw
    assert "output_str" not in kw


def test_score_zero_is_a_real_value():
    """``score == 0`` must populate output_float (truthiness pitfall)."""
    kw = {}
    _dual_write_eval_value(0, "score", kw)
    assert kw["output_float"] == 0.0


def test_score_dict_with_choice_does_not_set_output_str_list():
    """Score-output evals never write output_str_list even if dict has 'choice'."""
    kw = {}
    _dual_write_eval_value({"score": 0.4, "choice": "Bad"}, "score", kw)
    assert "output_str_list" not in kw


def test_score_list_averages_into_output_float():
    """A list arriving on the score path is collapsed to its mean — score evals
    always render a single scalar from output_float, never a list."""
    kw = {}
    _dual_write_eval_value([0.4, 0.6, 0.8], "score", kw)
    assert kw["output_float"] == pytest.approx(0.6)
    assert "output_str_list" not in kw
    assert json.loads(kw["output_str"]) == [0.4, 0.6, 0.8]


def test_score_list_with_non_numeric_elements_is_filtered_before_averaging():
    kw = {}
    _dual_write_eval_value([0.2, "skip", 0.8, None], "score", kw)
    assert kw["output_float"] == pytest.approx(0.5)
    assert "output_str_list" not in kw


def test_score_empty_list_does_not_set_output_float():
    kw = {}
    _dual_write_eval_value([], "score", kw)
    assert "output_float" not in kw
    assert "output_str_list" not in kw
    assert json.loads(kw["output_str"]) == []


def test_score_list_of_bools_does_not_count_them_as_numeric():
    """``bool`` is a subclass of int; exclude bools from the mean so we don't
    accidentally average them to 0.5."""
    kw = {}
    _dual_write_eval_value([True, False], "score", kw)
    assert "output_float" not in kw


def test_score_list_of_dicts_extracts_score_field_and_averages():
    """Choices-promoted score evals can yield per-item dicts; pull each item's
    'score' and average those."""
    kw = {}
    _dual_write_eval_value(
        [
            {"score": 0.4, "choice": "A"},
            {"score": 0.6, "choice": "B"},
            {"score": 0.8, "choice": "C"},
        ],
        "score",
        kw,
    )
    assert kw["output_float"] == pytest.approx(0.6)
    assert "output_str_list" not in kw
    assert json.loads(kw["output_str"]) == [
        {"score": 0.4, "choice": "A"},
        {"score": 0.6, "choice": "B"},
        {"score": 0.8, "choice": "C"},
    ]


def test_score_list_mixes_numbers_and_dicts():
    kw = {}
    _dual_write_eval_value([0.4, {"score": 0.8}], "score", kw)
    assert kw["output_float"] == pytest.approx(0.6)


def test_score_list_of_dicts_skips_items_with_missing_or_non_numeric_score():
    kw = {}
    _dual_write_eval_value(
        [{"score": 0.5}, {"choice": "no-score-here"}, {"score": "x"}],
        "score",
        kw,
    )
    assert kw["output_float"] == pytest.approx(0.5)


# ── choices output_type ──────────────────────────────────────────────────


def test_choices_dict_single_choice_routes_to_list_not_float():
    kw = {}
    _dual_write_eval_value({"score": 0.7, "choice": "Choice 1"}, "choices", kw)
    assert json.loads(kw["output_str"]) == {"score": 0.7, "choice": "Choice 1"}
    assert kw["output_str_list"] == ["Choice 1"]
    assert "output_float" not in kw


def test_choices_dict_multi_choice_routes_to_list():
    kw = {}
    _dual_write_eval_value({"score": 1.0, "choices": ["A", "B"]}, "choices", kw)
    assert kw["output_str_list"] == ["A", "B"]
    assert "output_float" not in kw


def test_choices_plain_string_dual_writes_to_str_and_list():
    """Prebuilt tune/choices eval: format_eval_value returns a raw choice str."""
    kw = {}
    _dual_write_eval_value("Choice 1", "choices", kw)
    assert kw["output_str"] == "Choice 1"
    assert kw["output_str_list"] == ["Choice 1"]


def test_choices_plain_list_routes_to_list():
    kw = {}
    _dual_write_eval_value(["A", "B"], "choices", kw)
    assert kw["output_str_list"] == ["A", "B"]
    assert "output_str" not in kw  # plain string lists don't touch output_str


def test_choices_plain_list_dedupes_repeated_strings():
    kw = {}
    _dual_write_eval_value(["A", "B", "A", "C", "B"], "choices", kw)
    assert kw["output_str_list"] == ["A", "B", "C"]


def test_choices_dict_multi_choice_dedupes_repeated_values():
    kw = {}
    _dual_write_eval_value({"choices": ["A", "B", "A"]}, "choices", kw)
    assert kw["output_str_list"] == ["A", "B"]


def test_choices_list_of_dicts_with_choice_flattens_and_dedupes():
    kw = {}
    _dual_write_eval_value(
        [{"choice": "A"}, {"choice": "B"}, {"choice": "A"}],
        "choices",
        kw,
    )
    assert kw["output_str_list"] == ["A", "B"]
    # Raw payloads kept in output_str for inspection.
    assert json.loads(kw["output_str"]) == [
        {"choice": "A"},
        {"choice": "B"},
        {"choice": "A"},
    ]


def test_choices_list_of_dicts_with_choices_field_flattens_and_dedupes():
    kw = {}
    _dual_write_eval_value(
        [{"choices": ["A", "B"]}, {"choices": ["B", "C"]}],
        "choices",
        kw,
    )
    assert kw["output_str_list"] == ["A", "B", "C"]


def test_choices_list_mixes_strings_and_dicts():
    kw = {}
    _dual_write_eval_value(
        ["A", {"choice": "B"}, {"choices": ["A", "C"]}, "C"],
        "choices",
        kw,
    )
    assert kw["output_str_list"] == ["A", "B", "C"]
    # Has at least one dict element → raw list also dumped to output_str.
    assert "output_str" in kw


# ── Pass/Fail, reason, numeric, other output_types ───────────────────────


def test_passfail_passed_routes_to_output_bool():
    kw = {}
    _dual_write_eval_value("Passed", "Pass/Fail", kw)
    assert kw["output_bool"] is True
    assert "output_float" not in kw
    assert "output_str_list" not in kw


def test_passfail_failed_routes_to_output_bool():
    kw = {}
    _dual_write_eval_value("Failed", "Pass/Fail", kw)
    assert kw["output_bool"] is False


def test_reason_plain_string_does_not_set_output_str_list():
    kw = {}
    _dual_write_eval_value("Why this scored low", "reason", kw)
    assert kw["output_str"] == "Why this scored low"
    assert "output_str_list" not in kw
    assert "output_float" not in kw


def test_numeric_plain_float_preserves_today_behavior():
    """``numeric`` is NOT in the dual-write gate; preserve today's dispatch."""
    kw = {}
    _dual_write_eval_value(5.0, "numeric", kw)
    assert kw["output_float"] == 5.0
    assert "output_str_list" not in kw


# ── Bool-vs-int ordering ─────────────────────────────────────────────────


def test_bool_takes_precedence_over_int_for_any_output_type():
    """bool is a subclass of int; output_bool wins regardless of config."""
    for cfg in ("score", "choices", "Pass/Fail", "reason", "numeric"):
        kw = {}
        _dual_write_eval_value(True, cfg, kw)
        assert kw["output_bool"] is True, cfg
        assert "output_float" not in kw, cfg


# ── Source-level regression guards ───────────────────────────────────────


def test_helper_is_called_from_exactly_seven_dispatch_sites():
    """1 alias import + 7 call sites = 8 occurrences."""
    calls = re.findall(r"_dual_write_eval_value\b", EVAL_PY)
    assert (
        len(calls) == 8
    ), f"Expected 1 import + 7 calls of _dual_write_eval_value, found {len(calls)}"


def test_helper_imported_from_canonical_module():
    """Pin the import path so the alias can't be re-defined locally."""
    assert (
        "from evaluations.engine.normalize import" in EVAL_PY
    ), "tracer/utils/eval.py must import dual_write_eval_value from evaluations.engine.normalize"
    assert (
        "dual_write_eval_value as _dual_write_eval_value" in EVAL_PY
    ), "the alias must be set up at import time"


def test_typed_columns_only_assigned_inside_helper():
    """``output_float`` / ``output_str_list`` must never be assigned outside
    the dual-write helper — that's the only place gating rules are enforced."""
    typed_assignments = re.findall(
        r'logger_kwargs\["output_(?:float|str_list)"\]\s*=', EVAL_PY
    )
    assert not typed_assignments, (
        f"Inline assignments outside helper found: {typed_assignments}. "
        "All output_float / output_str_list writes must go through "
        "_dual_write_eval_value to honour the score/choices gating."
    )
