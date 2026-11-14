"""Service-free eval-template average tests, collectable by pytest or unittest."""

import ast
import json
import logging
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace


CHOICES_MAP = {"Excellent": "pass", "Average": "neutral", "Poor": "fail"}


def _load_calculate_average():
    # Execute the production function without importing unrelated Django/ClickHouse
    # dependencies from functions.py. No scoring logic is duplicated in this test.
    source_path = Path(__file__).resolve().parents[1] / "functions.py"
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_eval_average"
    )
    namespace = {
        "json": json,
        "logger": logging.getLogger(__name__),
        "traceback": traceback,
        # Canonical status from tfc/constants/api_calls.py.
        "APICallStatusChoices": SimpleNamespace(
            SUCCESS=SimpleNamespace(value="success")
        ),
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(source_path), "exec"),
        namespace,
    )
    return namespace["calculate_eval_average"]


def _log(selected_choices, status="success"):
    return {
        "status": status,
        "config": json.dumps({"output": {"output": selected_choices}}),
    }


class TestCalculateEvalAverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calculate_average = staticmethod(_load_calculate_average())

    def assert_choice_average(
        self, logs, expected, *, multi_choice=True, choices_map=CHOICES_MAP
    ):
        for output_type in ("choices", "reason"):
            with self.subTest(output_type=output_type):
                template = SimpleNamespace(
                    multi_choice=multi_choice,
                    config={"output": output_type, "choices_map": choices_map},
                )
                self.assertEqual(self.calculate_average(template, logs), expected)

    def test_multichoice_uses_choice_weights(self):
        for selected_choices, expected in [
            (["Poor"], 0),
            (["Excellent"], 100),
            (["Average"], 50),
            (["Excellent", "Poor"], 50),
            (["Excellent", "Average"], 75),
            (["Excellent", "Average", "Poor"], 50),
            (["Excellent", "Unknown"], 50),
        ]:
            with self.subTest(selected_choices=selected_choices):
                self.assert_choice_average([_log(selected_choices)], expected)

    def test_multichoice_weights_each_log_equally(self):
        # Per-log means are 0.75 and 0, not the pooled-choice mean of 0.5.
        logs = [_log(["Excellent", "Average"]), _log(["Poor"])]
        self.assert_choice_average(logs, 37.5)

    def test_multichoice_mostly_failing_traces(self):
        logs = [_log(["Poor"])] * 80 + [_log(["Excellent"])] * 20
        self.assert_choice_average(logs, 20)

    def test_empty_or_malformed_selections_are_not_successes(self):
        for invalid_choices in ([], None, "Excellent"):
            with self.subTest(invalid_choices=invalid_choices):
                self.assert_choice_average([_log(invalid_choices)], 0)
                logs = [_log(invalid_choices), _log(["Average"])]
                self.assert_choice_average(logs, 50)

    def test_unsuccessful_and_malformed_logs_are_skipped(self):
        logs = [
            _log(["Excellent"], status="error"),
            {"status": "success", "config": "not json"},
            {"status": "success", "config": json.dumps({})},
            _log(["Average"]),
        ]
        self.assert_choice_average(logs, 50)

    def test_single_choice_still_scores_only_first_choice(self):
        for selected_choices, expected in [
            (["Poor"], 0),
            (["Average"], 50),
            (["Unknown"], 0),
            (["Excellent", "Poor"], 100),
        ]:
            with self.subTest(selected_choices=selected_choices):
                self.assert_choice_average(
                    [_log(selected_choices)], expected, multi_choice=False
                )

    def test_unmapped_templates_keep_existing_success_fallback(self):
        for choices_map in (None, {}):
            for multi_choice in (False, True):
                with self.subTest(choices_map=choices_map, multi_choice=multi_choice):
                    self.assert_choice_average(
                        [_log(["Poor"])],
                        100,
                        multi_choice=multi_choice,
                        choices_map=choices_map,
                    )

    def test_no_logs_returns_zero(self):
        self.assert_choice_average([], 0)

    def test_other_output_types_are_unchanged(self):
        for output_type, outputs in [
            ("Pass/Fail", ["Passed", "Failed"]),
            ("score", [0.25, 0.75]),
        ]:
            with self.subTest(output_type=output_type):
                template = SimpleNamespace(
                    config={"output": output_type}, multi_choice=False
                )
                self.assertEqual(
                    self.calculate_average(template, [_log(value) for value in outputs]),
                    50,
                )


if __name__ == "__main__":
    unittest.main()
