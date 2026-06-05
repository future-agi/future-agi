---
status: Accepted — known limitation
date: 2026-05-08
---

# ADR 010 — Root span `input`/`output` is last-writer-wins in a batch

## Evidence

`futureagi/tracer/utils/trace_ingestion.py` — `_bulk_update_traces()`:
only root spans (`parent_span_id IS NULL`) contribute `input`/`output` to `Trace`,
and the last one processed in the batch wins.

## Context

`Trace.input` and `Trace.output` are denormalised copies of the root span's user-visible
content, used for display in the trace list. They are populated by `_bulk_update_traces()`
during ingestion.

## Decision

When a batch contains multiple root spans for the same trace (e.g., re-ingestion or
split batch), the `_bulk_update_traces()` function iterates all root spans without
deduplication. The last span in iteration order overwrites any earlier value for that trace.

## Why

The common case is one root span per trace per batch, so the race condition was not
observed in production. Handling it robustly would require tracking which root span's
data is canonical (usually the earliest by `start_time`), which adds complexity for
an edge case.

## Consequences

- Traces with multiple root spans in a single batch may display incorrect `input`/`output`.
- Re-ingestion of a trace can overwrite correct data with stale data if ordering differs.
- The correct fix: prefer the root span with the earliest `start_time_unix_nano`.
