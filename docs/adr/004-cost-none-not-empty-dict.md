# ADR 004 — `EvalResult.cost` is `None` when absent, not `{}`

**Status**: Accepted
**Evidence**: commit `4e768b0` ("fix: emit cost-based UsageEvent for SDK evaluations — TH-3402"); `evaluations/engine/runner.py:71-74,187-188`

## Context

Billing for eval runs was originally handled entirely inside `EvaluationRunner`, which held
the evaluator instance and read `eval_instance.cost` directly. When the SDK evaluation path
(`sdk/utils/evaluations.py`) needed to emit billing events, it had no access to the instance
after `run_eval()` returned.

The fix (commit `4e768b0`, "fix: emit cost-based UsageEvent for SDK evaluations — TH-3402")
added `cost` and `token_usage` to `EvalResult` so callers can emit billing without holding
the instance.

## Decision

```python
cost: dict | None = None
token_usage: dict | None = None
```

Populated via:
```python
cost=getattr(eval_instance, "cost", None),
token_usage=getattr(eval_instance, "token_usage", None),
```

`None` means the evaluator does not expose cost data (most deterministic/function evals).
A dict means cost data is available for billing.

## Why `None` not `{}`

An empty dict `{}` is falsy in Python but is semantically ambiguous — it could mean "the
evaluator tracks cost but this run cost nothing" or "the evaluator doesn't track cost". `None`
is unambiguous: this evaluator produced no billing data. Callers that emit billing events guard
with `if result.cost is not None`.

## Consequences

- Callers must guard `if result.cost is not None` before using cost data. A bare `if result.cost`
  would also work (empty dict is falsy) but would silently drop a zero-cost run.
- `None` propagates to `EvalResult` even for LLM evals if the evaluator instance doesn't set
  `.cost`. Always instrument new LLM evaluators with `.cost` and `.token_usage` properties.
