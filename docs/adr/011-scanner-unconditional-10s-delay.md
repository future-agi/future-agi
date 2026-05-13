---
status: Accepted — known limitation
date: 2026-05-08
---

# ADR 011 — Scanner waits 10 seconds unconditionally for straggler spans

## Evidence

`futureagi/tracer/tasks/trace_scanner.py` (or `tasks.py`):
`SCAN_DELAY_SECONDS = 10` hardcoded before invoking `TraceErrorAnalysisAgent`.

## Context

OTLP spans arrive in independent HTTP requests. The root span may be ingested before
its children, so the scanner needs to wait for the full trace to materialise before
analysing errors. A 10-second delay was chosen to cover slow agents.

## Decision

`scan_traces_task` always waits `SCAN_DELAY_SECONDS = 10` regardless of trace
complexity. A single-span trace (chatbot echo) waits the same as a 200-span
multi-agent trace.

## Why

A uniform delay is simple and operationally predictable. Adaptive logic (checking
whether all expected children have arrived) requires knowing how many children to
expect, which OTLP does not provide — a span's children are not declared in advance.

## Consequences

- Every trace incurs at least 10 seconds of latency before error analysis starts.
- Single-span and simple traces could be analysed immediately; the delay is wasted time.
- The delay is a config constant; operators can tune it, but there is no per-trace
  adaptive mechanism.
- A better approach would start scanning once the root span's `end_time` arrives and
  no new child spans have appeared within a shorter rolling window (e.g., 2 seconds).
