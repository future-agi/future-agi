-- =============================================================================
-- 021 — Projections for session-keyed and user-keyed span lookups (TH-6237)
-- =============================================================================
--
-- The sessions tab fires multiple queries filtering by trace_session_id IN (...)
-- on the spans table: content (first/last message), end-user resolution, and
-- span-attribute enrichment. The base table ORDER BY starts with
-- (project_id, observation_type, service_name, hour) so trace_session_id is
-- only reachable via the bloom_filter skip index — a scatter read across all
-- granules that pass the bloom test.
--
-- Similarly, the Users tab scans raw spans filtered by end_user_id which is
-- also only bloom-indexed, not in the primary key.
--
-- These projections store the same data in an alternative sort order so the
-- CH optimizer can do a prefix scan for session-keyed and user-keyed queries.
-- With optimize_use_projections=1 (already in _V2_REQUIRED_SETTINGS) the
-- optimizer auto-selects the projection when the query's filter/order matches.
-- =============================================================================

-- Session-keyed lookup: covers build_content_query, _fetch_end_user_info,
-- build_span_attributes_query, and _retrieve_clickhouse detail queries.
ALTER TABLE spans
ADD PROJECTION IF NOT EXISTS proj_by_session
(
    SELECT *
    ORDER BY (project_id, trace_session_id, start_time)
);

-- User-keyed lookup: covers the UserListQueryBuilder raw_spans scan
-- when end_user_id IN (...) filter is pushed down.
ALTER TABLE spans
ADD PROJECTION IF NOT EXISTS proj_by_end_user
(
    SELECT *
    ORDER BY (project_id, end_user_id, start_time)
);

ALTER TABLE spans MATERIALIZE PROJECTION proj_by_session;
ALTER TABLE spans MATERIALIZE PROJECTION proj_by_end_user;
