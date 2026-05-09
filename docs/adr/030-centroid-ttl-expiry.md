---
id: ADR-030
title: Expire stale cluster centroids via ClickHouse TTL (closes #306)
status: accepted
date: 2026-05-08
related_issues: ["#306"]
---

## Context

`ErrorClusteringDB.cluster_unclustered_errors()` writes cluster centroids to
the `cluster_centroids` ClickHouse table using `ReplacingMergeTree`. Old
centroids were never removed: projects with high error rates accumulated
centroids indefinitely, increasing HDBSCAN input size, memory pressure, and
clustering latency on the Temporal worker. Over months this causes OOM or
timeout failures (issue #306).

Three fix options were evaluated:

1. **ClickHouse row-level TTL** — declare `TTL last_updated + INTERVAL N DAY DELETE`
   on the table; ClickHouse background workers enforce expiry automatically.
2. **Periodic consolidation** — merge adjacent centroids in a scheduled task.
3. **Soft-delete with `status = "resolved"`** — exclude resolved clusters from
   HDBSCAN input.

## Decision

Use **option 1 — ClickHouse row-level TTL** with a default of 90 days.

Reasons:
- TTL is enforced by ClickHouse automatically; no application-level scheduler
  or manual cleanup is required.
- `ALTER TABLE cluster_centroids MODIFY TTL ...` is a fast, metadata-only
  operation — it can be applied to existing tables without downtime.
- The 90-day default covers typical error clustering lifecycles; it is
  configurable via `ErrorClusteringDB(centroid_ttl_days=N)`.
- Any cluster that receives a new member gets its `last_updated` refreshed by
  the upsert, so active clusters are never accidentally expired.

Option 2 (consolidation) solves a different problem (centroid drift) and can
be added orthogonally later. Option 3 (soft-delete) requires all query sites
to filter on `status`, is brittle, and still leaves rows in the table.

## Implementation

Two changes to `error_clustering.py`:

1. `ensure_centroid_table()` — added `TTL last_updated + INTERVAL {ttl_days} DAY DELETE`
   to the `CREATE TABLE IF NOT EXISTS` DDL so new deployments get TTL from day one.

2. `expire_stale_centroids()` — issues `ALTER TABLE cluster_centroids MODIFY TTL ...`
   once per clustering run. This is a no-op when the TTL is already set to the
   same value, and retroactively applies TTL to tables created before this fix.
   Errors are logged as warnings (non-fatal) so the clustering job is not blocked.

`cluster_unclustered_errors()` calls `expire_stale_centroids()` immediately
after `ensure_centroid_table()`.

## TTL boundary semantics

The TTL expression `last_updated + INTERVAL 90 DAY < now()` is exclusive:
a centroid refreshed exactly 90 days ago is NOT expired; one refreshed
90 days + 1 second ago IS expired. This matches the Z3/Hypothesis proofs
in `futureagi/tracer/formal_tests/test_centroid_expiry_{z3,hypothesis}.py`.

## Consequences

- Clusters with no new events in 90 days are automatically reaped by ClickHouse.
- Active projects are unaffected — every centroid update refreshes `last_updated`.
- The default of 90 days is a parameter (`centroid_ttl_days`) and can be tuned
  per deployment without code changes.
- ClickHouse TTL expiry is best-effort (background worker, not instantaneous);
  rows survive until the next merge cycle after the deadline. This is acceptable
  for a gradual memory-pressure fix.
