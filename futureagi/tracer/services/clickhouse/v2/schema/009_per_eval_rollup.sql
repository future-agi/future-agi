-- =============================================================================
-- 009 — Per-eval-config rollup (row-reducing MV → AggregatingMergeTree)
-- =============================================================================
--
-- Pattern lifted from SigNoz `dependency_graph_minutes_*_mv` — incremental
-- aggregate-state MV (see MV_STRATEGY.md).
--
-- STATUS:
--   This MV requires the `tracer_eval_logger` table to live on the SAME
--   ClickHouse cluster as `spans`. Today it lives on the legacy CH 24.10
--   cluster (still CDC'd by PeerDB from PG `tracer_eval_logger`). It will
--   move to CH 25.3 in a separate, future migration (deferred from
--   PLAN_V2_NO_CDC scope).
--
--   Until that migration lands:
--     • DO NOT apply this file in production.
--     • In local testing, it can be applied against a synthetic
--       `tracer_eval_logger` table that mirrors the prod schema (the
--       legacy fields `_peerdb_is_deleted`, `_peerdb_version` are
--       included for cross-cluster compat).
--
--   When EvalLogger migrates to CH 25.3:
--     1. Drop the `_peerdb_*` columns from this MV's SELECT.
--     2. Change the engine on `tracer_eval_logger` to match the new
--        CH 25.3 ReplacingMergeTree(_version, is_deleted) shape.
--     3. Re-apply this file via apply_schema.py.
--
-- What it powers:
--   The eval-metrics dashboard query "for each eval config, what's the
--   per-hour pass rate?" used to scan millions of `tracer_eval_logger`
--   rows. With this rollup, it reads pre-aggregated state.
--
-- Dashboard read pattern:
--   SELECT custom_eval_config_id, hour,
--          sumMerge(passes) / countMerge(n) AS pass_rate,
--          quantilesTDigestMerge(0.5, 0.95, 0.99)(score_q) AS score_pct
--   FROM   eval_per_config FINAL
--   WHERE  hour >= now() - INTERVAL 7 DAY
--   GROUP  BY custom_eval_config_id, hour;
-- =============================================================================

CREATE TABLE IF NOT EXISTS eval_per_config
(
    hour                     DateTime('UTC'),
    custom_eval_config_id    UUID,

    n                        AggregateFunction(count),
    passes                   AggregateFunction(sumIf, UInt64, UInt8),
    fails                    AggregateFunction(sumIf, UInt64, UInt8),
    errors                   AggregateFunction(countIf, UInt8),
    score_q                  AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Float64),

    INDEX idx_config_id custom_eval_config_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (custom_eval_config_id, hour)
TTL toDateTime(hour) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

-- The MV is commented OUT until EvalLogger migrates to CH 25.3.
-- See header for the activation steps.
--
-- CREATE MATERIALIZED VIEW IF NOT EXISTS eval_per_config_mv
-- TO eval_per_config
-- AS
-- SELECT
--     toStartOfHour(created_at)                         AS hour,
--     custom_eval_config_id,
--     countState()                                       AS n,
--     sumIfState(toUInt64(1), output_bool = 1)           AS passes,
--     sumIfState(toUInt64(1), output_bool = 0)           AS fails,
--     countIfState(error = 1)                            AS errors,
--     quantilesTDigestState(0.5, 0.95, 0.99)(output_float) AS score_q
-- FROM tracer_eval_logger
-- WHERE is_deleted = 0 AND custom_eval_config_id IS NOT NULL
-- GROUP BY hour, custom_eval_config_id;
