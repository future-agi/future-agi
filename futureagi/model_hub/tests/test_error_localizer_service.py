"""Direct unit tests for the ``should_run_error_localizer`` gate.

These pin the **behavioural** contract Nikhil's review flagged:
- A bare list / non-numeric string under a ``None`` / ``percentage`` template
  must NOT trigger error-localization (previously collapsed to score 0.0
  and fired EL on a passing eval, burning a paid Gemini call).
- A choices eval with an empty ``choice_scores`` map must NOT trigger EL
  for an unknown-string output, for the same reason.
- The numeric-scoring paths still behave correctly: low score → run EL,
  high score → skip EL.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from model_hub.services.error_localizer_service import should_run_error_localizer
from model_hub.utils.scoring import is_numerically_scorable


def _template(**overrides):
    """Minimal duck-typed EvalTemplate stub. Pure unit, no ORM."""
    defaults = {
        "eval_type": "llm",
        "template_type": "single",
        "output_type_normalized": None,
        "choice_scores": {},
        "pass_threshold": 0.5,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# =========================================================================
# Fail-closed guard — the exact regression Nikhil flagged
# =========================================================================


class TestFailClosedForUnscorableValues:
    """Bare list/string under None/percentage template ⇒ skip EL.

    Mirrors the path in eval_runner.py:1976 ``else: value = choice_result``
    where a choices eval with empty ``choice_scores`` emits a bare string
    or list. Under output_type=None (legacy/system templates) we have no
    way to score it; treating it as failed and running EL on a passing
    eval is the bug the gate now closes.
    """

    @pytest.mark.parametrize(
        "output_type", [None, "percentage", "unknown"],
    )
    def test_bare_list_does_not_trigger(self, output_type):
        template = _template(output_type_normalized=output_type)
        run, reason = should_run_error_localizer(["Good"], template)
        assert run is False
        assert "not eligible for localization" in reason

    @pytest.mark.parametrize(
        "output_type", [None, "percentage", "unknown"],
    )
    def test_non_numeric_string_does_not_trigger(self, output_type):
        template = _template(output_type_normalized=output_type)
        run, reason = should_run_error_localizer("Resolved", template)
        assert run is False
        assert "not eligible for localization" in reason

    def test_bare_dict_value_does_not_trigger(self):
        # extract_eval_value finds nothing useful → returns the dict as-is
        # → dict isn't numerically scorable → fail closed.
        template = _template(output_type_normalized="percentage")
        run, reason = should_run_error_localizer({"some": "shape"}, template)
        assert run is False
        assert "not eligible for localization" in reason

    def test_deterministic_eval_with_empty_choice_scores_skips(self):
        # Choices eval where choice_scores wasn't populated → can't rank
        # the output → fail closed instead of treating as 0.0.
        template = _template(
            output_type_normalized="deterministic", choice_scores={}
        )
        run, reason = should_run_error_localizer("Yes", template)
        assert run is False
        assert "not eligible for localization" in reason

    def test_none_value_does_not_trigger(self):
        template = _template(output_type_normalized="percentage")
        run, reason = should_run_error_localizer(None, template)
        assert run is False


# =========================================================================
# Numeric-scoring paths still work
# =========================================================================


class TestScoringPathsStillBehave:
    def test_low_percentage_score_triggers(self):
        template = _template(
            output_type_normalized="percentage", pass_threshold=0.5
        )
        run, reason = should_run_error_localizer(0.2, template)
        assert run is True
        assert "evaluation failed" in reason.lower()

    def test_high_percentage_score_skips(self):
        template = _template(
            output_type_normalized="percentage", pass_threshold=0.5
        )
        run, reason = should_run_error_localizer(0.9, template)
        assert run is False
        assert "evaluation passed" in reason.lower()

    def test_pass_fail_string_passed_skips(self):
        template = _template(output_type_normalized="pass_fail")
        run, _ = should_run_error_localizer("Passed", template)
        assert run is False

    def test_pass_fail_string_failed_triggers(self):
        template = _template(output_type_normalized="pass_fail")
        run, _ = should_run_error_localizer("Failed", template)
        assert run is True

    def test_deterministic_known_choice_uses_score(self):
        template = _template(
            output_type_normalized="deterministic",
            choice_scores={"Yes": 1.0, "No": 0.0},
        )
        run_yes, _ = should_run_error_localizer("Yes", template)
        run_no, _ = should_run_error_localizer("No", template)
        assert run_yes is False
        assert run_no is True


# =========================================================================
# Up-front shortcircuits
# =========================================================================


class TestEarlyExits:
    def test_no_template_skips(self):
        run, reason = should_run_error_localizer(0.2, None)
        assert run is False
        assert "no eval template" in reason.lower()

    def test_code_eval_skips(self):
        template = _template(eval_type="code")
        run, reason = should_run_error_localizer(0.2, template)
        assert run is False
        assert "code-type" in reason

    def test_composite_eval_skips(self):
        template = _template(template_type="composite")
        run, reason = should_run_error_localizer(0.2, template)
        assert run is False
        assert "composite" in reason


# =========================================================================
# Runtime threshold override — TH-6469
# =========================================================================


class TestRuntimeThresholdOverride:
    def test_runtime_threshold_takes_precedence_over_template(self):
        # Template default 0.5; user override 0.8; score 0.6 → EL must fire.
        template = _template(
            output_type_normalized="percentage", pass_threshold=0.5
        )
        run, reason = should_run_error_localizer(
            0.6, template, runtime_threshold=0.8
        )
        assert run is True
        assert "0.80" in reason

    def test_runtime_threshold_flips_high_score_to_pass(self):
        # Template default 0.5 would fire on 0.4; runtime 0.3 must skip.
        template = _template(
            output_type_normalized="percentage", pass_threshold=0.5
        )
        run, _ = should_run_error_localizer(0.4, template, runtime_threshold=0.3)
        assert run is False

    def test_runtime_threshold_none_falls_back_to_template(self):
        template = _template(
            output_type_normalized="percentage", pass_threshold=0.5
        )
        run, _ = should_run_error_localizer(0.2, template, runtime_threshold=None)
        assert run is True


# =========================================================================
# The scorability helper, directly
# =========================================================================


class TestIsNumericallyScorable:
    @pytest.mark.parametrize("value", [0.5, 1, 0, True, False, "0.5", "1"])
    def test_percentage_accepts_numeric_shapes(self, value):
        assert is_numerically_scorable(value, "percentage", {}) is True

    @pytest.mark.parametrize("value", [["x"], {"a": 1}, "not_a_number", None])
    def test_percentage_rejects_non_numeric_shapes(self, value):
        assert is_numerically_scorable(value, "percentage", {}) is False

    def test_pass_fail_accepts_known_keywords(self):
        assert is_numerically_scorable("passed", "pass_fail", {}) is True
        assert is_numerically_scorable("Fail", "pass_fail", {}) is True
        assert is_numerically_scorable(True, "pass_fail", {}) is True

    def test_pass_fail_rejects_unknown_string(self):
        assert is_numerically_scorable("Resolved", "pass_fail", {}) is False

    def test_deterministic_needs_choice_scores(self):
        assert is_numerically_scorable("Yes", "deterministic", {}) is False
        assert (
            is_numerically_scorable(
                "Yes", "deterministic", {"Yes": 1.0, "No": 0.0}
            )
            is True
        )

    def test_deterministic_list_at_least_one_known_member(self):
        # Matches normalize_score: skip unknown labels, average the known ones.
        # Preflight should stay symmetric with the scorer.
        scores = {"A": 1.0, "B": 0.0}
        assert is_numerically_scorable(["A"], "deterministic", scores) is True
        assert is_numerically_scorable(["A", "B"], "deterministic", scores) is True
        assert is_numerically_scorable(["A", "C"], "deterministic", scores) is True
        assert is_numerically_scorable(["C", "D"], "deterministic", scores) is False

    def test_deterministic_string_case_insensitive(self):
        # Matches apply_choice_scores.
        scores = {"Yes": 1.0, "No": 0.0}
        assert is_numerically_scorable("yes", "deterministic", scores) is True
        assert is_numerically_scorable("YES", "deterministic", scores) is True
        assert is_numerically_scorable(" Yes ", "deterministic", scores) is True
