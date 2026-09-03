-- =============================================================================
-- 020 — Remove all TTLs from the v2 spans + rollup tables.
-- =============================================================================
--
-- Decision: spans (and every aggregate that hangs off them) must be retained
-- indefinitely at the storage layer. Per-org retention is enforced by
-- ee/usage/tasks/retention.py via the `retention_traces_days` entitlement, not
-- by ClickHouse TTL. Removing the table-level TTL stops background merges
-- from dropping parts whose start_time exceeds the previous 90-day window.
--
-- This file is intentionally APPEND-ONLY (per apply_schema.py's hash-tracking
-- contract). Do not edit 002_spans_v2.sql or any other prior file to strip
-- their TTL clauses — that would trigger drift detection (`exit 2`) on every
-- existing cluster and the CREATE TABLE IF NOT EXISTS would no-op anyway.
-- 
-- =============================================================================

ALTER TABLE spans REMOVE TTL;

ALTER TABLE spans_v2_dead_letter REMOVE TTL;

ALTER TABLE spans_per_session REMOVE TTL;

ALTER TABLE eval_per_config REMOVE TTL;

ALTER TABLE spans_hourly_rollup REMOVE TTL;

ALTER TABLE trace_count_rollup REMOVE TTL;

ALTER TABLE span_user_rollup REMOVE TTL;
