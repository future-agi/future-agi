---
status: Accepted
date: 2026-05-08
---

# ADR 013 — OTLP payloads staged in Redis before Temporal activity

## Evidence

`futureagi/tracer/views/` — `OTLPTraceView.post()` and `ObservationSpanService.Export()`:
payload serialised to JSON/bytes, stored in Redis with TTL, then
`bulk_create_observation_span_task.apply_async(args=[payload_key, ...])`.

## Context

OTLP/HTTP requests can carry large payloads (hundreds of spans, megabytes of attribute
data). Temporal activities pass arguments as serialised function parameters; large
arguments bloat the Temporal history and hit size limits.

## Decision

The view stores the raw payload in Redis and passes only the Redis key to the Temporal
activity. The activity retrieves the payload, processes it, and never writes back to
Redis (TTL handles cleanup).

## Why

- Temporal history entries have a size limit (~2 MB per event). Embedding a 5 MB
  OTLP payload directly would exceed it.
- Redis is already in the stack for caching and locking; adding a payload staging
  pattern is low operational overhead.
- The view returns immediately (200 OK) after the Redis write, keeping ingest latency
  decoupled from processing latency.

## Consequences

- If Redis returns `None` for the key (TTL expired or eviction), the activity logs
  `trace_payload_not_found_in_redis` at ERROR level and raises `ValueError`. The
  Temporal activity fails visibly; Temporal records the failure. The spans are lost
  but the failure is neither silent nor swallowed.
  Evidence: `futureagi/tracer/utils/trace_ingestion.py:670-675`.
- Payloads with TTL `PAYLOAD_DEFAULT_TTL` (24h) are leaked storage if the activity
  never runs (e.g., Temporal worker outage lasting >24h).
- The payload is stored in Redis, not in Temporal — it is not replay-safe. If the
  activity fails and Temporal retries it after TTL expiry, the retry will also fail
  with the same `ValueError`.
