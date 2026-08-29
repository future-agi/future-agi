"""Unit tests for ``_calculate_judge_human_agreement`` and its helpers.

Coverage:
  - ``_normalize_eval_output``: pass_fail / percentage / deterministic.
  - ``_majority_value``: strict majority, ties, single value, order-independence.
  - ``_normalize_human_score_value``: the three real shapes of a stored
    ``Score.value`` — per-type dict, legacy bare scalar, and None/empty/garbage
    (degrades to "not comparable" rather than crashing or false-agreeing).
  - ``_calculate_judge_human_agreement``:
      * returns None when no evaluator is linked / no span-sourced items;
      * returns null agreement when judge and human scores don't overlap;
      * computes per-label and overall agreement correctly;
      * the latest-non-error eval Subquery is built with the right gates
        (error=False, status=COMPLETED, skipped_reason__isnull, deleted=False).

Note: the *execution* of the Subquery (real error-filtering and
``-created_at`` ordering) is covered by the integration tests, which run
against a real DB. These unit tests only assert the query *signature*, so a
regression that drops a gate fails fast instead of silently passing.
"""

import unittest
from unittest.mock import MagicMock, patch

from django.db.models import Subquery

from model_hub.utils.annotation_queue_helpers import (
    _calculate_judge_human_agreement,
    _majority_value,
    _normalize_eval_output,
    _normalize_human_score_value,
)
from tracer.models.observation_span import EvalEntryStatus


class TestNormalizeEvalOutput(unittest.TestCase):
    def test_pass_fail_true(self):
        assert _normalize_eval_output({"output_bool": True}, "pass_fail") == "pass"

    def test_pass_fail_false(self):
        assert _normalize_eval_output({"output_bool": False}, "pass_fail") == "fail"

    def test_percentage_valid(self):
        assert _normalize_eval_output({"output_float": 0.87654}, "percentage") == "0.88"

    def test_percentage_none(self):
        assert _normalize_eval_output({"output_float": None}, "percentage") is None

    def test_deterministic_uses_str(self):
        assert (
            _normalize_eval_output({"output_str": "toxic"}, "deterministic") == "toxic"
        )

    def test_deterministic_fallbacks_to_str_list(self):
        assert (
            _normalize_eval_output(
                {"output_str": None, "output_str_list": ["A"]},
                "deterministic",
            )
            == "['A']"
        )

    def test_deterministic_str_list_is_sorted(self):
        """str_list items are sorted before str() conversion so that
        order differences don't cause false disagreements."""
        assert (
            _normalize_eval_output(
                {"output_str": None, "output_str_list": ["B", "A", "C"]},
                "deterministic",
            )
            == "['A', 'B', 'C']"
        )

    def test_pass_fail_none_bool_returns_none(self):
        assert _normalize_eval_output({"output_bool": None}, "pass_fail") is None

    def test_output_type_none_returns_none(self):
        """When output_type is None the function bails early (returns None),
        not the deterministic codepath — this mirrors the integration test
        ``test_queue_with_null_output_type_returns_none`` which relies on the
        caller short-circuiting to None for a linked evaluator whose
        ``output_type_normalized`` is unset."""
        assert _normalize_eval_output({"output_str": "abc"}, None) is None


class TestMajorityValue(unittest.TestCase):
    def test_returns_most_common(self):
        assert _majority_value(["a", "b", "a"]) == "a"

    def test_returns_none_for_empty(self):
        assert _majority_value([]) is None

    def test_handles_single_value(self):
        assert _majority_value(["only"]) == "only"

    def test_returns_none_on_tie(self):
        # Two annotators disagree — no true majority.
        assert _majority_value(["a", "b"]) is None

    def test_returns_none_on_three_way_tie(self):
        assert _majority_value(["a", "b", "c"]) is None

    def test_strict_majority_wins(self):
        # 2 "a" vs 1 "b" → "a" has a strict majority.
        assert _majority_value(["a", "b", "a"]) == "a"

    def test_equivalent_lists_are_not_ties(self):
        """Lists with the same items in a different order are normalised
        identically by _normalize_value and must not be reported as a tie."""
        assert _majority_value([["A", "B"], ["B", "A"], ["A", "B"]]) == ["A", "B"]

    def test_equivalent_dicts_are_not_ties(self):
        """Dicts whose list-valued entries differ in order are normalised
        identically by _normalize_value and must not be reported as a tie."""
        assert _majority_value(
            [
                {"sel": ["A", "B"]},
                {"sel": ["B", "A"]},
            ]
        ) == {"sel": ["A", "B"]}


class TestNormalizeHumanScoreValue(unittest.TestCase):
    """``_normalize_human_score_value`` must mirror the judge side so that
    a stored ``Score.value`` compares equal to an ``_normalize_eval_output``
    string. It also has to be defensive: the value is user/legacy data and
    must never raise.

    The first four tests cover the *comparable* shapes (per-type dict and
    legacy bare scalar); the remaining tests cover every branch that must
    degrade to "not comparable" (None) rather than crash or false-agree.
    """

    def test_categorical_dict_unwraps_single_choice(self):
        assert (
            _normalize_human_score_value(
                {"selected": ["pass"]}, "categorical", "pass_fail"
            )
            == "pass"
        )

    def test_categorical_dict_multi_choice_sorts(self):
        # Multiple selections stay order-independent via the sorted-list form,
        # matching the judge side for deterministic output.
        assert (
            _normalize_human_score_value(
                {"selected": ["B", "A"]}, "categorical", "deterministic"
            )
            == "['A', 'B']"
        )

    def test_numeric_percentage_rounds_to_two_decimals(self):
        # Mirrors _normalize_eval_output's round(2) so the sides match.
        assert (
            _normalize_human_score_value({"value": 0.876}, "numeric", "percentage")
            == "0.88"
        )

    def test_star_percentage_rounds(self):
        assert (
            _normalize_human_score_value({"rating": 4.0}, "star", "percentage") == "4.0"
        )

    def test_legacy_bare_scalar_passes_through(self):
        # Old rows that stored a bare string stay comparable (backward compat).
        assert (
            _normalize_human_score_value("pass", "categorical", "pass_fail") == "pass"
        )

    def test_none_value_is_not_comparable(self):
        assert _normalize_human_score_value(None, "categorical", "pass_fail") is None

    def test_dict_missing_type_key_is_not_comparable(self):
        # A categorical dict without "selected" cannot be unwrapped.
        assert (
            _normalize_human_score_value({"other": "x"}, "categorical", "pass_fail")
            is None
        )

    def test_empty_selected_list_is_not_comparable(self):
        # A categorical dict with an empty "selected" means no choice was
        # made; it cannot be unwrapped into a comparable scalar.
        assert (
            _normalize_human_score_value({"selected": []}, "categorical", "pass_fail")
            is None
        )

    def test_unknown_label_type_is_not_comparable(self):
        # No mapping entry for the type → can't unwrap.
        assert (
            _normalize_human_score_value({"value": 5}, "unknown_type", "percentage")
            is None
        )

    def test_non_numeric_percentage_is_not_comparable(self):
        # A percentage judge needs a float; a string can't round.
        assert (
            _normalize_human_score_value({"value": "abc"}, "numeric", "percentage")
            is None
        )


class TestCalculateJudgeHumanAgreement(unittest.TestCase):
    """The function is called with a mocked ``AnnotationQueue``. Database
    queries are mocked so the tests stay fast and deterministic. Note: because
    the Subquery is not executed under a MagicMock, tests that care about the
    *latest non-error eval row* assert on the query signature rather than on a
    result that the mock would fabricate (and that would false-pass)."""

    def test_returns_none_when_no_evaluator_linked(self):
        queue = MagicMock()
        queue.custom_eval_config_id = None
        queue.status = "completed"
        assert _calculate_judge_human_agreement(queue) is None

    def test_returns_none_when_queue_not_completed(self):
        """A linked evaluator on a non-COMPLETED queue must not produce a
        judge-vs-human stat — agreement is only meaningful once annotation is
        finished. The test sets status to ``active`` to exercise the gate."""
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = "pass_fail"
        queue.status = "active"
        assert _calculate_judge_human_agreement(queue) is None

    @patch("tracer.models.observation_span.EvalLogger.objects")
    @patch("model_hub.models.score.Score.objects")
    def test_returns_none_when_no_span_sourced_items(
        self, mock_score_objects, mock_eval_objects
    ):
        queue = MagicMock()
        queue.status = "completed"
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = "pass_fail"
        queue.custom_eval_config.name = "Safety Eval"

        # No observation_span-sourced items in the queue.
        queue.items.filter.return_value.values_list.return_value = []

        assert _calculate_judge_human_agreement(queue) is None

    @patch("tracer.models.observation_span.EvalLogger.objects")
    @patch("model_hub.models.score.Score.objects")
    def test_evaluator_name_falls_back_to_config_id(
        self, mock_score_objects, mock_eval_objects
    ):
        """When custom_eval_config.name (None) and eval_template.name ("")
        are both empty, evaluator_name falls back to the config PK so the UI
        never renders a blank label. This single test covers both the None
        and "" cases (both are falsy in the ``name or template.name or id``
        chain)."""
        queue = MagicMock()
        queue.status = "completed"
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = "pass_fail"
        queue.custom_eval_config.name = None
        queue.custom_eval_config.eval_template.name = ""

        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
        ]
        mock_eval_objects.filter.return_value.values.return_value = []
        mock_score_objects.filter.return_value.values.return_value = []

        result = _calculate_judge_human_agreement(queue)
        assert result["evaluator_name"] == "eval-config-1"

    @patch("tracer.models.observation_span.EvalLogger.objects")
    @patch("model_hub.models.score.Score.objects")
    def test_latest_eval_subquery_gates_on_error_and_status(
        self, mock_score_objects, mock_eval_objects
    ):
        """The inner Subquery that selects the latest eval row per source must
        be built with the right gates (error=False, status=COMPLETED,
        skipped_reason__isnull=True, deleted=False, matching config id). Under
        a MagicMock the Subquery is never executed, so we assert on the *call
        signature*: dropping any gate from ``_latest_eval`` changes these
        kwargs and fails the assertion. The actual filtering/ordering is
        covered by the integration tests against a real DB."""
        queue = MagicMock()
        queue.status = "completed"
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = "pass_fail"
        queue.custom_eval_config.name = "Safety Eval"

        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
        ]
        mock_eval_objects.filter.return_value.values.return_value = []
        mock_score_objects.filter.return_value.values.return_value = []

        _calculate_judge_human_agreement(queue)

        # The inner latest-eval filter carries the gates; the outer filter only
        # carries id=Subquery(...), observation_span_id__in, deleted=False.
        inner = [
            c.kwargs
            for c in mock_eval_objects.filter.call_args_list
            if c.kwargs.get("error") is False
        ]
        assert inner, "expected an inner latest-eval filter gated on error=False"
        gate = inner[0]
        assert gate.get("error") is False
        assert gate.get("status") == EvalEntryStatus.COMPLETED
        assert gate.get("skipped_reason__isnull") is True
        assert gate.get("deleted") is False
        assert gate.get("custom_eval_config_id") == "eval-config-1"

        # The outer filter must wrap the inner one in a Subquery on id.
        outer = [
            c
            for c in mock_eval_objects.filter.call_args_list
            if any(isinstance(v, Subquery) for v in c.kwargs.values())
        ]
        assert outer, "expected the outer eval-row filter to use a Subquery on id"

    @patch("tracer.models.observation_span.EvalLogger.objects")
    @patch("model_hub.models.score.Score.objects")
    def test_calculates_agreement_correctly(
        self, mock_score_objects, mock_eval_objects
    ):
        """Two items, two labels. Judge agrees on label-1, disagrees on
        label-2. Overall = 2/4 = 0.5."""
        queue = MagicMock()
        queue.status = "completed"
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = "pass_fail"
        queue.custom_eval_config.name = "Safety Eval"

        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
            ("item-2", "span-2"),
        ]

        # Judge: pass on span-1, fail on span-2.
        mock_eval_objects.filter.return_value.values.return_value = [
            {
                "observation_span_id": "span-1",
                "output_bool": True,
                "output_float": None,
                "output_str": None,
                "output_str_list": [],
            },
            {
                "observation_span_id": "span-2",
                "output_bool": False,
                "output_float": None,
                "output_str": None,
                "output_str_list": [],
            },
        ]

        # Human scores: each item has two labels, one annotator per label.
        # label-1: item-1 says "pass" (agree), item-2 says "fail" (agree)
        # label-2: item-1 says "fail" (judge:pass → disagree),
        #          item-2 says "pass" (judge:fail → disagree)
        mock_score_objects.filter.return_value.values.return_value = [
            {
                "queue_item_id": "item-1",
                "label_id": "label-1",
                "label__name": "Label A",
                "label__type": "categorical",
                "value": {"selected": ["pass"]},
            },
            {
                "queue_item_id": "item-1",
                "label_id": "label-2",
                "label__name": "Label B",
                "label__type": "categorical",
                "value": {"selected": ["fail"]},
            },
            {
                "queue_item_id": "item-2",
                "label_id": "label-1",
                "label__name": "Label A",
                "label__type": "categorical",
                "value": {"selected": ["fail"]},
            },
            {
                "queue_item_id": "item-2",
                "label_id": "label-2",
                "label__name": "Label B",
                "label__type": "categorical",
                "value": {"selected": ["pass"]},
            },
        ]

        result = _calculate_judge_human_agreement(queue)

        assert result["labels"]["label-1"]["judge_human_agreement"] == 1.0
        assert result["labels"]["label-1"]["total_comparisons"] == 2
        assert result["labels"]["label-2"]["judge_human_agreement"] == 0.0
        assert result["labels"]["label-2"]["total_comparisons"] == 2
        assert result["overall_agreement"] == 0.5
        assert result["total_comparisons"] == 4

    @patch("tracer.models.observation_span.EvalLogger.objects")
    @patch("model_hub.models.score.Score.objects")
    def test_handles_no_overlapping_scores(self, mock_score_objects, mock_eval_objects):
        """When span evals exist but no human scores overlap, return empty
        labels and null overall agreement."""
        queue = MagicMock()
        queue.status = "completed"
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = "percentage"
        queue.custom_eval_config.name = "Score Eval"

        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
        ]

        mock_eval_objects.filter.return_value.values.return_value = [
            {
                "observation_span_id": "span-1",
                "output_bool": None,
                "output_float": 0.95,
                "output_str": None,
                "output_str_list": [],
            },
        ]

        # No human scores.
        mock_score_objects.filter.return_value.values.return_value = []

        result = _calculate_judge_human_agreement(queue)

        assert result["overall_agreement"] is None
        assert result["total_comparisons"] == 0
        assert result["labels"] == {}


class TestPercentageEndToEnd(unittest.TestCase):
    """A percentage judge must agree with a human numeric dict value after
    both sides round to two decimals. This exercises the full agreement path
    (not just the normalize helper) for the percentage output type."""

    @patch("tracer.models.observation_span.EvalLogger.objects")
    @patch("model_hub.models.score.Score.objects")
    def test_percentage_judge_agrees_with_numeric_dict(
        self, mock_score_objects, mock_eval_objects
    ):
        queue = MagicMock()
        queue.status = "completed"
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = "percentage"
        queue.custom_eval_config.name = "Score Eval"

        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
        ]

        # Judge: 0.876 → "0.88"
        mock_eval_objects.filter.return_value.values.return_value = [
            {
                "observation_span_id": "span-1",
                "output_bool": None,
                "output_float": 0.876,
                "output_str": None,
                "output_str_list": [],
            },
        ]

        # Human: numeric dict {"value": 0.876} → unwraps + rounds → "0.88"
        mock_score_objects.filter.return_value.values.return_value = [
            {
                "queue_item_id": "item-1",
                "label_id": "label-1",
                "label__name": "Label A",
                "label__type": "numeric",
                "value": {"value": 0.876},
            },
        ]

        result = _calculate_judge_human_agreement(queue)
        assert result["labels"]["label-1"]["judge_human_agreement"] == 1.0
        assert result["overall_agreement"] == 1.0
        assert result["total_comparisons"] == 1


if __name__ == "__main__":
    import unittest

    unittest.main()
