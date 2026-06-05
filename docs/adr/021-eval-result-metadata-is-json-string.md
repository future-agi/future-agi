---
status: Problematic — filed as issue #315
date: 2026-05-08
---

# ADR 021 — `EvalResult.metadata` is always a JSON string, typed as `str | None`

## Evidence

`futureagi/agentic_eval/core_evals/fi_evals/base_evaluator.py` — `EvalResult` TypedDict:
`"metadata": str | None`.

`futureagi/agentic_eval/core_evals/fi_evals/llm/custom_prompt_evaluator/evaluator.py`:
`metadata = json.dumps({"usage": ..., "cost": ..., "response_time": ..., "explanation": ...})`

`futureagi/agentic_eval/core_evals/fi_evals/function/function_evaluator.py`:
`metadata = None`

## Context

`metadata` was added to carry ancillary data (token usage, cost, explanation) out of
the evaluator without changing the `EvalResult` TypedDict structure. JSON-serialising
it was the path of least resistance.

## Decision

LLM-based evaluators always serialise a metadata dict to a JSON string.
Function-based evaluators always return `None`. The type hint `str | None` accurately
reflects the actual range but does not convey that the string is always valid JSON.

## Why

Adding structured fields to `EvalResult` TypedDict (e.g., `token_usage: dict | None`,
`cost: float | None`) would require updating all evaluator implementations at once.
The JSON string approach let each evaluator carry arbitrary ancillary data with no
schema change.

## Consequences

- Callers wanting `usage` or `cost` must `json.loads(result["metadata"])` and handle the
  `None` case. No caller can tell from the type hint whether the string is JSON.
- Function evaluators always produce `metadata=None`, so callers can't assume metadata
  is always present even for successful evals.
- The actual cost data lives in `eval_instance.cost` (post-run attribute), not in
  `EvalResult.metadata`. These are two separate paths for the same information. Filed as
  issue #315.
