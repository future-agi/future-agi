-- =============================================================================
-- 012 — trace_name on spans + trace_count_rollup for cheap pagination counts
-- =============================================================================
--
-- TWO closely-related fixes, both surfaced by the kartik regression sweep:
--
-- (A) The v2 TRACE_LIST query was emitting `SELECT ... trace_name ...` because
--     the v1 builder it subclasses pulls trace_name from the legacy v1 spans
--     table (which was denormalized via the OOM-prone spans_mv). The v2 spans
--     table never had that column, so every TRACE_LIST page-load crashed with
--     `Code: 47. Unknown expression identifier 'trace_name'`.
--
--     Fix: add a MATERIALIZED `trace_name` column. We derive it from
--     `attrs_string['fi.trace.name']` (the OTel canonical attribute the
--     fi-collector populates per resource attributes). If the producer
--     hasn't set it, the column is empty — matching the legacy behaviour
--     where unnamed traces showed blank in the UI.
--
--     This is additive — no data backfill needed; CH computes the column
--     on read for existing rows. New writes get the materialised value at
--     part-merge time.
--
-- (B) The v2 TRACE_LIST.count query ran `uniq(trace_id)` over the whole
--     project's spans (~80K rows in dev → 200ms p95). Dashboard pagination
--     calls this once per page load → 200ms × every page. With a rollup
--     this drops to ~5ms because uniqMerge over hourly states is O(buckets).
--
--     Pattern: same row-reducing AggregatingMergeTree MV shape as 008/010
--     (SigNoz/Uptrace pattern). Stores `uniqState(trace_id)` per (project,
--     hour) so the dashboard's count over any time window merges hourly
--     states.
-- =============================================================================

-- (A) trace_name MATERIALIZED column on spans
--
-- Source of truth: a trace's name is its root span's `name` (the legacy
-- ingest promotes exactly this — see langfuse_upsert.py
-- `trace.name = promoted_root_name`). We therefore derive trace_name from
-- the span's own `name`, while still honoring an explicit producer-set
-- `fi.trace.name` attribute if one is ever present (none is today, but
-- the OTel-GenAI convention reserves the key).
--
-- IMPORTANT — why `if(... != '', ..., name)` and NOT `coalesce(...)`:
-- a ClickHouse `Map(K, String)` subscript on a MISSING key returns the
-- value type's default (empty string), NOT NULL. `coalesce` only skips
-- NULLs, so `coalesce(attrs_string['fi.trace.name'], name)` would return
-- '' verbatim and never fall through to `name`. The previous definition
-- (`coalesce(attrs_string['fi.trace.name'], '')`) was dead for the same
-- reason — it always yielded '' because the key is never set. An explicit
-- emptiness guard is required.
--
-- MATERIALIZED (not ALIAS): both `attrs_string` and `name` are present on
-- the row at INSERT, so the value is computed once at write time and the
-- bloom index below can prune on it. All active readers query trace_name
-- on root spans (parent_span_id = ''), where `name` IS the trace name.
ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS trace_name String
        MATERIALIZED if(attrs_string['fi.trace.name'] != '', attrs_string['fi.trace.name'], name);

-- MODIFY after ADD so environments that already applied the old (dead,
-- coalesce-based) definition converge to the corrected expression —
-- `ADD COLUMN IF NOT EXISTS` is a no-op on an existing column and would
-- otherwise leave the broken definition in place. MODIFY of a
-- MATERIALIZED expression is metadata-only (no data rewrite); it applies
-- to future inserts. Pre-launch there are no rows to backfill; if this
-- ever ships with data present, follow with
-- `ALTER TABLE spans MATERIALIZE COLUMN trace_name` out-of-band.
ALTER TABLE spans
    MODIFY COLUMN trace_name String
        MATERIALIZED if(attrs_string['fi.trace.name'] != '', attrs_string['fi.trace.name'], name);

-- Skip-index on trace_name so the search-by-trace-name path can prune.
ALTER TABLE spans
    ADD INDEX IF NOT EXISTS idx_trace_name trace_name TYPE bloom_filter GRANULARITY 1;

-- (B) Pre-aggregated unique-trace-id counts per (project, hour)
--
-- Why a SEPARATE rollup instead of adding to the existing spans_per_session
-- or spans_hourly_rollup MVs:
--   • spans_per_session keys on (project, session) — wrong grain for trace count.
--   • spans_hourly_rollup keys on (project, hour, obs_type, model, provider) —
--     each merge sums many rows; would have to GROUP BY all of those before
--     calling uniqMerge. Slower and confusing.
--   • Dedicated table keyed on (project, hour) means the count query reads
--     ONE row per (project × hour) — the smallest possible state.
CREATE TABLE IF NOT EXISTS trace_count_rollup
(
    project_id        UUID,
    hour              DateTime('UTC'),
    -- `uniqExact` not `uniq`: HLL's approximation drifts ~10-15% on the
    -- small per-hour sets we see in dev/test data and that drift confuses
    -- dashboard pagination ("page 14 of 5913 traces" vs an actual 6814).
    -- uniqExact stores the full set, so the count is byte-accurate. Per-
    -- project trace counts are bounded (millions max, not billions), so the
    -- state size is fine even at trillion-row prod scale.
    uniq_traces_state AggregateFunction(uniqExact, String),
    -- Skip indexes irrelevant at this row count (1 row per project × hour =
    -- a few thousand rows for a typical project lifetime). PrimaryKey scan
    -- on (project_id, hour) is already O(log N).
    INDEX idx_project_id project_id TYPE minmax GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour)
TTL toDateTime(hour) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS trace_count_rollup_mv
TO trace_count_rollup
AS
SELECT
    project_id,
    toStartOfHour(start_time) AS hour,
    uniqExactState(trace_id)  AS uniq_traces_state    -- MUST match column type (uniqExact)
FROM spans
WHERE is_deleted = 0
  AND (parent_span_id = '' OR parent_span_id IS NULL)
GROUP BY project_id, hour;
