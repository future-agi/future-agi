-- =============================================================================
-- 008 — Per-session rollup (row-reducing MV → AggregatingMergeTree)
-- =============================================================================
--
-- Pattern lifted from SigNoz `trace_summary_mv` — one of the proven safe
-- materialized-view shapes in production OTel-on-CH stacks (see
-- internal-docs/clickhouse-analytics/migration-to-ch25/MV_STRATEGY.md for
-- the survey).
--
-- Why this is a SAFE MV (vs the old `spans_mv` that OOMed):
--   • spans_mv was row-EXPANDING — read 1 row, ARRAY JOIN'd a Map, wrote N
--     rows. Memory grew with per-row attribute cardinality (10K-attr LLM
--     payloads OOM'd CH).
--   • This MV is row-REDUCING — reads N rows of a batch, GROUP BYs by
--     (project_id, trace_session_id), writes <= N rows. Memory is bounded
--     by the number of distinct sessions in a batch — typically far smaller
--     than the batch itself.
--
-- What it powers:
--   The dashboard query "show me sessions sorted by token spend" used to
--   do `GROUP BY trace_session_id` on raw spans every page-load (full
--   scan of the partition). With this rollup, the query reads pre-
--   aggregated state from a tiny table (one row per (project, session)).
--
-- Dashboard read pattern:
--   SELECT trace_session_id,
--          countMerge(span_count)               AS n,
--          sumMerge(total_tokens_sum)           AS tokens,
--          sumMerge(cost_sum)                   AS cost,
--          quantilesTDigestMerge(0.5,0.95,0.99)(latency_q) AS pct
--   FROM   spans_per_session FINAL
--   WHERE  project_id = ? AND hour_first_seen >= ?
--   GROUP  BY trace_session_id
--   ORDER  BY tokens DESC LIMIT 50;
--
-- TTL: keep per-session rollups for the same window the raw spans live
-- (90 days currently; will move to 7 days raw + 90 days rollup once
-- 010 is in place and the dashboard switches to read from the rollup
-- for older windows).
-- =============================================================================

CREATE TABLE IF NOT EXISTS spans_per_session
(
    project_id            UUID,
    trace_session_id      UUID,
    hour_first_seen       DateTime('UTC'),

    span_count            AggregateFunction(count),
    total_tokens_sum      AggregateFunction(sum, Int64),
    prompt_tokens_sum     AggregateFunction(sum, Int64),
    completion_tokens_sum AggregateFunction(sum, Int64),
    cost_sum              AggregateFunction(sum, Float64),
    latency_q             AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Int32),
    -- end_time on `spans` is Nullable(DateTime64). The aggregate-function
    -- state must match the source-column nullability or CH rejects the
    -- insert (CANNOT_CONVERT_TYPE). Both first_seen and last_seen carry
    -- Nullable through so an in-flight span (no end_time yet) doesn't crash
    -- the rollup batch.
    first_seen            AggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen             AggregateFunction(max, Nullable(DateTime64(6, 'UTC'))),
    error_count           AggregateFunction(countIf, UInt8),

    -- Skip indexes for the most common dashboard filter combinations.
    INDEX idx_trace_session_id trace_session_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour_first_seen)
ORDER BY (project_id, trace_session_id, hour_first_seen)
TTL toDateTime(hour_first_seen) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS spans_per_session_mv
TO spans_per_session
AS
SELECT
    project_id,
    trace_session_id,
    toStartOfHour(min(start_time))                          AS hour_first_seen,
    countState()                                            AS span_count,
    -- Upcast Int32 → Int64 BEFORE the aggregate so the state's element type
    -- matches the destination column. CH refuses
    -- AggregateFunction(sum, Int32) → AggregateFunction(sum, Int64) conversion
    -- on insert, so the SELECT must produce the wider type.
    sumState(toInt64(total_tokens))                         AS total_tokens_sum,
    sumState(toInt64(prompt_tokens))                        AS prompt_tokens_sum,
    sumState(toInt64(completion_tokens))                    AS completion_tokens_sum,
    sumState(cost)                                          AS cost_sum,
    quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)      AS latency_q,
    minState(start_time)                                    AS first_seen,
    maxState(end_time)                                      AS last_seen,
    countIfState(status = 'ERROR')                          AS error_count
FROM spans
WHERE is_deleted = 0 AND trace_session_id IS NOT NULL
GROUP BY project_id, trace_session_id;
