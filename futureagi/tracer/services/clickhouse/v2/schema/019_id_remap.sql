-- =============================================================================
-- 019 — id remap: old (random PG uuid4) → new (deterministic UUIDv5) surrogate
--       ids for the CURATED end_users / trace_sessions dimensions
-- =============================================================================
--
-- WHY: P3b re-keys the EndUser / TraceSession surrogate id from a random PG
-- `uuid4` to a DETERMINISTIC `UUIDv5(natural_key)` (DESIGN §3) so ingestion can
-- drop the hot-path `get_or_create`. But pre-cutover spans / Scores / annotations
-- already carry the OLD random ids, while new spans carry the deterministic ids —
-- the same identity would split across the cutover. These two tables are the
-- bounded bridge: ONE row per historical entity (NOT per span — entities are
-- tiny: ≈879 endusers / low-thousands of sessions, vs billions of spans), so a
-- read spanning the cutover resolves `old_id → new_id` through this map instead of
-- rewriting billions of span rows. The heavy span-rewrite stays optional /
-- never-done — the map alone is enough for joins (DESIGN §3, §10.1).
--
-- POPULATION: `ch25_build_id_remap` iterates PG `tracer_enduser` / `trace_session`,
-- computes `new_id` via `deterministic_id.deterministic_*` (the SAME functions the
-- future ingestion path uses), and inserts `(old_id, new_id, version=now())`.
-- The map is MANY-TO-ONE by construction (the deterministic id consolidates the
-- NULL-`user_id_type` enduser dupes — 879→544 on box data — and the rename-bug
-- duplicate sessions): several `old_id`s legitimately point at one `new_id`. ORDER
-- BY `old_id` (the lookup direction: a span's stored old id → its new id) keeps the
-- key unique-per-old-row so RMT dedups re-runs cleanly.
--
-- ENGINE: ReplacingMergeTree(version). Idempotent — re-running the build is a
-- latest-wins no-op (same `old_id` → same `new_id`, fresher `version` wins the
-- merge but the value is identical). `version` is `DateTime64(6,'UTC')` to match
-- the curated RMTs (017/018), not the integer-ns `_version` of `traces`.
--
-- TTL: NONE — and this is LOAD-BEARING, not just the tiny-table convention. The
-- remap MUST OUTLIVE the 90d span TTL: a Score / annotation can reference a span
-- whose row has aged out, and the read still resolves that span's `old` user /
-- session id THROUGH this map to the surviving `new` id. A TTL here would silently
-- break cross-cutover resolution once the oldest spans expire. (Same TTL-vs-
-- lifetime rule as the curated RMTs and the Score store — DESIGN §6.)
--
-- DB-AGNOSTIC: table names are UNQUALIFIED so apply_schema's --ch-database is the
-- single switch for dev / test (ch_test) / prod — matches 015 / 017 / 018.
-- =============================================================================

-- ─── end_user_id_remap — old random EndUser id → deterministic id ────────────
-- ORDER BY old_id: the resolve direction is "a span carries the old id → find its
-- new id", and old_id is unique per historical PG row, so it is the natural RMT
-- dedup key. new_id is many-to-one (consolidated identities share one new_id).
CREATE TABLE IF NOT EXISTS end_user_id_remap
(
    old_id   UUID,
    new_id   UUID,
    version  DateTime64(6, 'UTC')  DEFAULT now64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY old_id
SETTINGS index_granularity = 8192;

-- ─── trace_session_id_remap — old random TraceSession id → deterministic id ──
-- Identical shape / rationale to end_user_id_remap. Many-to-one here too: the
-- rename bug (DESIGN §2.5) minted multiple TraceSession rows for one external id,
-- so several old_ids collapse onto one new_id. (Step1 builds the UNIFORM map for
-- every row; the §3.1 rename carve-out — renamed sessions keeping their old id —
-- is the P3b-step3 consolidation sweep, NOT this additive build.)
CREATE TABLE IF NOT EXISTS trace_session_id_remap
(
    old_id   UUID,
    new_id   UUID,
    version  DateTime64(6, 'UTC')  DEFAULT now64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY old_id
SETTINGS index_granularity = 8192;
