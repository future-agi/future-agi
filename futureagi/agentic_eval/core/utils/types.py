from enum import Enum, unique

from pydantic import BaseModel


@unique
class MetricTypes(Enum):
    WHOLE_USER_OUTPUT = "WholeUserOutput", "Whole User Output"
    STEPWISE_MODEL_INFERENCE = (
        "StepwiseModelInference",
        "Stepwise Model Inference",
    )


class PromptEvalResponse(BaseModel):
    is_ambiguity: bool = False
    explanation: str = ""
    prompts: str = "Need more information to create unambiguous prompt."


class PromptEvalLlmResponse(BaseModel):
    ambiguous: bool
    explanation: str
    suggested_prompt: str


class PromptGenerateResponse(BaseModel):
    generated_prompt: str
    explanation: str
    ambiguous: bool


class PromptGenerateLLMResponse(BaseModel):
    suggested_prompt: str
    explanation: str
    ambiguous: bool


class PromptSuggestion(BaseModel):
    prompt_suggestion: str
    explanation: str
    ambiguous: bool


class Planning(BaseModel):
    planning: str
    explanation: str
    ambiguous: bool


class PlanningValidation(BaseModel):
    planning_validation: str
    explanation: str
    ambiguous: bool


class InitialDraftPrompt(BaseModel):
    initial_draft_prompt: str
    explanation: str
    ambiguous: bool


class RefinedPrompt(BaseModel):
    refined_prompt: str
    explanation: str
    ambiguous: bool


class OptimizedPrompt(BaseModel):
    optimized_prompt: str
    explanation: str
    ambiguous: bool


class OptimizedPromptValidation(BaseModel):
    optimized_prompt_validation: str
    explanation: str
    ambiguous: bool


class FinalPrompt(BaseModel):
    final_prompt: str
    explanation: str
    ambiguous: bool
