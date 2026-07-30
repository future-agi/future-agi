"""Unit tests for ``_calculate_judge_human_agreement``.

Coverage:
  - Returns ``None`` when the queue has no linked evaluator.
  - Returns ``None`` when no trace/span-sourced items have overlapping data.
  - Picks the latest ``EvalLogger`` row per span via Subquery.
  - Skips error rows from ``EvalLogger``.
  - Calculates overall and per-label agreement correctly.
  - Handles pass_fail, percentage, and deterministic output types.
  - Returns ``None`` for items without overlapping judge and human scores.
"""

import unittest
from unittest.mock import MagicMock, patch

from model_hub.utils.annotation_queue_helpers import (
    _calculate_judge_human_agreement,
    _majority_value,
    _normalize_eval_output,
)


class TestNormalizeEvalOutput(unittest.TestCase):
    def test_pass_fail_true(self):
        assert _normalize_eval_output({"output_bool": True}, "pass_fail") == "pass"

    def test_pass_fail_false(self):
        assert _normalize_eval_output({"output_bool": False}, "pass_fail") == "fail"

    def test_percentage_valid(self):
        assert _normalize_eval_output(
            {"output_float": 0.87654}, "percentage"
        ) == "0.88"

    def test_percentage_none(self):
        assert _normalize_eval_output(
            {"output_float": None}, "percentage"
        ) is None

    def test_deterministic_uses_str(self):
        assert _normalize_eval_output(
            {"output_str": "toxic"}, "deterministic"
        ) == "toxic"

    def test_deterministic_fallbacks_to_str_list(self):
        assert _normalize_eval_output(
            {"output_str": None, "output_str_list": ["A"]},
            "deterministic",
        ) == "['A']"

    def test_deterministic_str_list_is_sorted(self):
        """str_list items are sorted before str() conversion so that
        order differences don't cause false disagreements."""
        assert _normalize_eval_output(
            {"output_str": None, "output_str_list": ["B", "A", "C"]},
            "deterministic",
        ) == "['A', 'B', 'C']"

    def test_pass_fail_none_bool_returns_none(self):
        assert _normalize_eval_output(
            {"output_bool": None}, "pass_fail"
        ) is None

    def test_output_type_none_falls_through_to_deterministic(self):
        """When output_type is None (not one of the recognised values)
        the function falls back to the deterministic codepath."""
        assert _normalize_eval_output(
            {"output_str": "abc"}, None
        ) == "abc"


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
        assert _majority_value([
            {"sel": ["A", "B"]},
            {"sel": ["B", "A"]},
        ]) == {"sel": ["A", "B"]}


class TestCalculateJudgeHumanAgreement(unittest.TestCase):
    """The function is called with a real or mocked ``AnnotationQueue``
    instance. We mock the database queries so the tests stay fast and
    deterministic."""

    def test_returns_none_when_no_evaluator_linked(self):
        queue = MagicMock()
        queue.custom_eval_config_id = None
        assert _calculate_judge_human_agreement(queue) is None

    def test_returns_none_when_output_type_is_none(self):
        """When eval_template.output_type_normalized is None, bail early
        because the normalisation codepath is undefined."""
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = None
        queue.custom_eval_config.name = "Safety Eval"

        assert _calculate_judge_human_agreement(queue) is None

    @patch("tracer.models.observation_span.EvalLogger.objects")
    @patch("model_hub.models.score.Score.objects")
    def test_evaluator_name_falls_back_to_config_id_when_names_empty(
        self, mock_score_objects, mock_eval_objects
    ):
        """When custom_eval_config.name is None and eval_template.name is
        empty string, evaluator_name falls back to the config PK."""
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = (
            "pass_fail"
        )
        queue.custom_eval_config.name = None
        queue.custom_eval_config.eval_template.name = ""

        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
        ]

        mock_eval_objects.filter.return_value.values.return_value = [
            {
                "observation_span_id": "span-1",
                "output_bool": True,
                "output_float": None,
                "output_str": None,
                "output_str_list": [],
            },
        ]

        mock_score_objects.filter.return_value.values.return_value = [
            {
                "queue_item_id": "item-1",
                "label_id": "label-1",
                "label__name": "Safety",
                "label__type": "categorical",
                "value": "pass",
            },
        ]

        result = _calculate_judge_human_agreement(queue)
        assert result["evaluator_name"] == "eval-config-1"

    @patch(
        "tracer.models.observation_span.EvalLogger.objects"
    )
    @patch(
        "model_hub.models.score.Score.objects"
    )
    def test_returns_none_when_no_span_sourced_items(
        self, mock_score_objects, mock_eval_objects
    ):
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = (
            "pass_fail"
        )
        queue.custom_eval_config.name = "Safety Eval"

        # No observation_span-sourced items in the queue.
        queue.items.filter.return_value.values_list.return_value = []

        assert _calculate_judge_human_agreement(queue) is None

    @patch(
        "tracer.models.observation_span.EvalLogger.objects"
    )
    @patch(
        "model_hub.models.score.Score.objects"
    )
    def test_skips_error_eval_rows(
        self, mock_score_objects, mock_eval_objects
    ):
        """EvalLogger rows with error=True are excluded by the Subquery
        filter, so only the span with a clean row contributes."""
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = (
            "pass_fail"
        )
        queue.custom_eval_config.name = "Safety Eval"

        # values_list now returns (item_id, observation_span_id) tuples.
        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
            ("item-2", "span-2"),
        ]

        # Only span-1 has a valid eval row; span-2 was error=True (excluded).
        mock_eval_objects.filter.return_value.values.return_value = [
            {
                "observation_span_id": "span-1",
                "output_bool": True,
                "output_float": None,
                "output_str": None,
                "output_str_list": [],
            },
        ]

        # Human scores for both items.
        mock_score_objects.filter.return_value.values.return_value = [
            {
                "queue_item_id": "item-1",
                "label_id": "label-1",
                "label__name": "Safety",
                "label__type": "categorical",
                "value": "pass",
            },
            {
                "queue_item_id": "item-2",
                "label_id": "label-1",
                "label__name": "Safety",
                "label__type": "categorical",
                "value": "fail",
            },
        ]

        result = _calculate_judge_human_agreement(queue)

        # span-2 had no (non-error) eval row → only item-1 contributes.
        assert result["evaluator_name"] == "Safety Eval"
        assert result["total_comparisons"] == 1
        assert result["labels"]["label-1"]["judge_human_agreement"] == 1.0

    @patch(
        "tracer.models.observation_span.EvalLogger.objects"
    )
    @patch(
        "model_hub.models.score.Score.objects"
    )
    def test_calculates_agreement_correctly(
        self, mock_score_objects, mock_eval_objects
    ):
        """Two items, two labels. Judge agrees on label-1, disagrees on
        label-2. Overall = 2/4 = 0.5."""
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = (
            "pass_fail"
        )
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
            {"queue_item_id": "item-1", "label_id": "label-1",
             "label__name": "Label A", "label__type": "categorical",
             "value": "pass"},
            {"queue_item_id": "item-1", "label_id": "label-2",
             "label__name": "Label B", "label__type": "categorical",
             "value": "fail"},
            {"queue_item_id": "item-2", "label_id": "label-1",
             "label__name": "Label A", "label__type": "categorical",
             "value": "fail"},
            {"queue_item_id": "item-2", "label_id": "label-2",
             "label__name": "Label B", "label__type": "categorical",
             "value": "pass"},
        ]

        result = _calculate_judge_human_agreement(queue)

        assert result["labels"]["label-1"]["judge_human_agreement"] == 1.0
        assert result["labels"]["label-1"]["total_comparisons"] == 2
        assert result["labels"]["label-2"]["judge_human_agreement"] == 0.0
        assert result["labels"]["label-2"]["total_comparisons"] == 2
        assert result["overall_agreement"] == 0.5
        assert result["total_comparisons"] == 4

    @patch(
        "tracer.models.observation_span.EvalLogger.objects"
    )
    @patch(
        "model_hub.models.score.Score.objects"
    )
    def test_uses_latest_eval_row_per_span(
        self, mock_score_objects, mock_eval_objects
    ):
        """The Subquery picks the latest EvalLogger row when a span has
        multiple rows for the same config."""
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = (
            "pass_fail"
        )
        queue.custom_eval_config.name = "Safety Eval"

        queue.items.filter.return_value.values_list.return_value = [
            ("item-1", "span-1"),
        ]

        mock_eval_objects.filter.return_value.values.return_value = [
            {
                "observation_span_id": "span-1",
                "output_bool": True,
                "output_float": None,
                "output_str": None,
                "output_str_list": [],
            },
        ]

        mock_score_objects.filter.return_value.values.return_value = [
            {"queue_item_id": "item-1", "label_id": "label-1",
             "label__name": "Label A", "label__type": "categorical",
             "value": "pass"},
        ]

        result = _calculate_judge_human_agreement(queue)
        assert result["labels"]["label-1"]["judge_human_agreement"] == 1.0

    @patch(
        "tracer.models.observation_span.EvalLogger.objects"
    )
    @patch(
        "model_hub.models.score.Score.objects"
    )
    def test_handles_no_overlapping_scores(
        self, mock_score_objects, mock_eval_objects
    ):
        """When span evals exist but no human scores overlap, return empty
        labels and null overall agreement."""
        queue = MagicMock()
        queue.custom_eval_config_id = "eval-config-1"
        queue.custom_eval_config.eval_template.output_type_normalized = (
            "percentage"
        )
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
