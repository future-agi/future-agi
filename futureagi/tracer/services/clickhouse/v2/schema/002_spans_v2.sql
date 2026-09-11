-- =============================================================================
-- FutureAGI — spans (the new home for all OTel span/observation data)
-- =============================================================================
--
-- Replaces ALL of:
--   • PG tracer_observation_span table (deleted Phase 5)
--   • CH tracer_observation_span landing table (PeerDB target, deleted Phase 5)
--   • CH spans table (the old denormalized table fed by spans_mv)
--   • CH spans_mv materialized view (the JSON-shredding MV that OOMs)
--
-- Filled by:
--   • Production: the FutureAGI OTel Collector custom exporter
--   • Backfill: scripts/backfill_pg_to_ch.py during migration
--   • Local tests: scripts/synthetic_load.py
--
-- Design rationale:
--   1. Typed Maps `attrs_string`/`attrs_number`/`attrs_bool` populated AT INSERT
--      by the Collector (SigNoz pattern). No insert-time MV — that's the OOM
--      cause we are eliminating.
--   2. `attributes_extra JSON(max_dynamic_paths=0)` is the overflow tier:
--      anything not classified by the Collector lands here. The `max_dynamic_paths=0`
--      forces all paths into the shared SubColumn store (avoids subcolumn
--      explosion on customer attributes; cf. ClickStack July 2025 retrospective).
--   3. The top-15 LLM keys are MATERIALIZED columns so dashboards never pay
--      the Map-scan cost on the hot path.
--   4. PARTITION BY toDate(start_time) so TTL drops whole parts (cheap).
--   5. ORDER BY puts (project_id, observation_type, service_name) first
--      because every query filters on those; toStartOfHour(start_time) gives
--      time-range pruning. trace_id at the end lets us collapse traces locally.
--   6. ZSTD(3) on fat columns; ZSTD(1) on attrs (Maps compress well with low cost).
--   7. `proj_metrics_hourly` PROJECTION replaces span_metrics_hourly_mv —
--      optimizer auto-routes dashboard aggregations through it.
--   8. `index_granularity_bytes = 64Mi` + `merge_max_block_size_bytes = 64Mi`
--      from Langfuse's fat-row tuning (rows can be multi-KB with LLM I/O).
--   9. `ttl_only_drop_parts = 1` is non-negotiable at 1B/day — rewriting parts
--      for TTL would saturate disk I/O.
--  10. Storage policy `tiered` (from 001_storage_policy.xml) gives us hot SSD
--      for 7 days, then cold (local in tests, S3 in prod) for 90 days.
--
-- Queries this serves (and the access path each uses):
--   • Dashboard hourly aggregates → proj_metrics_hourly (auto-routed)
--   • Trace tree fetch          → ORDER BY (project_id, …, trace_id) prefix scan
--   • Span-by-id (eval path)     → idx_id bloom filter
--   • Filter by model            → gen_ai_model materialized column
--   • Filter by custom attribute → attrs_number['x'] or attributes_extra.x.:Type
--
-- DO NOT add an insert-time MV on this table. If you need a derived view, use
-- a PROJECTION (auto-maintained by CH, no per-row Python overhead).
-- =============================================================================

CREATE TABLE IF NOT EXISTS spans
(
    -- ─── Identity (ORDER BY prefix; multi-tenancy boundary) ─────────────────
    project_id          UUID,
    observation_type    LowCardinality(String),
    service_name        LowCardinality(String) DEFAULT '',
    start_time          DateTime64(6, 'UTC'),
    trace_id            String,
    id                  String,
    parent_span_id      String  DEFAULT '',
    name                String,

    -- ─── Per-span timing (Collector fills) ──────────────────────────────────
    end_time            Nullable(DateTime64(6, 'UTC')),
    latency_ms          Int32  DEFAULT 0,

    -- ─── Org / user / session (denormalized from PG metadata) ───────────────
    org_id              Nullable(UUID),
    project_version_id  Nullable(UUID),
    end_user_id         Nullable(UUID),
    trace_session_id    Nullable(UUID),
    prompt_version_id   Nullable(UUID),
    prompt_label_id     Nullable(UUID),
    custom_eval_config_id Nullable(UUID),

    -- ─── Status ─────────────────────────────────────────────────────────────
    status              LowCardinality(String)         DEFAULT '',
    status_message      String                         DEFAULT '',

    -- ─── Top-15 LLM hot attributes (Collector fills via OTel GenAI semconv) ─
    -- Promoted to first-class columns so the most common filters never pay
    -- the Map-scan cost. Add/remove from this list based on quarterly
    -- system.query_log analysis.
    model               LowCardinality(String)         DEFAULT '',
    provider            LowCardinality(String)         DEFAULT '',
    gen_ai_system       LowCardinality(String)         DEFAULT '',
    gen_ai_operation    LowCardinality(String)         DEFAULT '',
    operation_name      LowCardinality(String)         DEFAULT '',

    -- ─── Token + cost economics ─────────────────────────────────────────────
    prompt_tokens       Int32  DEFAULT 0,
    completion_tokens   Int32  DEFAULT 0,
    total_tokens        Int32  DEFAULT 0,
    cost                Float64 DEFAULT 0,

    -- ─── Typed attribute Maps (Collector splits at ingest) ─────────────────
    -- SigNoz pattern. The Collector's fi_adapter_processor walks each span's
    -- OTel attributes and routes each (key, value) pair to one of these three
    -- maps based on the value's type. Cheap mapKeys/mapValues bloom indexes
    -- below let us answer "which spans have this attribute" without scan.
    attrs_string        Map(LowCardinality(String), String)  CODEC(ZSTD(1)),
    attrs_number        Map(LowCardinality(String), Float64) CODEC(ZSTD(1)),
    attrs_bool          Map(LowCardinality(String), UInt8)   CODEC(ZSTD(1)),

    -- ─── Overflow JSON: arbitrary customer keys that don't fit the above ──
    -- max_dynamic_paths=0 forces all paths into the shared sub-column store
    -- — prevents schema-explosion when one customer sends 10k unique keys.
    attributes_extra    JSON(max_dynamic_paths=0)            CODEC(ZSTD(1)),
    resource_attrs      JSON(max_dynamic_paths=512)          CODEC(ZSTD(1)),
    metadata            JSON(max_dynamic_paths=256)          CODEC(ZSTD(1)),

    -- ─── Fat text (LLM input/output, span events, tags) ───────────────────
    input               String  DEFAULT ''   CODEC(ZSTD(3)),
    output              String  DEFAULT ''   CODEC(ZSTD(3)),
    input_length        UInt32  MATERIALIZED  lengthUTF8(input),
    output_length       UInt32  MATERIALIZED  lengthUTF8(output),
    -- For oversized payloads, the Collector PUTs to object storage and stores
    -- the URL. Read-path code knows to fetch if these are non-null.
    input_gcs_url       Nullable(String),
    output_gcs_url      Nullable(String),
    tags                String  DEFAULT '[]' CODEC(ZSTD(1)),
    span_events         String  DEFAULT '[]' CODEC(ZSTD(1)),

    -- ─── Eval surface (denormalized for fast filter) ────────────────────────
    eval_status         LowCardinality(String) DEFAULT '',

    -- ─── Provenance / soft-delete ───────────────────────────────────────────
    semconv_source      LowCardinality(String) DEFAULT '',
    created_at          DateTime64(6, 'UTC')   DEFAULT now64(6, 'UTC'),
    updated_at          DateTime64(6, 'UTC')   DEFAULT now64(6, 'UTC'),
    is_deleted          UInt8                  DEFAULT 0,
    _version            UInt64                 DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),

    -- ─── Skip indexes ───────────────────────────────────────────────────────
    -- Bloom filters on high-cardinality identifier columns. False-positive rates
    -- chosen to balance index size vs selectivity at 1B/day scale.
    INDEX idx_id                  id                       TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_trace_id            trace_id                 TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_parent_span_id      parent_span_id           TYPE bloom_filter(0.01)  GRANULARITY 1,
    INDEX idx_trace_session_id    trace_session_id         TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_end_user_id         end_user_id              TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_custom_eval_config_id custom_eval_config_id  TYPE bloom_filter(0.01)  GRANULARITY 1,
    INDEX idx_model               model                    TYPE bloom_filter(0.01)  GRANULARITY 1,
    INDEX idx_provider            provider                 TYPE bloom_filter(0.01)  GRANULARITY 1,
    INDEX idx_status              status                   TYPE set(20)             GRANULARITY 1,
    INDEX idx_eval_status         eval_status              TYPE set(20)             GRANULARITY 1,
    -- Map-key indexes enable "which spans have attribute foo?" without scanning the maps.
    INDEX idx_attrs_str_keys      mapKeys(attrs_string)    TYPE bloom_filter(0.01)  GRANULARITY 1,
    INDEX idx_attrs_num_keys      mapKeys(attrs_number)    TYPE bloom_filter(0.01)  GRANULARITY 1,
    INDEX idx_attrs_bool_keys     mapKeys(attrs_bool)      TYPE bloom_filter(0.01)  GRANULARITY 1,
    -- Min/max skip indexes on numeric columns for range-filter pruning
    -- (dashboard filters by latency / cost / token counts; soft-delete +
    -- version predicates prune on is_deleted / _version).
    --
    -- These are declared EXPLICITLY rather than via the table setting
    -- `add_minmax_index_for_numeric_columns = 1`. That setting auto-creates
    -- a minmax index for EVERY numeric column — including the
    -- `_peerdb_is_deleted UInt8 ALIAS` added in 014. A minmax index on a
    -- non-stored ALIAS column is invalid: CH bakes it into the table's
    -- persisted metadata, then on the next table load (ATTACH, e.g. every
    -- CH restart) the setting tries to recreate it and ATTACH fails with
    -- `Code: 49 ... auto_minmax_index__peerdb_is_deleted already exists`,
    -- wedging the whole table (every read → Code 722, even DROP fails).
    -- Listing the real numeric columns by hand makes that impossible: no
    -- setting, so no auto-index is ever derived for the alias (or any
    -- future ALIAS column). Keep this list in sync if numeric columns are
    -- added.
    INDEX auto_minmax_index_latency_ms        latency_ms        TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_prompt_tokens     prompt_tokens     TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_completion_tokens completion_tokens TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_total_tokens      total_tokens      TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_cost              cost              TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_input_length      input_length      TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_output_length     output_length     TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index_is_deleted        is_deleted        TYPE minmax() GRANULARITY 1,
    INDEX auto_minmax_index__version          _version          TYPE minmax() GRANULARITY 1,

    -- ─── Dashboard projection (replaces span_metrics_hourly_mv) ────────────
    -- The CH query optimizer routes any GROUP BY-on-these-dims aggregation
    -- through this projection automatically. Zero code change in dashboards.
    PROJECTION proj_metrics_hourly
    (
        SELECT
            project_id,
            toStartOfHour(start_time)   AS hour,
            observation_type,
            status,
            model,
            provider,
            countState(),
            sumState(cost),
            sumState(total_tokens),
            sumState(prompt_tokens),
            sumState(completion_tokens),
            quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
        GROUP BY project_id, hour, observation_type, status, model, provider
    ),

    -- ─── Root-span projection (replaces proj_root_spans in old `spans`) ────
    -- Used by trace-list pagination. Sorts roots by (project_id, start_time)
    -- so the most common dashboard query — "give me recent traces" — does a
    -- pure prefix scan with no full-table read.
    PROJECTION proj_root_spans
    (
        SELECT
            project_id,
            start_time,
            trace_id,
            id,
            name,
            observation_type,
            status,
            latency_ms,
            cost,
            total_tokens,
            model,
            provider,
            trace_session_id
        ORDER BY (project_id, is_deleted, parent_span_id, start_time)
    )
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toDate(start_time)
PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time))
ORDER BY    (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
TTL  toDateTime(start_time) + INTERVAL  7 DAY  TO VOLUME 'cold',
     toDateTime(start_time) + INTERVAL 90 DAY  DELETE
SETTINGS
    storage_policy                          = 'tiered',
    index_granularity                        = 8192,
    index_granularity_bytes                  = 67108864,    -- 64 MiB
    merge_max_block_size_bytes               = 67108864,    -- 64 MiB
    ttl_only_drop_parts                      = 1,
    -- NOTE: `add_minmax_index_for_numeric_columns` is deliberately NOT set.
    -- It auto-indexes every numeric column including the `_peerdb_is_deleted`
    -- ALIAS (014), which wedges the table on load. The numeric minmax
    -- indexes are declared explicitly in the INDEX block above instead.
    deduplicate_merge_projection_mode        = 'rebuild',   -- safer with projections + dedup
    allow_nullable_key                       = 1            -- nullable ORDER BY tiebreaker bits if needed later;
;

-- =============================================================================
-- Materialized hot-key columns
-- =============================================================================
-- Promoting top LLM attribute keys to typed columns lets dashboard queries
-- read just the column they need (a few hundred bytes per granule) instead
-- of materializing the whole attrs_string Map (kilobytes). Cost: nothing — the
-- column is derived from another column already in the row.
--
-- We ADD these as ALTER statements (vs inline in the CREATE) so they're easy
-- to extend later without rewriting the canonical CREATE TABLE.
-- =============================================================================

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS llm_request_model       LowCardinality(String) MATERIALIZED attrs_string['gen_ai.request.model'],
    ADD COLUMN IF NOT EXISTS llm_response_model      LowCardinality(String) MATERIALIZED attrs_string['gen_ai.response.model'],
    ADD COLUMN IF NOT EXISTS llm_finish_reason       LowCardinality(String) MATERIALIZED attrs_string['gen_ai.response.finish_reason'],
    ADD COLUMN IF NOT EXISTS embedding_model         LowCardinality(String) MATERIALIZED attrs_string['llm.embedding.model'],
    ADD COLUMN IF NOT EXISTS streaming               Nullable(UInt8)        MATERIALIZED attrs_bool['streaming'],
    ADD COLUMN IF NOT EXISTS temperature             Nullable(Float64)      MATERIALIZED attrs_number['gen_ai.request.temperature'],
    ADD COLUMN IF NOT EXISTS top_p                   Nullable(Float64)      MATERIALIZED attrs_number['gen_ai.request.top_p'],
    ADD COLUMN IF NOT EXISTS max_tokens              Nullable(Int32)        MATERIALIZED toInt32OrZero(toString(attrs_number['gen_ai.request.max_tokens']));
