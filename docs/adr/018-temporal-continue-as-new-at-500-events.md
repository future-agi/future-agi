---
status: Accepted
date: 2026-05-08
---

# ADR 018 — `TestExecutionWorkflow` uses continue-as-new at 500 history events

## Evidence

`futureagi/simulate/temporal/workflows/test_execution_workflow.py`:
`MAX_EVENTS_BEFORE_CONTINUE_AS_NEW = 500`; checkpoint logic with `_event_count`.

## Context

Temporal persists every workflow event (activity schedule, activity complete, signal
received, timer fired, etc.) in an immutable history log. History size is bounded by
Temporal Server configuration (default ~2 MB). A test execution with 1000 calls
generates O(calls × activities × signals) events, easily exceeding the limit.

## Decision

`TestExecutionWorkflow` tracks `_event_count` (incremented per iteration of the main
loop) and calls `continue_as_new()` when it hits 500. The checkpoint payload includes
the current state (`_completed_calls`, `_failed_calls`, `_launched_call_ids`) so the
new workflow instance resumes correctly.

## Why

`continue_as_new` is the idiomatic Temporal pattern for long-running workflows. It
creates a new workflow execution with the same ID, passing state as input, and the old
history is closed. The workflow appears continuous from the outside.

## Consequences

- `_event_count` tracks parent workflow iterations, not actual Temporal event count.
  The mapping is not 1:1 (each iteration may produce multiple events), so the
  checkpoint may fire earlier or later than the actual limit.
- Signals arriving during `continue_as_new` can be lost if Temporal delivers them to
  the old (closing) workflow instance. Child workflows must handle the case where
  their parent has reset.
- After continue-as-new, `get_unlaunched_call_ids()` queries the DB for remaining
  PENDING calls — this re-establishes ground truth from Postgres rather than relying
  on in-memory state that was carried across.
