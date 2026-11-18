-- =============================================================================
-- 011 — tracer_eval_logger on CH 25.3 (kill PeerDB columns, align with v2 conventions)
-- =============================================================================
--
-- Replaces the legacy CH 24.10 `tracer_eval_logger` table that was fed by
-- PeerDB CDC from PG. After this migration:
--   • No `_peerdb_*` columns.
--   • Version + soft-delete use the same names as `spans` (`_version`,
--     `is_deleted`) so the unified Score model layer can ReplacingMergeTree-
--     dedupe consistently across both tables.
--   • Writes come from the Django app directly (no CDC) via the existing
--     `tracer.utils.eval` write paths after they're flipped to point at
--     this table (env var `CH25_EVAL_LOGGER_TABLE=tracer_eval_logger_v2`).
--
-- Why a NEW table name (`tracer_eval_logger_v2`) instead of replacing in-place:
--   • The legacy `tracer_eval_logger` is still receiving CDC writes during
--     P0-P5 of the deployment plan. Dropping or renaming it mid-rollout
--     would break the existing read paths that haven't yet flipped to v2.
--   • Once P5 completes and reads have been v2-only for 30 days, the
--     legacy table is dropped in P6 alongside the rest of the CDC teardown.
--   • At that point we can RENAME `tracer_eval_logger_v2 TO tracer_eval_logger`
--     in a single atomic DDL — or just leave the v2 name. Code refers to
--     the table via the `CH25_EVAL_LOGGER_TABLE` env var so the rename is
--     a config flip.
--
-- Backfill path (codex P2 finding 2026-05-26 — TWO corrections vs. prior
-- version of this header):
--
--   1. EXPLICIT target column list. The positional `INSERT ... SELECT` form
--      relies on table order, but the source SELECT below lists deleted /
--      deleted_at BEFORE created_at / updated_at while the target table
--      orders created_at, updated_at, deleted_at, is_deleted, _version.
--      Without an explicit column list the wrong columns receive each value
--      (e.g. NULL deleted_at → non-null updated_at → insert fails). Always
--      name the target columns when the column orders differ.
--
--   2. Read from the legacy table WITH `FINAL` and filter out CDC delete
--      markers via `_peerdb_is_deleted = 0`. The legacy table is a
--      ReplacingMergeTree fed by PeerDB CDC; without FINAL the backfill
--      sends superseded row versions AND tombstoned rows into v2, and
--      because `eval_per_config_mv` is active by the time this runs, the
--      rollup PERMANENTLY over-counts pass/fail/error totals (aggregate MVs
--      can't retract). FINAL is acceptable here because backfill is a
--      one-shot read, not a hot-path query.
--
--   INSERT INTO tracer_eval_logger_v2 (
--       id, trace_id, observation_span_id, trace_session_id, target_type,
--       custom_eval_config_id, eval_type_id,
--       output_bool, output_float, output_str, output_str_list,
--       error, error_message,
--       eval_explanation, output_metadata, results_tags, results_explanation,
--       eval_tags, eval_id, eval_task_id,
--       created_at, updated_at, deleted_at, is_deleted, _version
--   )
--   SELECT
--       id, trace_id, observation_span_id, trace_session_id, target_type,
--       custom_eval_config_id, eval_type_id,
--       output_bool, output_float, output_str, output_str_list,
--       error, error_message,
--       eval_explanation, output_metadata, results_tags, results_explanation,
--       eval_tags, eval_id, eval_task_id,
--       created_at, updated_at, deleted_at,
--       deleted                       AS is_deleted,
--       toUInt64(_peerdb_version)     AS _version
--   FROM tracer_eval_logger FINAL
--   WHERE _peerdb_is_deleted = 0 AND deleted = 0;
-- =============================================================================

CREATE TABLE IF NOT EXISTS tracer_eval_logger_v2
(
    id                   UUID,

    -- Foreign keys (mirror legacy shape; target_type discriminates row kind)
    trace_id             Nullable(UUID),
    observation_span_id  Nullable(String),
    trace_session_id     Nullable(UUID),
    target_type          LowCardinality(String) DEFAULT 'span',
    custom_eval_config_id UUID DEFAULT '00000000-0000-0000-0000-000000000000',
    eval_type_id         Nullable(String),

    -- Results
    output_bool          Nullable(UInt8),
    output_float         Nullable(Float64),
    output_str           Nullable(String),
    output_str_list      String DEFAULT '[]',

    -- Error tracking
    error                UInt8           DEFAULT 0,
    error_message        Nullable(String),

    -- Explanation / metadata
    eval_explanation     Nullable(String),
    output_metadata      String          DEFAULT '{}',
    results_tags         String          DEFAULT '[]',
    results_explanation  String          DEFAULT '{}',
    eval_tags            String          DEFAULT '[]',

    -- Identifiers
    eval_id              Nullable(String),
    eval_task_id         Nullable(String),

    -- Timestamps
    created_at           DateTime64(3, 'UTC'),
    updated_at           DateTime64(3, 'UTC'),
    deleted_at           Nullable(DateTime64(3, 'UTC')),

    -- Provenance / soft-delete — same convention as `spans` table
    is_deleted           UInt8           DEFAULT 0,
    _version             UInt64          DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),

    -- Skip indexes for hot lookup paths
    INDEX idx_trace_id              trace_id              TYPE bloom_filter GRANULARITY 1,
    INDEX idx_observation_span_id   observation_span_id   TYPE bloom_filter GRANULARITY 1,
    INDEX idx_trace_session_id      trace_session_id      TYPE bloom_filter GRANULARITY 1,
    INDEX idx_target_type           target_type           TYPE bloom_filter GRANULARITY 1,
    INDEX idx_custom_eval_config_id custom_eval_config_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toYYYYMM(created_at)
ORDER BY (custom_eval_config_id, created_at, id)
SETTINGS index_granularity = 8192, allow_nullable_key = 1;

-- =============================================================================
-- The per-eval-config rollup MV (009) was commented out because it depended
-- on the legacy CH 24.10 schema with `_peerdb_is_deleted`. Now that
-- `tracer_eval_logger_v2` exists with our v2 conventions, re-create the MV
-- pointing at the new table.
--
-- Activation order:
--   1. Apply this file (creates the v2 table + the MV reading from it).
--   2. Backfill historical data (see header SQL).
--   3. Flip Django app config: CH25_EVAL_LOGGER_TABLE=tracer_eval_logger_v2.
--   4. After 30 days of clean v2-only writes, drop the legacy
--      tracer_eval_logger as part of P6.
-- =============================================================================

-- NOTE (codex P1 finding 2026-05-26): `output_bool` and `output_float` are
-- Nullable; the target `eval_per_config` declares aggregate states over
-- NON-nullable arguments (sumIf<UInt64, UInt8>, quantilesTDigest<Float64>).
-- Without coalescing/filtering, CH rejects inserts of nullable-state
-- aggregates into non-null target columns and the MV silently fails on
-- every row that has any NULL eval field.
--
-- Resolution:
--   • `output_bool = N` is 3-valued; coalesce NULL → 0 (treats null as
--     neither pass nor fail — correct semantics; counts still increment).
--   • `output_float` is gated through `quantilesTDigestStateIf(...)` with
--     an `output_float IS NOT NULL` predicate so null floats are excluded
--     from the percentile state without polluting it with zeros.
--   • `error` is non-nullable (DEFAULT 0), no change needed.
--
-- NOTE (codex follow-up P1 finding 2026-05-26): an earlier version of this
-- file used `CREATE MATERIALIZED VIEW IF NOT EXISTS eval_per_config_mv`,
-- which means CH skips the DDL on any cluster that already created the
-- previous (buggy) MV. The fix above is then a no-op on follow-up upgrades —
-- the broken MV with nullable aggregate expressions persists in production.
--
-- Resolution: explicit DROP+CREATE. Safe because:
--   • The MV writes TO eval_per_config (a separate target table); dropping
--     the MV does NOT drop the rollup data.
--   • After DROP+CREATE, the new MV starts fresh — past inserts into
--     tracer_eval_logger_v2 are already in eval_per_config; new inserts
--     get propagated correctly through the fixed MV.
--   • apply_schema.py is hash-tracked (D-004), so this re-runs on next
--     `--force` apply after the file hash changes.
DROP VIEW IF EXISTS eval_per_config_mv;

CREATE MATERIALIZED VIEW eval_per_config_mv
TO eval_per_config
AS
SELECT
    toStartOfHour(created_at)                              AS hour,
    custom_eval_config_id,
    countState()                                           AS n,
    -- pass = output_bool = 1; fail = output_bool = 0; null = neither
    sumIfState(toUInt64(1), coalesce(output_bool, 255) = 1) AS passes,
    sumIfState(toUInt64(1), coalesce(output_bool, 255) = 0) AS fails,
    countIfState(error = 1)                                AS errors,
    quantilesTDigestStateIf(0.5, 0.95, 0.99)(output_float, output_float IS NOT NULL) AS score_q
FROM tracer_eval_logger_v2
WHERE is_deleted = 0
  AND custom_eval_config_id != '00000000-0000-0000-0000-000000000000'
GROUP BY hour, custom_eval_config_id;
