-- Pre-aggregated rollup for the dashboard "latency avg, broken down by a
-- low-cardinality attribute" read path. Same MV shape as 010 / 016.
-- Covered attr_keys: final_status, country only — other breakdowns fall back
-- to the spans scan (router gate in query_builders/dashboard.py).
-- No TTL: this aggregate hangs off spans, which 020_remove_ttls retains
-- indefinitely (retention is enforced per-org by ee/usage, not CH TTL).

CREATE TABLE IF NOT EXISTS dashboard_attr_rollup
(
    project_id   UUID,
    hour         DateTime('UTC'),
    attr_key     LowCardinality(String),
    attr_value   String,
    n            AggregateFunction(count),
    latency_sum  AggregateFunction(sum, Int64),

    INDEX idx_attr_value attr_value TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour, attr_key, attr_value)
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS dashboard_attr_rollup_mv
TO dashboard_attr_rollup
AS
SELECT
    project_id,
    toStartOfHour(start_time)            AS hour,
    attr_key,
    attrs_string[attr_key]               AS attr_value,
    countState()                         AS n,
    sumState(toInt64(latency_ms))        AS latency_sum
FROM spans
ARRAY JOIN ['final_status', 'country'] AS attr_key
WHERE is_deleted = 0
  AND parent_span_id = ''
GROUP BY project_id, toStartOfHour(start_time), attr_key, attrs_string[attr_key];
