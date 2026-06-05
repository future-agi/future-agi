---
status: Problematic — filed as issue #310
date: 2026-05-08
---

# ADR 014 — Dual execution path: Temporal workflow vs. legacy TestExecutor

## Evidence

`futureagi/simulate/services/test_executor.py` — comment: "DEPRECATED".
`futureagi/simulate/views/` — `RunTestExecutionView._execute_with_temporal()` vs.
`_execute_with_legacy()`, gated on `TEMPORAL_TEST_EXECUTION_ENABLED` feature flag.

## Context

The simulate app originally executed tests with a polling-based Celery task
(`TestExecutor.execute_test()`). This was replaced by a durable Temporal workflow
(`TestExecutionWorkflow`) that handles retries, signals, and continue-as-new.

## Decision

The `TestExecutor` (Celery path) was kept alive as a fallback behind the
`TEMPORAL_TEST_EXECUTION_ENABLED` flag rather than being removed after Temporal
was validated in production.

## Why

Operational caution: Temporal was a new dependency. Keeping the Celery fallback allowed
rolling back without a code deploy if Temporal had reliability issues.

## Consequences

- Two execution paths must be kept in sync. Features added to `TestExecutionWorkflow`
  are silently absent in the Celery path.
- `TestExecutor` is 256 KB of tightly-coupled logic with balance checking, call monitoring,
  and metric calculation — it cannot be easily removed without refactoring callers.
- The divergence is already happening: `continue_as_new`, `eval_only` reruns, and merge
  signals exist only in the Temporal path.
- Filed as issue #310.
