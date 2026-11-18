"""Run the actual validator on shared Go/JS fixtures without Django services.

Standalone: python3.11 -I path/to/test_guardrail_numeric_readiness.py -v
The TestCase is also discoverable by the repository's normal pytest runner.
"""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "numeric_guardrail_config_validation",
    ROOT / "futureagi/agentcc/guardrail_config_validation.py",
)
VALIDATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATION)
FIXTURE_PATH = (
    ROOT
    / "agentcc-gateway/internal/guardrails/contentmod/testdata/numeric_readiness.json"
)
FIXTURES = json.loads(FIXTURE_PATH.read_text())


class NumericReadinessParityTest(unittest.TestCase):
    """The production helper must agree with all selected JSON boundaries."""


def _fixture_test(fixture):
    def test(self):
        config = copy.deepcopy(fixture["config"])
        before = copy.deepcopy(config)
        error = VALIDATION.builtin_config_error(
            "content-moderation", config, fixture["threshold"]
        )
        self.assertEqual(error is None, fixture["valid"], error)
        self.assertEqual(config, before)

    return test


for _fixture in FIXTURES:
    setattr(
        NumericReadinessParityTest,
        "test_" + _fixture["name"].replace("-", "_"),
        _fixture_test(_fixture),
    )


def _nonfinite_test(value):
    def test(self):
        for config in (
            {"weights": {"toxicity": value}},
            {"category_threshold": value},
        ):
            self.assertIsNotNone(
                VALIDATION.builtin_config_error("content-moderation", config, 0)
            )

    return test


for _name, _value in (
    ("nan", float("nan")),
    ("inf", float("inf")),
    ("negative_inf", -float("inf")),
):
    setattr(NumericReadinessParityTest, "test_" + _name, _nonfinite_test(_value))


if __name__ == "__main__":
    unittest.main()
