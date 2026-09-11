"""Context management for the agent evaluator (TH-4970).

Public surface for the evaluator to wire in:

    from ee.evals.llm.agent_evaluator.context import EvalLLMClient

Internal modules:
  budget    — token estimation, Turing-model thresholds, media detection
  digest    — digest sentinels + attach-to-first-user-anchor logic
  prompts   — compaction system prompt (agent-type-neutral)
  client    — EvalLLMClient orchestrator (the 4-layer defense)
  logging   — safe-log shims that never propagate logger errors

The split keeps each concern independently testable and lets the
defense layers be reasoned about one at a time.
"""

from ee.evals.llm.agent_evaluator.context.client import EvalLLMClient

__all__ = ["EvalLLMClient"]
