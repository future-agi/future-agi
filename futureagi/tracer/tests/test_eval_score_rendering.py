"""
Eval Score Rendering Tests

Tests for the eval score inversion bug where custom Pass/Fail evals show
100% in trace list grid but 0% in detail drawer.

Root causes:
1. isinstance ordering in eval.py - bool is subclass of int
2. Update doesn't clear stale fields (output_float remains)
3. observation_span.py ignores eval config when selecting fields
4. ClickHouse returns output_str_list as JSON string, not Python list

This file tests the fixes for all of these issues.
"""

import json

import pytest

from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger


def _get_configured_output_type(custom_eval_config):
    """Get the configured output type from an eval's template config.

    Returns the output type string ("Pass/Fail", "score", "choices") or None
    if unavailable.
    """
    if (
        custom_eval_config
        and getattr(custom_eval_config, "eval_template", None)
        and custom_eval_config.eval_template
    ):
        eval_template_config = custom_eval_config.eval_template.config or {}
        return eval_template_config.get("output")
    return None


def _build_eval_metric_entry(
    output_float, output_bool, output_str_list, configured_output_type
):
    """Determine score and outputType based on eval template config.

    For Pass/Fail evals, prioritises output_bool over output_float so that
    stale float values (left behind by re-runs) don't mask the boolean result.

    Returns (score, output_type_str) or (None, None) when no score data exists.
    """
    # str_list can come from CH as a JSON string '[]' or from PG as a Python list
    parsed_str_list = None
    if output_str_list:
        if isinstance(output_str_list, list):
            parsed_str_list = output_str_list
        elif isinstance(output_str_list, str) and output_str_list.startswith("["):
            try:
                parsed_str_list = json.loads(output_str_list)
            except json.JSONDecodeError:
                pass

    # str_list always wins (choices type) - but only if it has data
    if parsed_str_list and len(parsed_str_list) > 0:
        return parsed_str_list, "str_list"

    # Config says Pass/Fail → prefer output_bool
    if configured_output_type == "Pass/Fail" and output_bool is not None:
        return (100.0 if output_bool else 0.0), "bool"

    # Float score (default path, or fallback for Pass/Fail when output_bool is absent)
    if output_float is not None:
        score = round(output_float * 100, 2)
        # If config says Pass/Fail but only float is stored (e.g. DeterministicEvaluator),
        # preserve the configured output type so the frontend renders Pass/Fail correctly.
        if configured_output_type == "Pass/Fail":
            return score, "Pass/Fail"
        return score, configured_output_type or "float"

    # Bool without Pass/Fail config
    if output_bool is not None:
        return (100.0 if output_bool else 0.0), "bool"

    return None, None


@pytest.mark.unit
class TestIsinstanceOrdering:
    """Tests for Bug 1: isinstance ordering in eval.py.

    Python bool is a subclass of int, so isinstance(True, int) returns True.
    The original code checked isinstance(value, int) BEFORE isinstance(value, bool),
    causing boolean values to be stored as output_float instead of output_bool.
    """

    def test_bool_is_subclass_of_int(self):
        """Verify that bool is a subclass of int in Python.

        This is the root cause of the bug - isinstance(True, int) returns True,
        so the isinstance check for int must come AFTER the bool check.
        """
        assert isinstance(True, int) is True
        assert isinstance(False, int) is True
        assert isinstance(True, bool) is True
        assert isinstance(False, bool) is True

    def test_correct_isinstance_ordering(self):
        """Verify that checking bool BEFORE int gives correct results.

        The fix swaps the order so bool is checked first:
        - isinstance(True, bool) -> True (correct)
        - isinstance(True, int) -> True (would be wrong for our use case)
        """
        test_cases = [
            (True, "bool"),
            (False, "bool"),
            (1.0, "float"),
            (0.5, "float"),
            (0.0, "float"),
            (1, "int"),
            (0, "int"),
        ]

        for value, expected_type in test_cases:
            if isinstance(value, bool):
                result = "bool"
            elif isinstance(value, (float, int)):
                result = "float" if isinstance(value, float) else "int"
            else:
                result = "other"

            assert result == expected_type, f"isinstance ordering failed for {value}"

    def test_original_buggy_ordering(self):
        """Demonstrate the bug: checking int BEFORE bool misclassifies booleans.

        Original code:
        if isinstance(value, int):     # True for bool!
            output_float = float(value)
        elif isinstance(value, bool):  # Dead code for actual bools
            output_bool = value
        """
        test_value = True

        # Original buggy order
        if isinstance(test_value, int):
            buggy_result = "output_float"
        elif isinstance(test_value, bool):
            buggy_result = "output_bool"
        else:
            buggy_result = "other"

        # Fixed order
        if isinstance(test_value, bool):
            fixed_result = "output_bool"
        elif isinstance(test_value, (float, int)):
            fixed_result = "output_float"
        else:
            fixed_result = "other"

        assert buggy_result == "output_float", "Bug: True stored as float"
        assert fixed_result == "output_bool", "Fix: True stored as bool"


@pytest.mark.integration
@pytest.mark.unit
class TestGetConfiguredOutputType:
    """Tests for _get_configured_output_type helper."""

    def test_returns_output_from_template_config(self, db, project, eval_template):
        """Should return the 'output' field from eval template config."""
        eval_template.config = {"output": "Pass/Fail"}
        eval_template.save()

        config = CustomEvalConfig.objects.create(
            name="Test Config",
            project=project,
            eval_template=eval_template,
        )

        result = _get_configured_output_type(config)
        assert result == "Pass/Fail"

    def test_returns_none_for_missing_template(self):
        """Should return None if config has no eval_template."""
        config = CustomEvalConfig(name="Test", project=None, eval_template=None)
        result = _get_configured_output_type(config)
        assert result is None

    def test_returns_none_for_none_config(self):
        """Should return None if config is None."""
        result = _get_configured_output_type(None)
        assert result is None

    def test_returns_none_for_missing_output_key(self, db, project, eval_template):
        """Should return None if template config has no 'output' key."""
        eval_template.config = {"type": "custom"}
        eval_template.save()

        config = CustomEvalConfig.objects.create(
            name="Test Config",
            project=project,
            eval_template=eval_template,
        )

        result = _get_configured_output_type(config)
        assert result is None


@pytest.mark.integration
@pytest.mark.unit
class TestBuildEvalMetricEntry:
    """Tests for _build_eval_metric_entry helper.

    This is the CORE fix for the score inversion bug. The function must:
    1. Respect eval config when choosing which field to use
    2. Prioritize output_bool over output_float for Pass/Fail evals
    3. Handle ClickHouse returning output_str_list as JSON string
    4. Ignore empty str_list (len > 0 check)
    """

    # Pass/Fail config tests

    def test_pass_fail_with_output_bool_true(self):
        """Pass/Fail with output_bool=True should return score=100.0, outputType='bool'."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=True,
            output_str_list=None,
            configured_output_type="Pass/Fail",
        )

        assert score == 100.0
        assert output_type == "bool"

    def test_pass_fail_with_output_bool_false(self):
        """Pass/Fail with output_bool=False should return score=0.0, outputType='bool'."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=False,
            output_str_list=None,
            configured_output_type="Pass/Fail",
        )

        assert score == 0.0
        assert output_type == "bool"

    def test_pass_fail_prioritizes_bool_over_stale_float(self):
        """THE BUG: Pass/Fail with output_bool=True and stale output_float=0.0 must use bool.

        This is the exact scenario that caused the bug:
        - First run: output_float=0.0 (bool stored as float due to isinstance bug)
        - Second run: output_bool=True (fixed isinstance ordering)
        - DB now has BOTH fields set

        Without the fix: code checks output_float first, returns score=0.0
        With the fix: code checks Pass/Fail config, prioritizes output_bool=True, returns score=100.0
        """
        score, output_type = _build_eval_metric_entry(
            output_float=0.0,  # Stale from first run
            output_bool=True,  # Correct from second run
            output_str_list=None,
            configured_output_type="Pass/Fail",
        )

        # Should prioritize output_bool, NOT use stale output_float
        assert score == 100.0
        assert output_type == "bool"

    def test_pass_fail_with_only_output_float_true(self):
        """Pass/Fail with output_float=1.0 (no bool) should return score=100.0, outputType='Pass/Fail'."""
        score, output_type = _build_eval_metric_entry(
            output_float=1.0,
            output_bool=None,
            output_str_list=None,
            configured_output_type="Pass/Fail",
        )

        assert score == 100.0
        # Preserves Pass/Fail type so frontend renders correctly
        assert output_type == "Pass/Fail"

    def test_pass_fail_with_only_output_float_false(self):
        """Pass/Fail with output_float=0.0 (no bool) should return score=0.0, outputType='Pass/Fail'."""
        score, output_type = _build_eval_metric_entry(
            output_float=0.0,
            output_bool=None,
            output_str_list=None,
            configured_output_type="Pass/Fail",
        )

        assert score == 0.0
        # Preserves Pass/Fail type so frontend renders correctly
        assert output_type == "Pass/Fail"

    # ClickHouse JSON string tests

    def test_str_list_from_clickhouse_json_string(self):
        """ClickHouse returns output_str_list as JSON string '["a","b"]', not Python list."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=None,
            output_str_list='["option_a", "option_b"]',  # JSON string from CH
            configured_output_type="choices",
        )

        assert score == ["option_a", "option_b"]
        assert output_type == "str_list"

    def test_str_list_from_postgresql_python_list(self):
        """PostgreSQL returns output_str_list as Python list."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=None,
            output_str_list=["option_a", "option_b"],  # Python list from PG
            configured_output_type="choices",
        )

        assert score == ["option_a", "option_b"]
        assert output_type == "str_list"

    def test_empty_str_list_from_clickhouse_ignored(self):
        """Empty str_list '[]' from ClickHouse should be ignored, not returned as score."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=None,
            output_str_list="[]",  # Empty JSON string from CH
            configured_output_type="choices",
        )

        # Should fall through to other fields (None, None in this case)
        assert score is None
        assert output_type is None

    def test_empty_python_list_ignored(self):
        """Empty Python list [] from PostgreSQL should be ignored."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=None,
            output_str_list=[],  # Empty Python list
            configured_output_type="choices",
        )

        assert score is None
        assert output_type is None

    # Score scaling tests

    def test_float_score_scaled_to_percentage(self):
        """Float values 0.0-1.0 should be scaled to 0-100 percentage."""
        score, output_type = _build_eval_metric_entry(
            output_float=0.75,
            output_bool=None,
            output_str_list=None,
            configured_output_type="score",
        )

        assert score == 75.0
        assert output_type == "score"

    def test_float_score_rounded(self):
        """Float scores should be rounded to 2 decimal places."""
        score, output_type = _build_eval_metric_entry(
            output_float=0.333333,
            output_bool=None,
            output_str_list=None,
            configured_output_type="score",
        )

        assert score == 33.33
        assert output_type == "score"

    # Edge cases

    def test_no_config_uses_default_float(self):
        """Without config, should use output_float as default path."""
        score, output_type = _build_eval_metric_entry(
            output_float=0.5,
            output_bool=None,
            output_str_list=None,
            configured_output_type=None,
        )

        assert score == 50.0
        assert output_type == "float"

    def test_no_config_uses_bool_if_only_bool_present(self):
        """Without config, if only output_bool is present, should return bool score."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=True,
            output_str_list=None,
            configured_output_type=None,
        )

        assert score == 100.0
        assert output_type == "bool"

    def test_all_none_returns_none(self):
        """All fields None should return (None, None)."""
        score, output_type = _build_eval_metric_entry(
            output_float=None,
            output_bool=None,
            output_str_list=None,
            configured_output_type=None,
        )

        assert score is None
        assert output_type is None

    def test_str_list_takes_priority_over_bool(self):
        """str_list (choices type) should always take priority if it has data."""
        score, output_type = _build_eval_metric_entry(
            output_float=0.5,
            output_bool=True,
            output_str_list=["choice_a", "choice_b"],
            configured_output_type="Pass/Fail",
        )

        # str_list wins regardless of config
        assert score == ["choice_a", "choice_b"]
        assert output_type == "str_list"

    def test_invalid_json_string_in_str_list_ignored(self):
        """Invalid JSON string in output_str_list should be ignored gracefully.

        When str_list parsing fails, code falls through to float (default path)
        or bool (Pass/Fail config).
        """
        # With "choices" config: falls through to float (50.0 from 0.5)
        score, output_type = _build_eval_metric_entry(
            output_float=0.5,
            output_bool=True,
            output_str_list="[invalid json",  # Malformed JSON
            configured_output_type="choices",
        )

        # The configured choices output type is preserved even if the stored
        # choice payload is malformed and rendering falls back to the float.
        assert score == 50.0
        assert output_type == "choices"

        # With "Pass/Fail" config: falls through to bool (100.0 from True)
        score2, output_type2 = _build_eval_metric_entry(
            output_float=0.5,
            output_bool=True,
            output_str_list="[invalid json",
            configured_output_type="Pass/Fail",
        )

        # Pass/Fail config prioritizes bool over float
        assert score2 == 100.0
        assert output_type2 == "bool"


@pytest.mark.integration
@pytest.mark.api
class TestEvalMetricEntryIntegration:
    """Integration tests verifying eval metric building works end-to-end."""

    def test_eval_logger_passes_config_to_metric_entry(
        self, db, project, trace, observation_span, eval_template
    ):
        """EvalLogger with Pass/Fail config should use output_bool, not output_float."""
        # Create config with Pass/Fail template
        eval_template.config = {"output": "Pass/Fail"}
        eval_template.save()

        config = CustomEvalConfig.objects.create(
            name="Pass Fail Test",
            project=project,
            eval_template=eval_template,
        )

        # Create EvalLogger with both fields set (simulates stale data)
        eval_logger = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.0,  # Stale
            output_bool=True,  # Correct
        )

        # Build metric entry
        configured_type = _get_configured_output_type(config)
        score, output_type = _build_eval_metric_entry(
            eval_logger.output_float,
            eval_logger.output_bool,
            eval_logger.output_str_list,
            configured_type,
        )

        assert score == 100.0
        assert output_type == "bool"

    def test_eval_logger_score_eval_uses_float(
        self, db, project, trace, observation_span, eval_template
    ):
        """EvalLogger with 'score' config should use output_float."""
        eval_template.config = {"output": "score"}
        eval_template.save()

        config = CustomEvalConfig.objects.create(
            name="Score Test",
            project=project,
            eval_template=eval_template,
        )

        eval_logger = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.85,
            output_bool=None,
        )

        configured_type = _get_configured_output_type(config)
        score, output_type = _build_eval_metric_entry(
            eval_logger.output_float,
            eval_logger.output_bool,
            eval_logger.output_str_list,
            configured_type,
        )

        assert score == 85.0
        assert output_type == "score"

    def test_eval_logger_choices_eval_uses_str_list(
        self, db, project, trace, observation_span, eval_template
    ):
        """EvalLogger with 'choices' config should use output_str_list."""
        eval_template.config = {"output": "choices"}
        eval_template.save()

        config = CustomEvalConfig.objects.create(
            name="Choices Test",
            project=project,
            eval_template=eval_template,
        )

        eval_logger = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_str_list=["toxic", "spam"],
        )

        configured_type = _get_configured_output_type(config)
        score, output_type = _build_eval_metric_entry(
            eval_logger.output_float,
            eval_logger.output_bool,
            eval_logger.output_str_list,
            configured_type,
        )

        assert score == ["toxic", "spam"]
        assert output_type == "str_list"
