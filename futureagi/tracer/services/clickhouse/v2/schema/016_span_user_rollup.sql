-- =============================================================================
-- 016 — span_user_rollup: EndUser as a CH-DERIVED hot dimension
-- =============================================================================
--
-- Per SCALE_ARCHITECTURE.md §1.1 (three-tier model): `EndUser` is a tier-2
-- "hot/large dimension" — high-cardinality at scale AND get_or_create'd on the
-- ingest hot path today (a synchronous PG write per trace). Its key
-- (`end_user_id`) is already denormalized onto every span, so the *entity* —
-- existence, first/last seen, trace count, token/cost/latency usage — is a
-- pure CH aggregation over spans. No PG table, no dual-write: it is DERIVED.
--
-- This is the exact mirror of `spans_per_session` (008), keyed by
-- `end_user_id` instead of `trace_session_id`, plus a distinct-trace count
-- (the headline per-user metric: how many traces/conversations a user drove).
--
-- Curated/mutable EndUser fields (user_id label, tags, custom metadata) are NOT
-- derivable and are handled separately by a thin `end_users` RMT + dict (later
-- in Phase 1); this rollup covers the derived analytics only.
-- =============================================================================

CREATE TABLE IF NOT EXISTS span_user_rollup
(
    project_id            UUID,
    end_user_id           UUID,
    hour_first_seen       DateTime('UTC'),

    span_count            AggregateFunction(count),
    -- distinct traces per user — the headline metric. uniq (HLL) is fine here:
    -- per-user trace counts are an analytics figure, not pagination math.
    trace_count           AggregateFunction(uniq, String),
    total_tokens_sum      AggregateFunction(sum, Int64),
    prompt_tokens_sum     AggregateFunction(sum, Int64),
    completion_tokens_sum AggregateFunction(sum, Int64),
    cost_sum              AggregateFunction(sum, Float64),
    latency_q             AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Int32),
    first_seen            AggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen             AggregateFunction(max, Nullable(DateTime64(6, 'UTC'))),
    error_count           AggregateFunction(countIf, UInt8),

    INDEX idx_end_user_id end_user_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour_first_seen)
ORDER BY (project_id, end_user_id, hour_first_seen)
TTL toDateTime(hour_first_seen) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS span_user_rollup_mv
TO span_user_rollup
AS
SELECT
    project_id,
    end_user_id,
    toStartOfHour(min(start_time))                          AS hour_first_seen,
    countState()                                            AS span_count,
    uniqState(toString(trace_id))                           AS trace_count,
    sumState(toInt64(total_tokens))                         AS total_tokens_sum,
    sumState(toInt64(prompt_tokens))                        AS prompt_tokens_sum,
    sumState(toInt64(completion_tokens))                    AS completion_tokens_sum,
    sumState(cost)                                          AS cost_sum,
    quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)      AS latency_q,
    minState(start_time)                                    AS first_seen,
    maxState(end_time)                                      AS last_seen,
    countIfState(status = 'ERROR')                          AS error_count
FROM spans
WHERE is_deleted = 0 AND end_user_id IS NOT NULL
GROUP BY project_id, end_user_id;
