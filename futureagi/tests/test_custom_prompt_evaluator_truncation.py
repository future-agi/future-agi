import json
import pytest
from unittest.mock import patch
from futureagi.agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import CustomPromptEvaluator


def test_truncation_warning_on_large_input():
    """Verify truncation warning is logged and flagged in metadata."""
    evaluator = CustomPromptEvaluator(
        rule_prompt="Evaluate: {{input}}",
        model="gpt-4",
    )
    
    large_input = "x" * 200001
    
    with patch.object(evaluator, 'call_llm') as mock_llm:
        mock_llm.return_value = '{"result": "Pass", "explanation": "Test"}'
        
        result = evaluator._evaluate(
            required_keys=["input"],
            input=large_input
        )
        
        metadata = json.loads(result["metadata"])
        assert metadata["truncated"] == True
        assert "input" in metadata["truncated_fields"]
