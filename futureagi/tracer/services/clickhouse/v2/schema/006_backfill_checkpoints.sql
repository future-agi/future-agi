-- =============================================================================
-- 006 — backfill_checkpoints (resumability state for scripts/backfill_pg_to_ch.py)
-- =============================================================================
--
-- One row per (project_id, hour_bucket) window. The backfill orchestrator:
--   1. Discovers windows by querying PG once: SELECT project_id, date_trunc('hour', start_time)
--   2. For each window not in status='completed', claims it (insert with status='in_progress'),
--      streams matching PG rows, converts, batches into CH `spans`, then writes a final
--      row with status='completed' | 'failed_validation'.
--   3. The orchestrator can be killed at any time. On restart it re-discovers windows
--      and re-claims any that aren't 'completed'. Because the CH target is
--      ReplacingMergeTree on (_version, is_deleted), re-inserting the same rows is
--      idempotent — the checkpoint is a SPEED optimization (skip done work), not a
--      CORRECTNESS gate. Even with a corrupted checkpoint, a re-run yields the same
--      final dataset (DECISIONS #013).
--
-- Design notes:
--   • ReplacingMergeTree(_version) so status transitions UPSERT cleanly:
--       in_progress → completed (final row wins by max _version)
--   • PARTITION BY toYYYYMM(hour_bucket) — natural slicing matches data partitioning
--     of `spans` itself.
--   • ORDER BY puts project_id first so per-tenant catch-up is a prefix scan.
--   • dead_letter_count is denormalized for the operator status dashboard. The
--     authoritative source for triage is the spans_v2_dead_letter table itself.
--   • error_message holds a short class+summary if the window ended in
--     'failed_validation' or 'failed'. Don't dump a full traceback here — those go
--     to structured logs.
--
-- Operator queries:
--   -- Overall progress
--   SELECT status, count() FROM backfill_checkpoints FINAL GROUP BY status;
--
--   -- Top 10 slow windows still in progress
--   SELECT project_id, hour_bucket, rows_in_pg, started_at
--   FROM backfill_checkpoints FINAL
--   WHERE status = 'in_progress'
--   ORDER BY started_at ASC LIMIT 10;
--
--   -- Windows that failed validation (need triage)
--   SELECT project_id, hour_bucket, rows_in_pg, rows_in_ch, dead_letter_count, error_message
--   FROM backfill_checkpoints FINAL
--   WHERE status = 'failed_validation';
-- =============================================================================

CREATE TABLE IF NOT EXISTS backfill_checkpoints
(
    project_id         UUID,
    hour_bucket        DateTime('UTC'),
    status             LowCardinality(String),  -- in_progress | completed | failed_validation | failed
    rows_in_pg         UInt64               DEFAULT 0,
    rows_in_ch         UInt64               DEFAULT 0,
    dead_letter_count  UInt64               DEFAULT 0,
    started_at         DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
    finished_at        Nullable(DateTime64(3, 'UTC')),
    worker_id          String               DEFAULT '',
    backfill_run_id    String               DEFAULT '',
    error_message      String               DEFAULT '',
    _version           UInt64               DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC'))
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(hour_bucket)
ORDER BY (project_id, hour_bucket)
SETTINGS index_granularity = 8192;
