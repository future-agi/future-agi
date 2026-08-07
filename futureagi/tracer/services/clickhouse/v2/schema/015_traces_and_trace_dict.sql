-- =============================================================================
-- 015 — traces table + trace_dict dictionary + main-faithful trace_name
-- =============================================================================
--
-- WHY: in the no-CDC v2 world the parent `Trace` row has to be queryable from
-- ClickHouse for two reasons:
--
--   (1) trace_name enrichment. main derives a span's trace_name as
--           dictGetOrDefault('trace_dict', 'name', toUUID(s.trace_id), NULL)
--       where `trace_dict` is sourced (via PeerDB CDC) from PG `tracer_trace`.
--       EVERY span — root AND child — therefore carries the *trace's* name.
--       The v2 `012` shim instead derived trace_name from the span's OWN
--       `name` (only the root span's name equals the trace name), so child
--       spans showed their own name — a regression vs main on ~all multi-span
--       traces. We restore main's behaviour by hosting the trace in CH and
--       redefining trace_name to the same dictGet.
--
--   (2) trace-list / trace-detail reads that need trace-level fields
--       (input/output/metadata/tags/status) without round-tripping to PG.
--
-- This does NOT decouple PG. `tracer_observation_span.trace_id` still FKs
-- `tracer_trace`, and spans are not yet cut over, so PG `tracer_trace` cannot
-- be dropped (its own child table references it). PG stays the system of
-- record; CH is a faithful read replica populated by backfill + app-level
-- dual-write (no PeerDB). FK-coupled reads (Score / QueueItem / error-analysis
-- joins) remain on PG until spans cutover unblocks the decouple.
--
-- trace_id FORMAT: PG `tracer_trace.id` is a UUID; the backfill and the
-- collector both land trace_id as the 36-char dashed UUID string in `spans`.
-- `traces.id` is a real UUID and the dict key is UUID, looked up via
-- toUUID(trace_id) — identical to main. (The collector's trace_id is
-- normalised to the dashed form in the same change set; 16-hex span ids are
-- unchanged.)
-- =============================================================================

-- ─── traces — CH read replica of PG tracer_trace ────────────────────────────
-- ReplacingMergeTree(_version, is_deleted), mirroring `spans`: dual-write
-- re-emits the full row with a newer _version on every PG write (create,
-- name promotion, error_analysis_status update); the latest version wins and
-- is_deleted tombstones a soft-deleted trace. ORDER BY (project_id, id) — id
-- is globally unique so dedup collapses to one row per trace, and the
-- project prefix keeps a tenant's traces co-located.
--
-- JSON-ish PG columns (metadata/input/output/error/tags) are stored as String
-- here — same choice as `spans.input`/`output`/`tags`. They are opaque blobs
-- to CH analytics; readers parse them app-side exactly as they do for spans.
CREATE TABLE IF NOT EXISTS traces
(
    id                     UUID,
    project_id             UUID,
    project_version_id     Nullable(UUID),
    name                   Nullable(String),
    session_id             Nullable(UUID),
    external_id            Nullable(String),
    tags                   String                  DEFAULT '[]' CODEC(ZSTD(1)),
    metadata               String                  DEFAULT '{}' CODEC(ZSTD(1)),
    input                  String                  DEFAULT ''   CODEC(ZSTD(3)),
    output                 String                  DEFAULT ''   CODEC(ZSTD(3)),
    error                  String                  DEFAULT ''   CODEC(ZSTD(3)),
    error_analysis_status  LowCardinality(String)  DEFAULT 'PENDING',
    created_at             DateTime64(6, 'UTC'),
    updated_at             DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
    is_deleted             UInt8                   DEFAULT 0,
    _version               UInt64                  DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),

    -- Bloom on external_id for the "find trace by customer id" lookup; minmax
    -- on created_at/is_deleted for time-window + soft-delete pruning.
    INDEX idx_external_id  external_id  TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX auto_minmax_index_created_at created_at TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_is_deleted is_deleted TYPE minmax() GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toYYYYMM(created_at)
ORDER BY (project_id, id)
SETTINGS index_granularity = 8192;

-- ─── trace_dict — in-memory dictionary over traces ──────────────────────────
-- Shape + tuning mirror main's trace_dict (schema.py): single UUID key,
-- COMPLEX_KEY_HASHED, 30-60s refresh. Differences from main: source is the v2
-- `traces` table (not the CDC `tracer_trace` landing table), and `name` is a
-- non-nullable String DEFAULT '' so dictGet never yields NULL into the
-- non-nullable spans.trace_name column. The DB clause is intentionally OMITTED
-- so the dict resolves `traces` in its own database — keeps the .sql
-- DB-agnostic (apply_schema's --ch-database is the single switch), verified
-- against a non-default database.
--
-- No FINAL on the source (matches main): a trace's name is set at ingest and
-- effectively immutable, so reading a not-yet-merged duplicate is harmless;
-- the 30-60s LIFETIME re-reads after background merges settle.
--
-- CREATE OR REPLACE (not IF NOT EXISTS) is REQUIRED: a CH that ever ran the
-- legacy CH 24.10 schema (schema.py) already has a `trace_dict` whose source
-- is the CDC table `tracer_trace` with a `_peerdb_is_deleted` column. In the
-- no-CDC v2 world that table is gone, so the stale dict is permanently broken
-- (UNKNOWN_TABLE on every load) — and because spans.trace_name below depends
-- on dictGet('trace_dict', …), a broken dict would fail EVERY span insert at
-- materialized-column evaluation. IF NOT EXISTS would silently keep the stale
-- definition; OR REPLACE makes this v2 definition authoritative and self-heals
-- any environment carrying the legacy dict. Idempotent across boots.
-- `name` is Nullable(String) to match main AND to load cleanly: ~96% of
-- traces have a NULL name, and a dictionary attribute's DEFAULT applies only
-- to a MISSING dictGet key — NOT to NULL values read from the source. A
-- non-nullable attr would fail the load with "Cannot convert NULL value to
-- non-Nullable type". session_id/external_id are Nullable for the same reason.
CREATE OR REPLACE DICTIONARY trace_dict
(
    id                     UUID,
    project_id             UUID,
    name                   Nullable(String),
    session_id             Nullable(UUID),
    external_id            Nullable(String),
    tags                   String        DEFAULT '[]',
    error_analysis_status  String        DEFAULT 'PENDING',
    is_deleted             UInt8         DEFAULT 0
)
PRIMARY KEY id
SOURCE(CLICKHOUSE(TABLE 'traces'))
LIFETIME(MIN 30 MAX 60)
LAYOUT(COMPLEX_KEY_HASHED(SHARDS 4));

-- ─── trace_name on spans — restore main's "every span = trace's name" ───────
-- Supersedes 012's span-own-name expression. dictGetOrDefault returns the
-- trace's name for EVERY span whose trace is in the dict (root + child) — the
-- regression 012 introduced was that child spans showed their OWN name.
--
-- Semantically matches main: every span (root + child) gets the TRACE's name
-- via the dict — the regression 012 introduced was child spans showing their
-- OWN name. main computes
--   dictGetOrDefault('trace_dict','name', toUUID(s.trace_id), NULL)
-- into a Nullable(String) column. We KEEP the non-nullable String column from
-- 012 (changing its type is blocked by the idx_trace_name bloom index —
-- Code 524 ALTER_OF_COLUMN_IS_FORBIDDEN) and instead ifNull(...,'') the
-- result: the dict's `name` is Nullable, so dictGetOrDefault returns NULL for a
-- found-but-null-name trace (the ~96% that the OTLP ingest never names) and
-- the bare '' default for a dict miss; ifNull folds both to '' so the String
-- column accepts them. Net: named traces show their name on every span (the
-- fix); unnamed traces show '' — the blank-equivalent of main's NULL.
--
-- This is an EXPRESSION-only MODIFY (type stays String), which the index
-- permits. It is metadata-only; existing rows keep their stored value until
-- `ALTER TABLE spans MATERIALIZE COLUMN trace_name` is run out-of-band after
-- the traces backfill warms the dict (ch25_backfill_traces --materialize-spans).
ALTER TABLE spans
    MODIFY COLUMN trace_name String
        MATERIALIZED ifNull(dictGetOrDefault('trace_dict', 'name', toUUID(trace_id), ''), '');
