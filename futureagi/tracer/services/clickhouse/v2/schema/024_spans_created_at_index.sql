-- =============================================================================
-- 024 — created_at minmax skip index on spans (arrival-time pruning)
-- =============================================================================
--
-- Continuous eval-task reconcile floors on created_at (arrival), not start_time.
-- spans is PARTITION BY toDate(start_time), so `created_at >= floor` prunes
-- nothing and each poll scans project history. This minmax index prunes by
-- created_at (late arrivals land in new, recent-created_at parts regardless of
-- start_time partition). Mirrors the traces table (015:69).
--
-- ADD INDEX is instant; only parts written after it are indexed on insert/merge.
--
-- ============================ DEPLOY STEP =====================================
-- MATERIALIZE (backfills existing parts) is a full-table mutation, kept out of
-- the applier. Run by hand per region, off-peak, before relying on the floor at
-- scale (add the cluster clause on replicated US; abort via KILL MUTATION):
--
--   ALTER TABLE spans MATERIALIZE INDEX auto_minmax_index_created_at;
-- =============================================================================

ALTER TABLE spans
    ADD INDEX IF NOT EXISTS auto_minmax_index_created_at created_at
    TYPE minmax() GRANULARITY 1;
