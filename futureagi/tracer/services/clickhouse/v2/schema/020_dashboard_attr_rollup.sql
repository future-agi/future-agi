-- =============================================================================
-- 020 — Dashboard attribute rollup (latency × low-cardinality attribute)
-- =============================================================================
--
-- Same chained incremental aggregate-state MV shape as 010 / 016 (see
-- MV_STRATEGY.md). Powers ONE dashboard read path: "latency average over root
-- spans, broken down by a low-cardinality custom attribute" (final_status,
-- country). That widget groups the fat `attrs_string` Map across tens of
-- millions of root spans on every page-load and times out; this rollup
-- pre-aggregates it so the read is a tiny scan of pre-grouped state.
--
-- Why sum + count (NOT avg) as the carried metric:
--   The dashboard serves hour/day/week/month buckets by RE-aggregating hourly
--   rows. An avg-of-avgs is wrong across buckets of unequal row counts. Carrying
--   sumState(latency_ms) + countState() lets the read compute an EXACT
--   count-weighted mean — sumMerge(latency_sum) / countMerge(n) — that equals
--   avg(latency_ms) over the same raw rows at ANY bucket size. This is the
--   property that makes the rollup numbers identical to the raw scan.
--
-- Why row-REDUCING / safe (vs the old row-expanding spans_mv that OOMed):
--   The MV ARRAY JOINs a FIXED, tiny key list (covered attrs only) — not the
--   whole Map — and GROUP BYs, so it reads N batch rows and writes
--   <= N × (#covered keys) rows. Memory is bounded by distinct
--   (hour, attr_key, attr_value) per batch, far smaller than the batch.
--
-- Root spans only: the dashboard latency metric is defined over root spans
--   (parent_span_id = ''), matching the `(parent_span_id IS NULL OR
--   parent_span_id = '')` predicate the spans path applies. On the v2 schema
--   parent_span_id is `String DEFAULT ''` (002_spans_v2.sql), so the root test
--   is `parent_span_id = ''`.
--
-- Covered attribute keys: kept to a fixed low-cardinality set. Adding a key is
--   a deliberate change — extend the ARRAY JOIN list here AND the COVERED set in
--   the dashboard query builder, in a new numbered schema file (this one is
--   append-only once applied — see apply_schema.py DECISIONS #004).
--
-- Dashboard read pattern (the builder's covered-breakdown fast-path):
--   SELECT toStartOfDay(hour)              AS time_bucket,
--          attr_value                      AS breakdown_value,
--          sumMerge(latency_sum) / countMerge(n) AS value
--   FROM   dashboard_attr_rollup
--   WHERE  project_id IN ? AND attr_key = ? AND hour >= ? AND hour < ?
--   GROUP  BY time_bucket, breakdown_value
--   ORDER  BY time_bucket, breakdown_value;
-- =============================================================================

CREATE TABLE IF NOT EXISTS dashboard_attr_rollup
(
    project_id   UUID,
    hour         DateTime('UTC'),
    attr_key     LowCardinality(String),
    attr_value   String,

    n            AggregateFunction(count),
    -- sum of latency_ms; toInt64 upcast (Int32 raw → Int64 state) so the
    -- SELECT's state element type matches this column — CH refuses the
    -- implicit AggregateFunction(sum, Int32) → (sum, Int64) conversion on
    -- insert (same rationale as 008/010/016).
    latency_sum  AggregateFunction(sum, Int64),

    INDEX idx_attr_value attr_value TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour, attr_key, attr_value)
TTL toDateTime(hour) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS dashboard_attr_rollup_mv
TO dashboard_attr_rollup
AS
SELECT
    project_id,
    toStartOfHour(start_time)            AS hour,
    attr_key,
    -- Breakdown value must match the spans path EXACTLY. The system-metric
    -- breakdown path (_build_system_metric_query → _resolve_all_breakdowns)
    -- emits the BARE Map subscript span_attr_str[k] (→ attrs_string[k] after the
    -- v2 rewrite), so a missing / empty attribute groups under the empty-string
    -- '' bucket — not dropped, not relabelled. (A different method,
    -- _build_breakdown_clause, wraps empties as '(not set)', but the path this
    -- rollup replaces does not call it — so neither does the rollup.)
    attrs_string[attr_key]               AS attr_value,
    countState()                         AS n,
    sumState(toInt64(latency_ms))        AS latency_sum
FROM spans
-- ARRAY JOIN over the FIXED covered key set — extracts only these keys from the
-- Map (not the whole Map), keeping the MV row-reducing. attr_key is the joined
-- element; attrs_string[attr_key] reads that one Map entry per covered key.
ARRAY JOIN ['final_status', 'country'] AS attr_key
WHERE is_deleted = 0
  AND parent_span_id = ''
-- Use the explicit expressions (not the `hour` / `attr_value` aliases) to
-- side-step the CH 25.x analyzer quirk that fails to recognize SELECT aliases
-- in GROUP BY (see 010 and DECISIONS #020).
GROUP BY project_id, toStartOfHour(start_time), attr_key, attrs_string[attr_key];
