-- =============================================================================
-- 017 — end_users: the CURATED EndUser dimension (RMT) + end_users_dict
-- =============================================================================
--
-- WHY: `016_span_user_rollup` covers the DERIVED EndUser analytics (counts,
-- tokens, cost, latency) — a pure aggregation over spans. But the *curated*
-- EndUser fields are NOT derivable from spans: `user_id` (the external OTel
-- `user.id` label), `user_id_type` (normalized email/phone/uuid/custom),
-- `user_id_hash` (SDK pass-through of `user.id.hash`), and `metadata`. Today
-- those reach ClickHouse only via the legacy PeerDB CDC table `tracer_enduser`
-- → `enduser_dict` (schema.py). v2 removes CDC, so the curated entity needs a
-- CH-native home. This is it: a stream-fed ReplacingMergeTree plus an in-memory
-- dictionary for point lookups by `end_user_id` (DESIGN §4.1).
--
-- ENGINE: ReplacingMergeTree(version). The entity is fed from the ingest stream
-- with versioned latest-wins state — RMT's sweet spot (a label cache where
-- `metadata` can change; `version` = ingest/edit time gives natural latest-wins,
-- mirroring `traces` and `spans`). A refreshable MV would be for periodic full
-- rescans of external reference data; this is not that.
--
-- IDENTITY (P3a): `end_user_id` is a STRAIGHT MIRROR of PG `tracer_enduser.id`
-- (the random PG-minted uuid4). The deterministic UUIDv5 re-keying from the
-- natural key — and the 879→544 consolidation it implies (DESIGN §3 / §3.1) —
-- is P3b and is intentionally NOT done here. This keeps `end_user_id` byte-
-- identical to the value already denormalized onto every span, so the dual-write
-- introduced in P3a stays keyed exactly like the spans and the legacy dict.
--
-- DB-AGNOSTIC: table names are UNQUALIFIED so apply_schema's --ch-database is the
-- single switch for dev / test / prod. The dict SOURCE omits the DB clause for
-- the same reason — a CLICKHOUSE source resolves an unqualified table in the
-- dict's own database (verified against `trace_dict` in 015 on a non-default DB).
--
-- TTL: NONE. The entity is tiny and must OUTLIVE the 90d span TTL — a Score or
-- annotation can reference a TTL'd span's user, and reads must still resolve the
-- label. Same TTL-vs-lifetime rule as the Score store (DESIGN §6).
-- =============================================================================

-- ─── end_users — curated EndUser dimension (CH-native) ──────────────────────
-- ORDER BY (project_id, end_user_id): end_user_id is globally unique so dedup
-- collapses to one row per user; the project prefix co-locates a tenant's users.
-- `user_id_type` is Nullable here because the SDK leaves it NULL on ~85% of prod
-- rows (it's derived only when the SDK can normalize the id) — making it the
-- single source of truth the dict reads from, NULL must round-trip.
CREATE TABLE IF NOT EXISTS end_users
(
    project_id       UUID,
    end_user_id      UUID,
    organization_id  UUID,
    user_id          String,
    user_id_type     LowCardinality(Nullable(String)),
    user_id_hash     String                  DEFAULT '',
    metadata         String                  DEFAULT '{}' CODEC(ZSTD(1)),
    first_seen       DateTime64(6, 'UTC'),
    version          DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
    is_deleted       UInt8                   DEFAULT 0,

    INDEX idx_end_user_id end_user_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, end_user_id)
SETTINGS index_granularity = 8192;

-- ─── end_users_dict — in-memory dictionary over end_users ───────────────────
-- Mirrors the legacy `enduser_dict` shape + tuning (COMPLEX_KEY_HASHED,
-- LIFETIME 60-120s) but: (a) sourced from the v2 `end_users` table (not the CDC
-- `tracer_enduser` landing table), and (b) ALSO exposes `user_id_hash` — which
-- the legacy dict omits, forcing `_fetch_end_user_info` to round-trip to PG for
-- the hash. Exposing it here lets that read drop PG entirely (DESIGN §4.3).
--
-- SOURCE uses the TABLE form with a WHERE predicate — NOT the QUERY form, and
-- NOT FINAL. This deviates from the DESIGN §4.1 literal (which showed a QUERY
-- with `FINAL WHERE is_deleted = 0`) for a verified resolution reason:
--   • DB-agnostic resolution REQUIRES the TABLE form. A CLICKHOUSE *QUERY*
--     source runs with the server's default database (`default`), so an
--     unqualified `FROM end_users` in a QUERY resolves to `default.end_users`,
--     NOT the dict's own database — the dict silently loads 0 rows when applied
--     to any non-`default` DB (proven on ch_test: QUERY-form → element_count 0
--     while the table held the rows; TABLE-form → element_count 2). The legacy
--     `enduser_dict` only dodged this by f-string-qualifying `{_CH_DATABASE}.`;
--     a static .sql can't, so TABLE-form is the only DB-agnostic option (same
--     reason 015's `trace_dict` uses `SOURCE(CLICKHOUSE(TABLE 'traces'))`).
--   • The `WHERE 'is_deleted = 0'` clause IS honored by the TABLE source
--     (verified on CH 25.3 — loads only live rows), giving the tombstone drop
--     without needing a QUERY.
--   • Dropping FINAL matches BOTH the legacy `enduser_dict` and 015 `trace_dict`
--     (neither uses FINAL): a not-yet-merged duplicate is harmless for a label
--     cache, and the 60-120s LIFETIME re-reads after background merges settle.
--
-- `user_id_type` is Nullable(String) to load cleanly: a dictionary attribute's
-- DEFAULT applies only to a MISSING dictGet key, NOT to a NULL value read from
-- the source — a non-nullable attr would fail the load with "Cannot convert NULL
-- value to non-Nullable type" on the ~85% NULL-type rows (the exact 015
-- `trace_dict.name` lesson). `user_id_hash`/`metadata` are non-null String with
-- a DEFAULT because the source column is non-null String (the backfill/collector
-- coerce PG NULL → '' / '{}').
--
-- IF NOT EXISTS (not OR REPLACE): unlike the legacy `enduser_dict`, this is a
-- brand-new name with no stale prior definition to self-heal, so the plain
-- idempotent create is sufficient and safe across boots.
CREATE DICTIONARY IF NOT EXISTS end_users_dict
(
    end_user_id      UUID,
    project_id       UUID,
    organization_id  UUID,
    user_id          String,
    user_id_type     Nullable(String),
    user_id_hash     String        DEFAULT '',
    metadata         String        DEFAULT '{}',
    is_deleted       UInt8         DEFAULT 0
)
PRIMARY KEY end_user_id
SOURCE(CLICKHOUSE(TABLE 'end_users' WHERE 'is_deleted = 0'))
LIFETIME(MIN 60 MAX 120)
LAYOUT(COMPLEX_KEY_HASHED());
