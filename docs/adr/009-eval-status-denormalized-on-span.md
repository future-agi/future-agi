---
status: Problematic — filed as issue #305 for removal
date: 2026-05-08
---

# ADR 009 — `eval_status` is denormalised on `ObservationSpan`

## Evidence

`futureagi/tracer/models/` — `ObservationSpan.eval_status` field.
Source comment: "eval_status on the span is a design flaw. It's a denormalized snapshot
that goes stale when evals are added/removed."

## Context

`ObservationSpan` has an `eval_status` field that stores a snapshot of evaluation state
at write time. It was added as a query optimisation — display lists could read one column
instead of joining `EvalLogger`.

## Decision

The field was written once at span creation and never updated when evaluations are
added, removed, or re-run. `EvalLogger` rows are the source of truth; `eval_status` on
the span diverges immediately after any eval change.

## Why

Short-term query speed won. The cost — stale data shown in the UI — was accepted as a
known tradeoff rather than addressed structurally.

## Consequences

- Callers reading `eval_status` get incorrect state for any span that has had evals
  changed since ingestion.
- The correct approach: derive eval status dynamically by joining `EvalLogger` at query
  time, never storing it on the span.
- Filed as issue #305.
