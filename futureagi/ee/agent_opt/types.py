from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional


class LLMMessage(BaseModel):
    """Every message sent and received by the LLM MUST follow this format."""

    role: str
    content: str
    name: Optional[str] = None
    function_call: Optional[str] = None
    tool_call_id: Optional[str] = None


class EvaluationResult(BaseModel):
    """
    A standardized  result from a single evaluation.
    """

    score: float = Field(..., description="The normalized score (0.0 to 1.0).")
    reason: str = Field("", description="The explanation for the score.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Any other metadata from the evaluator."
    )


class IterationHistory(BaseModel):
    """
    A detailed record of a single optimization iteration.
    """

    prompt: str
    average_score: float
    individual_results: Dict[str, EvaluationResult]


class OptimizationResult(BaseModel):
    """Output Model to hold the results of an optimization run."""

    best_prompt: str
    history: List[IterationHistory]
    final_score: float = 0.0
    best_index: int = 0
    stepper_state: Optional[Dict[str, Any]] = None
