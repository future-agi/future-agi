"""W10-EVALS-L02: injected calibration must not be silently discarded."""

from unittest.mock import Mock

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.mapping import JevMappingError, validate_jev_mapping

pytestmark = pytest.mark.requires_ee

BLOCKS = [
    {"type": "text", "text": "## Reference example 1"},
    {"type": "text", "text": "Inputs:"},
    {"type": "text", "text": "- answer: calibration sentinel"},
    {"type": "text", "text": "Expected output: Pass"},
]


def test_mapping_rejects_service_injected_ground_truth_blocks():
    with pytest.raises(JevMappingError, match="JEV_FEW_SHOT_UNSUPPORTED"):
        validate_jev_mapping(
            None,
            output="Pass/Fail",
            choice_scores=None,
            multi_choice=False,
            config={"ground_truth_blocks": BLOCKS},
        )


def test_runtime_rejects_ground_truth_before_calling_provider():
    client = Mock()
    client.evaluate.return_value = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }
    client.gateway_request_id = None
    evaluator = JevEvaluator(
        rule_prompt="Judge {{answer}}",
        model="jev-latest",
        required_keys=["answer"],
        client=client,
    )
    with pytest.raises(JevMappingError, match="JEV_FEW_SHOT_UNSUPPORTED"):
        evaluator._evaluate(answer="An answer", ground_truth_blocks=BLOCKS)
    client.evaluate.assert_not_called()


@pytest.mark.parametrize("blocks", [None, []])
def test_empty_ground_truth_blocks_remain_compatible(blocks):
    mapping = validate_jev_mapping(
        None,
        output="Pass/Fail",
        choice_scores=None,
        multi_choice=False,
        config={"ground_truth_blocks": blocks},
    )
    assert mapping.question_type == "noul"
