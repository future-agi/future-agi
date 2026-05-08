"""
Shared constants for the evaluation engine.

Extracted from model_hub/views/eval_runner.py to avoid circular imports
and provide a single source of truth.
"""

# Eval types that use FutureAGI's internal models (Turing) instead of external LLMs.
# These get special config preparation: criteria injection, few-shot retrieval,
# model/provider resolution via _prepare_futureagi_config().
# See docs/adr/002-few-shots-futureagi-only.md for why few-shot RAG is restricted to these types.
FUTUREAGI_EVAL_TYPES = [
    "RankingEvaluator",
    "DeterministicEvaluator",
]

# eval_type_id value emitted by AgentEvaluator.name (see
# agentic_eval.core_evals.fi_evals.llm.agent_evaluator.evaluator).
AGENT_EVALUATOR_TYPE_ID = "AgentEvaluator"
