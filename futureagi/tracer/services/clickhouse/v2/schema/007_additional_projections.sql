-- =============================================================================
-- 007 — Additional projections at finer aggregation granularities
-- =============================================================================
--
-- Rationale: CH 25.x projection auto-routing requires the query's GROUP BY
-- to EXACTLY match the projection's GROUP BY (not a subset). The existing
-- `proj_metrics_hourly` aggregates by (project_id, hour, observation_type,
-- status, model, provider) — 6 dimensions. Dashboard queries that group on
-- just (hour) or (hour, model) miss the projection and full-scan the parts.
--
-- At trillion-row scale, a missed projection means scanning ~B rows even
-- after partition + primary-key pruning. With these finer projections, the
-- common dashboard queries become projection hits that read ~K pre-
-- aggregated rows.
--
-- Cost: each projection stores additional aggregated state per part. The
-- per-projection storage is dominated by the cardinality of its GROUP BY
-- (hour × ~20 models × 100 projects = 2.4 M rows per month at most),
-- which is tiny compared to the raw rows. Merge cost grows linearly.
--
-- See DECISIONS #025 in internal-docs/clickhouse-analytics/migration-to-ch25/
-- for the full investigation that surfaced this issue via local EXPLAIN.

-- Hour x project_id only — for "give me spans per hour for this project"
ALTER TABLE spans
ADD PROJECTION IF NOT EXISTS proj_metrics_hourly_by_project
(
    SELECT
        project_id,
        toStartOfHour(start_time) AS hour,
        countState(),
        sumState(cost),
        sumState(total_tokens),
        sumState(prompt_tokens),
        sumState(completion_tokens),
        quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
    GROUP BY project_id, hour
);

-- Hour x observation_type — for "per-span-type breakdown across all projects"
ALTER TABLE spans
ADD PROJECTION IF NOT EXISTS proj_metrics_hourly_by_obs_type
(
    SELECT
        project_id,
        toStartOfHour(start_time) AS hour,
        observation_type,
        countState(),
        sumState(cost),
        sumState(total_tokens),
        quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
    GROUP BY project_id, hour, observation_type
);

-- Hour x model — for "model usage / cost breakdown" (eval dashboards)
ALTER TABLE spans
ADD PROJECTION IF NOT EXISTS proj_metrics_hourly_by_model
(
    SELECT
        project_id,
        toStartOfHour(start_time) AS hour,
        model,
        countState(),
        sumState(cost),
        sumState(total_tokens),
        sumState(prompt_tokens),
        sumState(completion_tokens),
        quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
    GROUP BY project_id, hour, model
);

-- Materialize the projection data for existing parts (no-op for fresh table).
ALTER TABLE spans MATERIALIZE PROJECTION proj_metrics_hourly_by_project;
ALTER TABLE spans MATERIALIZE PROJECTION proj_metrics_hourly_by_obs_type;
ALTER TABLE spans MATERIALIZE PROJECTION proj_metrics_hourly_by_model;
