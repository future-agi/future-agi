import pytest
from unittest.mock import MagicMock, patch
from agentic_eval.core_evals.fi_evals.sre_root_cause_accuracy.evaluator import SRERootCauseAccuracyEvaluator
from agentic_eval.core_evals.fi_evals.eval_type import LlmEvalTypeId
import json

@pytest.fixture
def evaluator():
    return SRERootCauseAccuracyEvaluator(failure_threshold=0.7)

def test_evaluator_properties(evaluator):
    assert evaluator.name == "sre_root_cause_accuracy"
    assert evaluator.display_name == "SRE Root Cause Accuracy"
    assert "sre_root_cause_score" in evaluator.metric_ids
    assert "diagnosis" in evaluator.required_args
    assert "context" in evaluator.required_args

def test_registration():
    # Test that sre_root_cause_accuracy is registered correctly in LlmEvalTypeId
    assert LlmEvalTypeId.SRE_ROOT_CAUSE_ACCURACY.value == "sre_root_cause_accuracy"

@patch('agentic_eval.core_evals.fi_evals.sre_root_cause_accuracy.evaluator.LLM._get_completion_content')
def test_evaluate_correct_diagnosis(mock_llm_call, evaluator):
    # Setup mock response
    mock_llm_call.return_value = json.dumps({"score": 1.0, "reason": "Perfect diagnosis."})

    context = "Logs show OutOfMemoryError in auth-service container at 10:05 PM due to large payload."
    diagnosis = "The auth-service crashed because it ran out of memory while processing a large payload."
    
    result = evaluator._evaluate(diagnosis=diagnosis, context=context)
    
    assert result["failure"] is False
    assert result["metrics"][0].value == 1.0
    assert result["reason"] == "Perfect diagnosis."

@patch('agentic_eval.core_evals.fi_evals.sre_root_cause_accuracy.evaluator.LLM._get_completion_content')
def test_evaluate_partial_diagnosis(mock_llm_call, evaluator):
    # Setup mock response
    mock_llm_call.return_value = json.dumps({"score": 0.5, "reason": "Partial diagnosis."})

    context = "Logs show OutOfMemoryError in auth-service container at 10:05 PM due to large payload."
    diagnosis = "The auth-service crashed."
    
    result = evaluator._evaluate(diagnosis=diagnosis, context=context)
    
    assert result["failure"] is True
    assert result["metrics"][0].value == 0.5

@patch('agentic_eval.core_evals.fi_evals.sre_root_cause_accuracy.evaluator.LLM._get_completion_content')
def test_evaluate_hallucinated_diagnosis(mock_llm_call, evaluator):
    # Setup mock response
    mock_llm_call.return_value = json.dumps({"score": 0.0, "reason": "Hallucinated diagnosis."})

    context = "Logs show OutOfMemoryError in auth-service container at 10:05 PM due to large payload."
    diagnosis = "The database went down because the CPU overheated and melted."
    
    result = evaluator._evaluate(diagnosis=diagnosis, context=context)
    
    assert result["failure"] is True
    assert result["metrics"][0].value == 0.0

def test_missing_args(evaluator):
    with pytest.raises(ValueError):
        evaluator._evaluate(diagnosis="Only diagnosis provided")
