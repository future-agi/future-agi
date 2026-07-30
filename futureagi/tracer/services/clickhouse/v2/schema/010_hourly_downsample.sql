-- =============================================================================
-- 010 — Hourly downsample (raw 7d → hourly 90d, the Uptrace pattern)
-- =============================================================================
--
-- Pattern lifted from Uptrace `datapoint_minutes → datapoint_hours_mv →
-- datapoint_hours` — chained incremental aggregate-state MV (see
-- MV_STRATEGY.md for the survey of why this is the production-blessed
-- shape vs `TTL ... GROUP BY` in-place downsample).
--
-- Why two tables instead of `TTL ... GROUP BY` on `spans`:
--   • In-place TTL aggregation runs during merges and can stall the merge
--     scheduler on multi-billion-row partitions. Memory cost is unbounded
--     by partition size.
--   • Two-table chained-MV: insert cost is bounded (per-batch GROUP BY of
--     the new rows only). Old data drops via TTL on the raw table; the
--     rollup keeps its 90-day window independently.
--   • Production stacks (Uptrace, SigNoz, HyperDX) all use the two-table
--     pattern. Zero use `TTL ... GROUP BY`.
--
-- IMPORTANT: this file is ADDITIVE only. It does NOT change `spans`'s
-- TTL to 7 days yet — that's a follow-on cutover step once dashboards
-- are confirmed to route >7d queries through the rollup table.
--
-- Activation sequence (deliberate, post-dashboard-cutover):
--   1. Apply this file. The rollup table fills via the MV on every new
--      insert into `spans`. After 24-48 hours, the rollup has full
--      coverage for new data.
--   2. Backfill historical: run a manual `INSERT INTO spans_hourly_rollup
--      SELECT ... GROUP BY ... FROM spans` to populate older windows.
--   3. Update dashboard queries that look at >7d windows to read from
--      `spans_hourly_rollup` instead of `spans`. Verify via the
--      `perf_audit.py` harness.
--   4. ONLY THEN: ALTER TABLE spans MODIFY TTL toDateTime(start_time) +
--      INTERVAL 7 DAY DELETE. Raw rows older than 7 days drop; rollup
--      keeps serving the historical window.
--
-- Dashboard read pattern for historical queries:
--   SELECT hour, observation_type,
--          countMerge(n) AS span_count,
--          sumMerge(total_tokens_sum) AS tokens,
--          quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q) AS pct
--   FROM   spans_hourly_rollup FINAL
--   WHERE  project_id = ? AND hour >= now() - INTERVAL 30 DAY
--   GROUP  BY hour, observation_type ORDER BY hour;
-- =============================================================================

CREATE TABLE IF NOT EXISTS spans_hourly_rollup
(
    hour                  DateTime('UTC'),
    project_id            UUID,
    observation_type      LowCardinality(String),
    model                 LowCardinality(String),
    provider              LowCardinality(String),

    n                     AggregateFunction(count),
    error_count           AggregateFunction(countIf, UInt8),
    total_tokens_sum      AggregateFunction(sum, Int64),
    prompt_tokens_sum     AggregateFunction(sum, Int64),
    completion_tokens_sum AggregateFunction(sum, Int64),
    cost_sum              AggregateFunction(sum, Float64),
    latency_q             AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Int32),

    -- Skip indexes
    INDEX idx_model    model    TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_provider provider TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour, observation_type, model, provider)
TTL toDateTime(hour) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS spans_hourly_rollup_mv
TO spans_hourly_rollup
AS
SELECT
    toStartOfHour(start_time)                            AS hour,
    project_id,
    observation_type,
    model,
    provider,
    countState()                                          AS n,
    countIfState(status = 'ERROR')                        AS error_count,
    -- toInt64 cast: raw Int32 → aggregate-state Int64 (CH refuses the
    -- implicit conversion). See 008's comment for the full rationale.
    sumState(toInt64(total_tokens))                       AS total_tokens_sum,
    sumState(toInt64(prompt_tokens))                      AS prompt_tokens_sum,
    sumState(toInt64(completion_tokens))                  AS completion_tokens_sum,
    sumState(cost)                                        AS cost_sum,
    quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)    AS latency_q
FROM spans
WHERE is_deleted = 0
-- Use the explicit expression (not the `hour` alias) to side-step the CH
-- 25.x analyzer quirk that fails to recognize SELECT aliases in GROUP BY
-- when the alias name collides with no column but appears in the SELECT.
-- (See DECISIONS #020 in internal-docs/clickhouse-analytics/migration-to-ch25/.)
GROUP BY toStartOfHour(start_time), project_id, observation_type, model, provider;
