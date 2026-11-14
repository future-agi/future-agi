-- =============================================================================
-- spans_v2_dead_letter — failed-conversion bucket for the backfill
-- =============================================================================
--
-- ANY row that fails:
--   • PG → adapter normalization
--   • adapter → typed Map split
--   • CH insert (e.g., type-cast error)
--
-- ...lands here with the raw PG payload + the error class + the timestamp.
-- The backfill continues processing the rest of the batch. The next morning,
-- ops triages with:
--
--   SELECT error_class, count(), groupArray(error_message)[1:3]
--   FROM   spans_v2_dead_letter
--   WHERE  attempted_at > now() - INTERVAL 1 DAY
--   GROUP  BY error_class
--   ORDER  BY count() DESC
--
-- Per DECISIONS.md #007 — silent drops are forbidden; this table is the
-- enforcement mechanism.
--
-- TTL 30 days so it self-cleans after we've triaged.
-- =============================================================================

CREATE TABLE IF NOT EXISTS spans_v2_dead_letter
(
    -- Provenance back to PG
    pg_id              String,
    project_id         Nullable(UUID),
    trace_id           Nullable(String),

    -- The raw PG row, serialized to JSON, so we can replay after fixing the bug.
    raw_pg_row         String CODEC(ZSTD(3)),

    -- What went wrong
    error_class        LowCardinality(String),       -- e.g. 'ADAPTER_FAIL', 'TYPE_CAST', 'CH_INSERT'
    error_message      String,
    error_stage        LowCardinality(String),       -- 'adapter', 'split', 'insert'

    -- When
    attempted_at       DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),

    -- Which backfill run produced this row (for grouping replays)
    backfill_run_id    String DEFAULT '',

    -- Stable ordering for dedup if we retry
    INDEX idx_pg_id pg_id TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(attempted_at)
ORDER BY (attempted_at, pg_id)
TTL toDateTime(attempted_at) + INTERVAL 30 DAY DELETE
SETTINGS index_granularity = 8192;
