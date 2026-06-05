# ADR 003 — `EvalResult.failure` is a string, not a bool

**Status**: Accepted
**Evidence**: `evaluations/engine/runner.py:59` (dataclass field `failure: str | None`)

## Context

An eval can fail in two distinct ways:

1. The evaluator ran successfully and determined the response did not pass (e.g. a constraint
   was violated, a score was below threshold). This is a **domain failure** — the eval worked
   as intended.
2. Something went wrong during evaluation (bad JSON, missing required field, model API error).
   This is an **execution failure** — the eval could not produce a result.

Early in the codebase's history, these were conflated under a single boolean `failure` flag
checked as `if result["failure"]`.

## Decision

`EvalResult.failure` is typed `str | None`:

- `None` — eval produced a valid result (pass or fail in the domain sense).
- A non-empty string — eval could not run; the string is the human-readable reason.

`result.value` carries the domain pass/fail outcome (e.g. `"Passed"` / `"Failed"` for
Pass/Fail output type, a score float for score type). `result.failure` is purely about
whether a result could be produced at all.

## Consequences

- Callers must check `if result.failure` to detect execution failures, then separately check
  `result.value` for the domain outcome. This is more precise than a bool but requires
  two-step checking.
- `result.failure` doubles as the error message — no separate `result.error_message` field.
  Callers rendering failure reasons use `result.failure` directly.
- A bool guard like `if result.failure is True` will always be False — use `if result.failure`.
