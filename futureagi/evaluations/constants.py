"""
Shared constants for the evaluation engine.

Extracted from model_hub/views/eval_runner.py to avoid circular imports
and provide a single source of truth.
"""

# Eval types that use FutureAGI's internal models (Turing) instead of external LLMs.
# These get special config preparation: criteria injection, few-shot retrieval,
# model/provider resolution via _prepare_futureagi_config().
FUTUREAGI_EVAL_TYPES = [
    "RankingEvaluator",
    "DeterministicEvaluator",
]

# eval_type_id value emitted by AgentEvaluator.name (see
# agentic_eval.core_evals.fi_evals.llm.agent_evaluator.evaluator).
AGENT_EVALUATOR_TYPE_ID = "AgentEvaluator"

# eval_type_id value emitted by CustomPromptEvaluator.name (see
# agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator).
CUSTOM_PROMPT_EVALUATOR_TYPE_ID = "CustomPromptEvaluator"

# The only evaluators that read a `ground_truth_blocks` kwarg. Every other
# type splats its kwargs straight into a bare operation, so handing them the
# key raises "unexpected keyword argument" and fails the run.
GROUND_TRUTH_AWARE_EVAL_TYPES = frozenset(
    {CUSTOM_PROMPT_EVALUATOR_TYPE_ID, AGENT_EVALUATOR_TYPE_ID}
)
