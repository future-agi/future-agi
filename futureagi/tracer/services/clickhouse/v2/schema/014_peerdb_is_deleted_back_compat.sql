-- 014 — `_peerdb_is_deleted` ALIAS for back-compat with legacy CDC queries.
--
-- The v2 typed-JSON `spans` table uses `is_deleted UInt8` (see
-- 002_spans_v2.sql) as the soft-delete column. The pre-cutover code paths
-- (and any external integration that still references the legacy column
-- name) read `_peerdb_is_deleted` because that was the PeerDB-managed
-- column on the old CDC-mirror `spans` table.
--
-- During the CH25 migration close-out (2026-05-27) we rewrote every
-- production query builder to use `is_deleted` directly. To stay safe for
-- anything we missed — third-party SDK queries, custom dashboards, ad-hoc
-- analytics — expose `_peerdb_is_deleted` as a true ALIAS column. Reads
-- resolve to `is_deleted` at query time; writes ignore it (ALIAS columns
-- are not persisted, so no storage cost and no INSERT contract change).
--
-- Why ALIAS over MATERIALIZED:
--   • ALIAS is query-time only; no backfill needed for existing rows.
--   • MATERIALIZED writes a new physical column at INSERT time, which
--     would require backfilling every existing row and double-writing on
--     every new INSERT.
-- The ALIAS form gives back-compat at zero cost as long as nothing tries
-- to ORDER/GROUP/PREWHERE by `_peerdb_is_deleted` — which would force CH
-- to compute the alias for every row. The legacy queries we kept on
-- `_peerdb_is_deleted` use it only in WHERE, which CH plans efficiently.

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS _peerdb_is_deleted UInt8 ALIAS is_deleted;

-- CH 25.3 bug interaction (root cause now fixed in 002, not here):
-- a minmax skip-index on this non-stored ALIAS column wedges the whole
-- table on load (`Code: 49 ... auto_minmax_index__peerdb_is_deleted
-- already exists` → ATTACH fails → every read Code 722). That index was
-- only ever created by the table setting
-- `add_minmax_index_for_numeric_columns = 1`, which 002 no longer sets —
-- numeric minmax indexes are declared explicitly there, excluding this
-- alias. So no index is created for `_peerdb_is_deleted` and there is
-- nothing to drop here. (A previous revision dropped the index in this
-- file; that was insufficient — a later metadata-rewriting ALTER under
-- concurrent multi-worker apply re-baked it. Removing the setting is the
-- robust fix: the index can never exist.)
