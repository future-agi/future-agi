---
status: Accepted — architectural constraint
date: 2026-05-08
---

# ADR 012 — ClickHouse dual-write has no defined consistency window

## Evidence

`futureagi/tracer/services/clickhouse/writer.py` — `ClickHouseWriter`: background thread,
`flush_interval = 5s`, `batch_size = 1000`, `max_retries = 3`. No acknowledgement
callback to the ingestion pipeline.

## Context

The platform uses PostgreSQL as the transactional source of truth and ClickHouse for
analytical queries (dashboards, time series, eval metrics). Spans are written to
PostgreSQL synchronously, then forwarded to ClickHouse asynchronously via a background
thread.

## Decision

The ClickHouse writer is fire-and-forget from the ingestion pipeline's perspective.
The ingestion activity returns success after the Postgres commit; ClickHouse writes
happen on a background thread with up to `flush_interval` delay and three retries on
failure. There is no SLA, no acknowledgement, and no replay from Postgres WAL on
persistent failure.

## Why

Making ingestion wait for ClickHouse confirmation would couple latency to ClickHouse
availability. The analytics use-case tolerates eventual consistency; real-time
operational data (trace detail, eval results) is served from PostgreSQL.

## Consequences

- Analytics queries (dashboards, graphs) may return stale data for up to ~5+ seconds
  after a span is ingested.
- If the ClickHouseWriter thread crashes, writes since the last flush are lost with
  no recovery mechanism.
- Callers must not assume ClickHouse reflects the same state as Postgres at any
  specific instant.
- A WAL-based CDC approach (e.g., PeerDB, already available in `COMPOSE_PROFILES=full`)
  would provide durable replay — but is opt-in, not the default path.
