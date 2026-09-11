-- =============================================================================
-- 018 — trace_sessions: the EXTERNAL-IDENTITY half of TraceSession (RMT) + dict
-- =============================================================================
--
-- WHY: TraceSession is split three ways by source-of-truth (DESIGN §5):
--   1. Facts            → `spans_per_session` (008, done) — derived analytics.
--   2. External identity→ THIS table — the immutable, ingestion-sourced session
--      id (`external_session_id`, the OTel session id) + `first_seen`.
--   3. User overlay     → PG `trace_session_overlay` (bookmarked / display_name),
--      written ONLY by the UI — a tiny tier-3 relational dimension.
--
-- The split untangles the dual-role `name` column on the PG `trace_session`
-- table, which today is BOTH the get_or_create match key (= external session id)
-- AND a user-editable display label — the source of the rename → duplicate-
-- session bug (DESIGN §2.5 / §5.1). Here `external_session_id` is the identity
-- ONLY; the editable display name lives in the PG overlay.
--
-- ENGINE / TTL / DB-AGNOSTIC: identical rationale to `017_end_users.sql` — a
-- stream-fed ReplacingMergeTree(version) with latest-wins, no TTL (entities
-- outlive the 90d span TTL), unqualified names + DB-less dict SOURCE so
-- apply_schema's --ch-database is the single switch.
--
-- IDENTITY (P3a): `trace_session_id` is a STRAIGHT MIRROR of PG
-- `trace_session.id`, and `external_session_id` = PG `trace_session.name` (its
-- value at backfill time — for never-renamed sessions this IS the external id;
-- for already-renamed ones it is the display label, which P3a faithfully
-- mirrors). The deterministic UUIDv5 re-key from `external_session_id`, the
-- rename carve-out, and the many-to-one collapse (DESIGN §3.1 / §5.1) are P3b
-- and are intentionally NOT done here.
-- =============================================================================

-- ─── trace_sessions — external session identity (CH-native) ─────────────────
-- ORDER BY (project_id, trace_session_id): id is globally unique so dedup
-- collapses to one row per session; project prefix co-locates a tenant.
-- No organization_id column — the PG `trace_session` table is keyed by project
-- only (its natural key is (project, name)), so the session identity carries no
-- org (DESIGN §5).
CREATE TABLE IF NOT EXISTS trace_sessions
(
    project_id          UUID,
    trace_session_id    UUID,
    external_session_id String                  DEFAULT '',
    first_seen          DateTime64(6, 'UTC'),
    version             DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
    is_deleted          UInt8                   DEFAULT 0,

    INDEX idx_trace_session_id trace_session_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, trace_session_id)
SETTINGS index_granularity = 8192;

-- ─── trace_sessions_dict — in-memory dictionary over trace_sessions ─────────
-- Mirrors `end_users_dict` (017) and the legacy `trace_session_dict` shape +
-- tuning (COMPLEX_KEY_HASHED, LIFETIME 60-120s), sourced from the v2
-- `trace_sessions` table instead of the CDC `trace_session` landing table.
-- Exposes `external_session_id` so the CH list/detail builders can read the
-- session's external id from the dict and stop hardcoding session_name=None /
-- back-filling `name` from PG (DESIGN §5.2).
--
-- SOURCE: TABLE form with a `WHERE 'is_deleted = 0'` predicate (NOT QUERY, NOT
-- FINAL) — same verified rationale as `end_users_dict` in 017: a CLICKHOUSE
-- QUERY source resolves unqualified names in `default`, not the dict's own DB,
-- so only the TABLE form is DB-agnostic; the WHERE clause is honored by the
-- TABLE source (CH 25.3, verified on ch_test) and gives the tombstone drop;
-- dropping FINAL matches the legacy/015 dict convention. `external_session_id`
-- is a non-null String with a DEFAULT because the source column is non-null.
CREATE DICTIONARY IF NOT EXISTS trace_sessions_dict
(
    trace_session_id    UUID,
    project_id          UUID,
    external_session_id String        DEFAULT '',
    is_deleted          UInt8         DEFAULT 0
)
PRIMARY KEY trace_session_id
SOURCE(CLICKHOUSE(TABLE 'trace_sessions' WHERE 'is_deleted = 0'))
LIFETIME(MIN 60 MAX 120)
LAYOUT(COMPLEX_KEY_HASHED());
