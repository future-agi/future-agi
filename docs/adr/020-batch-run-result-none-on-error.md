---
status: Accepted — caller contract documented
date: 2026-05-08
---

# ADR 020 — `run_batch()` returns `None` entries for per-item errors

## Evidence

`futureagi/agentic_eval/core_evals/fi_evals/base_evaluator.py` — `_run_batch_generator_async()`:
thread exceptions are caught and yield `None` into `eval_results`.

`BatchRunResult.eval_results: list[EvalResult | None]`

## Context

`run_batch()` uses a `ThreadPoolExecutor` to evaluate multiple items in parallel.
Individual item failures should not abort the entire batch — other items should still
produce results.

## Decision

Per-item exceptions are caught inside the thread, logged, and replaced with `None` in
`eval_results`. The batch always completes; callers inspect the list and handle `None`.

## Why

Batch evaluation is used for dataset evaluation where partial results are valuable.
Failing the whole batch because one row had a malformed input would discard all valid
results. `None` as a sentinel is cheap to check and doesn't conflate "eval ran and
failed" (which produces `EvalResult(failure=True)`) with "eval raised an exception".

## Consequences

- Callers must distinguish `None` (exception swallowed) from `EvalResult(failure=True)`
  (evaluator ran successfully but judged a failure). These have different meanings.
- The exception is logged but the caller has no programmatic access to the error.
- `evaluations/engine/formatting.extract_raw_result()` reads `eval_results[0]` — if
  that entry is `None`, it returns `{}` and the error appears as an empty result, not a
  raised exception. The failure is silent at the engine level.
