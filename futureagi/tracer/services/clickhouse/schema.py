"""
ClickHouse Schema Definitions — PeerDB CDC + Analytics Layers

Architecture overview
=====================
Layer 1  –  CDC landing tables (incl. prompt tables)
            PeerDB mirrors PostgreSQL rows into ReplacingMergeTree tables.
            Every table carries the three PeerDB meta-columns that enable
            exactly-once, order-preserving CDC.

Layer 2  –  trace_dict dictionary + denormalized ``spans`` table
            A ClickHouse Dictionary keeps the latest trace metadata in RAM
            for cheap look-ups.  A Materialized View enriches every new
            observation span with trace context and shreds the JSON
            attribute bag into typed Map columns for attribute analytics.

Layer 3  –  Pre-aggregated hourly rollups
            AggregatingMergeTree tables with companion MVs provide
            sub-second dashboard queries (span metrics, eval metrics).

Creation order
--------------
1. CDC landing tables  (tracer_observation_span, tracer_trace,
   trace_session, tracer_eval_logger)
2. trace_dict dictionary
3. spans (denormalized wide table)
4. spans_mv (MV that feeds ``spans``)
5. span_metrics_hourly + span_metrics_hourly_mv
6. eval_metrics_hourly + eval_metrics_hourly_mv
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

# Resolve the configured CH database name for use in DDL templates.
# Falls back to "futureagi" which is the production default.
_CH_DATABASE = os.getenv("CH_DATABASE", "futureagi")
_USE_REPLICATED_ENGINES = os.getenv(
    "CH_USE_REPLICATED_ENGINES", "true"
).lower() in {"1", "true", "yes", "on"}


def _to_single_node_engine(ddl: str) -> str:
    """Convert Replicated*MergeTree engines to single-node equivalents.

    OSS/self-host deployments usually run a single ClickHouse node without
    shard/replica macros. Replicated engines fail there with:
    "No macro 'shard' in config ...".
    """

    pattern = (
        r"Replicated(?P<engine>[A-Za-z]+MergeTree)\("
        r"\s*'[^']*'\s*,\s*'[^']*'\s*(?:,\s*(?P<args>[^)]*?)\s*)?\)"
    )

    def repl(match: re.Match[str]) -> str:
        engine = match.group("engine")
        args = (match.group("args") or "").strip()
        return f"{engine}({args})" if args else f"{engine}()"

    return re.sub(pattern, repl, ddl, flags=re.DOTALL)

# ============================================================================
# LAYER 1 — PeerDB CDC Landing Tables
# ============================================================================
# All tables use ReplacingMergeTree(_peerdb_version) so that PeerDB can
# replay and de-duplicate change events.  The three meta-columns are
# appended to every table:
#   _peerdb_synced_at  DateTime64(6)  — wall-clock time the row landed
#   _peerdb_is_deleted UInt8          — soft-delete flag (1 = deleted)
#   _peerdb_version    Int64          — monotonic version for RBMT dedup
# ============================================================================

# ---------------------------------------------------------------------------
# 1. tracer_observation_span
#    Mirrors: PostgreSQL model ObservationSpan
# ---------------------------------------------------------------------------
CDC_OBSERVATION_SPAN = """
CREATE TABLE IF NOT EXISTS tracer_observation_span (
    -- Primary key (CharField max_length=255 in PG)
    id String,

    -- Foreign keys / hierarchy
    trace_id String,
    project_id UUID,
    project_version_id Nullable(UUID),
    org_id Nullable(UUID),
    parent_span_id Nullable(String),

    -- Descriptors
    name String,
    observation_type LowCardinality(String),
    operation_name Nullable(String),
    status LowCardinality(Nullable(String)),
    status_message Nullable(String),

    -- Timing
    start_time Nullable(DateTime64(3)),
    end_time Nullable(DateTime64(3)),
    latency_ms Nullable(Int32),

    -- LLM fields
    model Nullable(String),
    provider Nullable(String),
    prompt_tokens Nullable(Int32),
    completion_tokens Nullable(Int32),
    total_tokens Nullable(Int32),
    cost Nullable(Float64),

    -- Content (stored as strings; JSON in PG)
    input String DEFAULT '',
    output String DEFAULT '',

    -- Attributes (JSONB -> String via PeerDB)
    span_attributes String DEFAULT '{}',
    resource_attributes String DEFAULT '{}',
    metadata String DEFAULT '{}',
    tags String DEFAULT '[]',
    span_events String DEFAULT '[]',

    -- Tracking
    end_user_id Nullable(UUID),
    custom_eval_config_id Nullable(UUID),
    semconv_source Nullable(String),
    schema_version String DEFAULT '1.0',

    -- Eval fields
    eval_id Nullable(String),
    eval_input String DEFAULT '{}',
    eval_attributes String DEFAULT '{}',
    eval_status Nullable(String),
    input_images String DEFAULT '[]',

    -- Model parameters (JSONB -> String)
    model_parameters String DEFAULT '{}',

    -- Additional fields
    response_time Nullable(Float64),
    org_user_id Nullable(UUID),
    prompt_version_id Nullable(UUID),
    prompt_label_id Nullable(UUID),

    -- Soft-delete
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_model model TYPE bloom_filter GRANULARITY 1,
    INDEX idx_observation_type observation_type TYPE set(100) GRANULARITY 1,
    INDEX idx_status status TYPE set(10) GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/tracer_observation_span', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (project_id, created_at, trace_id, id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# 2. tracer_trace
#    Mirrors: PostgreSQL model Trace
# ---------------------------------------------------------------------------
CDC_TRACE = """
CREATE TABLE IF NOT EXISTS tracer_trace (
    id UUID,
    project_id UUID,
    project_version_id Nullable(UUID),
    name Nullable(String),

    -- Content (JSONB -> String)
    metadata String DEFAULT '{}',
    input String DEFAULT '{}',
    output String DEFAULT '{}',
    error String DEFAULT '{}',

    -- Relationships
    session_id Nullable(UUID),
    external_id Nullable(String),
    tags String DEFAULT '[]',

    -- Error analysis
    error_analysis_status Nullable(String),

    -- Soft-delete
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_session_id session_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_external_id external_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/tracer_trace', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (project_id, created_at, id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# 3. trace_session
#    Mirrors: PostgreSQL model TraceSession
# ---------------------------------------------------------------------------
CDC_TRACE_SESSION = """
CREATE TABLE IF NOT EXISTS trace_session (
    id UUID,
    project_id UUID,
    external_id Nullable(String),
    name Nullable(String),
    end_user_id Nullable(UUID),
    status LowCardinality(Nullable(String)),
    attributes String DEFAULT '{}',
    started_at Nullable(DateTime64(3)),
    bookmarked UInt8 DEFAULT 0,

    -- Soft-delete
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/trace_session', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (project_id, created_at, id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# 4. tracer_eval_logger
#    Mirrors: PostgreSQL model EvalLogger
# ---------------------------------------------------------------------------
CDC_EVAL_LOGGER = """
CREATE TABLE IF NOT EXISTS tracer_eval_logger (
    id UUID,

    -- Foreign keys
    trace_id UUID,
    observation_span_id Nullable(String),
    custom_eval_config_id UUID DEFAULT '00000000-0000-0000-0000-000000000000',
    eval_type_id Nullable(String),

    -- Results
    output_bool Nullable(UInt8),
    output_float Nullable(Float64),
    output_str Nullable(String),
    output_str_list String DEFAULT '[]',

    -- Error tracking
    error UInt8 DEFAULT 0,
    error_message Nullable(String),

    -- Explanation / metadata
    eval_explanation Nullable(String),
    output_metadata String DEFAULT '{}',
    results_tags String DEFAULT '[]',
    results_explanation String DEFAULT '{}',
    eval_tags String DEFAULT '[]',

    -- Identifiers
    eval_id Nullable(String),
    eval_task_id Nullable(String),

    -- Soft-delete
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_observation_span_id observation_span_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_custom_eval_config_id custom_eval_config_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/tracer_eval_logger', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (trace_id, custom_eval_config_id, id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# 5. trace_annotation
#    Mirrors: PostgreSQL model TraceAnnotation (table: trace_annotation)
# ---------------------------------------------------------------------------
CDC_TRACE_ANNOTATION = """
CREATE TABLE IF NOT EXISTS trace_annotation (
    id UUID,

    -- Foreign keys
    trace_id Nullable(UUID),
    observation_span_id Nullable(String),
    annotation_label_id UUID,
    user_id Nullable(UUID),

    -- Values (one is populated depending on label type)
    annotation_value Nullable(String),
    annotation_value_bool Nullable(UInt8),
    annotation_value_float Nullable(Float64),
    annotation_value_str_list String DEFAULT '[]',

    updated_by Nullable(String),

    -- Soft-delete
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_observation_span_id observation_span_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_annotation_label_id annotation_label_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/trace_annotation', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (annotation_label_id, created_at, id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# 6. model_hub_score
#    Mirrors: PostgreSQL model Score (table: model_hub_score)
#    The unified annotation/score primitive. Replaces trace_annotation for
#    annotation queries. value is a JSONField with type-specific subkeys.
# ---------------------------------------------------------------------------
CDC_MODEL_HUB_SCORE = """
CREATE TABLE IF NOT EXISTS model_hub_score (
    id UUID,

    -- Source reference
    source_type LowCardinality(String),
    trace_id Nullable(UUID),
    observation_span_id Nullable(String),
    trace_session_id Nullable(UUID),
    call_execution_id Nullable(UUID),
    dataset_row_id Nullable(UUID),
    prototype_run_id Nullable(UUID),
    queue_item_id Nullable(UUID),
    project_id Nullable(UUID),

    -- What was scored
    label_id UUID,
    value String DEFAULT '{}',

    -- Who scored it
    annotator_id Nullable(UUID),
    score_source LowCardinality(String) DEFAULT 'HUMAN',
    notes Nullable(String),

    -- Scoping
    organization_id UUID,
    workspace_id Nullable(UUID),

    -- Soft-delete
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(6)),

    -- Timestamps
    created_at DateTime64(6),
    updated_at DateTime64(6),

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_label_id label_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_span_id observation_span_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_score', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (label_id, created_at, id)
SETTINGS index_granularity = 8192;
"""

# ============================================================================
# LAYER 2 — Dictionary + Denormalized Spans Table
# ============================================================================

# ---------------------------------------------------------------------------
# trace_dict — in-memory dictionary sourced from tracer_trace
# Enables cheap dictGet() look-ups inside the spans MV without a JOIN.
# ---------------------------------------------------------------------------
TRACE_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS trace_dict (
    id UUID,
    project_id UUID,
    name Nullable(String),
    session_id Nullable(UUID),
    external_id Nullable(String),
    tags String,
    _peerdb_is_deleted UInt8
)
PRIMARY KEY id
SOURCE(CLICKHOUSE(
    TABLE 'tracer_trace'
    DB '{_CH_DATABASE}'
))
LIFETIME(MIN 30 MAX 60)
LAYOUT(COMPLEX_KEY_HASHED(SHARDS 4));
"""

# ---------------------------------------------------------------------------
# tracer_enduser — CDC landing table for end user profiles
# ---------------------------------------------------------------------------
CDC_TRACER_ENDUSER = """
CREATE TABLE IF NOT EXISTS tracer_enduser (
    id UUID,
    user_id String,
    user_id_type Nullable(String),
    user_id_hash Nullable(String),
    metadata String DEFAULT '{{}}',
    project_id UUID,
    organization_id UUID,
    workspace_id Nullable(UUID),
    created_at DateTime64(3),
    updated_at DateTime64(3),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3))
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/tracer_enduser', '{replica}', _peerdb_version)
ORDER BY (id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# enduser_dict — in-memory dictionary for user attribute look-ups
# ---------------------------------------------------------------------------
ENDUSER_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS enduser_dict (
    id UUID,
    user_id String,
    user_id_type Nullable(String),
    metadata String,
    project_id UUID,
    _peerdb_is_deleted UInt8
)
PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, user_id, user_id_type, metadata, project_id, _peerdb_is_deleted FROM {_CH_DATABASE}.tracer_enduser WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LIFETIME(MIN 60 MAX 120)
LAYOUT(COMPLEX_KEY_HASHED());
"""

# ---------------------------------------------------------------------------
# CDC landing tables for prompt tables (PeerDB mirrors from PostgreSQL)
# ---------------------------------------------------------------------------
CDC_MODEL_HUB_PROMPTVERSION = """
CREATE TABLE IF NOT EXISTS model_hub_promptversion (
    id UUID,
    original_template_id Nullable(UUID),
    template_version String,
    prompt_config_snapshot String DEFAULT '{}',
    output String DEFAULT '{}',
    metadata String DEFAULT '{}',
    variable_names String DEFAULT '[]',
    evaluation_results String DEFAULT '{}',
    evaluation_configs String DEFAULT '{}',
    commit_message Nullable(String),
    is_default UInt8 DEFAULT 0,
    is_draft UInt8 DEFAULT 0,
    prompt_base_template_id Nullable(UUID),
    placeholders String DEFAULT '{}',
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_promptversion', '{replica}', _peerdb_version)
ORDER BY id;
"""

CDC_MODEL_HUB_PROMPTTEMPLATE = """
CREATE TABLE IF NOT EXISTS model_hub_prompttemplate (
    id UUID,
    name String,
    description Nullable(String),
    organization_id Nullable(UUID),
    workspace_id Nullable(UUID),
    variable_names String DEFAULT '[]',
    is_sample UInt8 DEFAULT 0,
    prompt_folder_id Nullable(UUID),
    created_by_id Nullable(UUID),
    placeholders String DEFAULT '{}',
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_prompttemplate', '{replica}', _peerdb_version)
ORDER BY id;
"""

CDC_MODEL_HUB_PROMPTLABEL = """
CREATE TABLE IF NOT EXISTS model_hub_promptlabel (
    id UUID,
    name String,
    type String,
    metadata String DEFAULT '{}',
    organization_id Nullable(UUID),
    workspace_id Nullable(UUID),
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_promptlabel', '{replica}', _peerdb_version)
ORDER BY id;
"""

# ---------------------------------------------------------------------------
# prompt_lookup — legacy lookup table (kept for backward compatibility,
# no longer actively populated; dicts now source from CDC tables above)
# ---------------------------------------------------------------------------
PROMPT_LOOKUP_TABLE = """
CREATE TABLE IF NOT EXISTS prompt_lookup (
    prompt_version_id UUID,
    prompt_name String,
    template_version String,
    template_id Nullable(UUID),
    commit_message Nullable(String)
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/prompt_lookup', '{replica}')
ORDER BY (prompt_version_id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# prompt_dict — resolves prompt_version_id → prompt name + version
# Sources from CDC tables model_hub_promptversion JOIN model_hub_prompttemplate
# ---------------------------------------------------------------------------
PROMPT_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS prompt_dict (
    prompt_version_id UUID,
    prompt_name String,
    template_version String,
    template_id Nullable(UUID),
    commit_message Nullable(String)
)
PRIMARY KEY prompt_version_id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT pv.id AS prompt_version_id, pt.name AS prompt_name, pv.template_version, pt.id AS template_id, pv.commit_message FROM {_CH_DATABASE}.model_hub_promptversion pv FINAL JOIN {_CH_DATABASE}.model_hub_prompttemplate pt FINAL ON pt.id = pv.original_template_id WHERE pv._peerdb_is_deleted = 0 AND pt._peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LIFETIME(MIN 60 MAX 120)
LAYOUT(COMPLEX_KEY_HASHED());
"""

# ---------------------------------------------------------------------------
# prompt_label_lookup — legacy lookup table (kept for backward compatibility,
# no longer actively populated; dict now sources from CDC table above)
# ---------------------------------------------------------------------------
PROMPT_LABEL_LOOKUP_TABLE = """
CREATE TABLE IF NOT EXISTS prompt_label_lookup (
    id UUID,
    name String,
    type Nullable(String)
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/prompt_label_lookup', '{replica}')
ORDER BY (id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# prompt_label_dict — resolves prompt_label_id → label name + type
# Sources from CDC table model_hub_promptlabel
# ---------------------------------------------------------------------------
PROMPT_LABEL_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS prompt_label_dict (
    id UUID,
    name String,
    type Nullable(String)
)
PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, name, type FROM {_CH_DATABASE}.model_hub_promptlabel WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LIFETIME(MIN 60 MAX 120)
LAYOUT(COMPLEX_KEY_HASHED());
"""

# ---------------------------------------------------------------------------
# spans — wide denormalized table
# Combines observation-span columns with trace context (via dict) and
# typed Map columns for attribute analytics.
# ---------------------------------------------------------------------------
SPANS_TABLE = """
CREATE TABLE IF NOT EXISTS spans (
    -- ---- Span core columns (from tracer_observation_span) ----
    id String,
    trace_id String,
    project_id UUID,
    project_version_id Nullable(UUID),
    org_id Nullable(UUID),
    parent_span_id Nullable(String),

    name String,
    observation_type LowCardinality(String),
    operation_name Nullable(String),
    status LowCardinality(Nullable(String)),
    status_message Nullable(String),

    start_time Nullable(DateTime64(3)),
    end_time Nullable(DateTime64(3)),
    latency_ms Nullable(Int32),

    model Nullable(String),
    provider Nullable(String),
    prompt_tokens Nullable(Int32),
    completion_tokens Nullable(Int32),
    total_tokens Nullable(Int32),
    cost Nullable(Float64),

    input String DEFAULT '' CODEC(ZSTD(3)),
    output String DEFAULT '' CODEC(ZSTD(3)),

    -- ---- Typed attribute maps (shredded from span_attributes JSON) ----
    span_attr_str Map(LowCardinality(String), String),
    span_attr_num Map(LowCardinality(String), Float64),
    span_attr_bool Map(LowCardinality(String), UInt8),

    -- Raw JSON kept for full-fidelity replay, heavily compressed
    span_attributes_raw String DEFAULT '{}' CODEC(ZSTD(3)),
    resource_attributes_raw String DEFAULT '{}' CODEC(ZSTD(3)),

    -- Metadata as a flat string map
    metadata_map Map(LowCardinality(String), String),

    tags String DEFAULT '[]',
    span_events String DEFAULT '[]',

    end_user_id Nullable(UUID),
    custom_eval_config_id Nullable(UUID),
    semconv_source Nullable(String),
    schema_version String DEFAULT '1.0',
    prompt_version_id Nullable(UUID),
    prompt_label_id Nullable(UUID),

    -- ---- Denormalized trace fields (from trace_dict) ----
    trace_name Nullable(String),
    trace_session_id Nullable(UUID),
    trace_external_id Nullable(String),
    trace_tags String DEFAULT '[]',

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),

    -- PeerDB meta-columns forwarded for FINAL filtering and dedup
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes on map keys for attribute analytics
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_model model TYPE bloom_filter GRANULARITY 1,
    INDEX idx_observation_type observation_type TYPE set(100) GRANULARITY 1,
    INDEX idx_status status TYPE set(10) GRANULARITY 1,
    INDEX idx_span_attr_str_keys mapKeys(span_attr_str) TYPE bloom_filter GRANULARITY 1,
    INDEX idx_span_attr_num_keys mapKeys(span_attr_num) TYPE bloom_filter GRANULARITY 1,
    INDEX idx_span_attr_bool_keys mapKeys(span_attr_bool) TYPE bloom_filter GRANULARITY 1,

    -- Projection for fast root-span pagination: allows CH to skip non-root
    -- spans via the index instead of scanning all rows.
    PROJECTION proj_root_spans (
        SELECT
            trace_id, trace_name, name, observation_type, status,
            start_time, end_time, latency_ms, cost,
            total_tokens, prompt_tokens, completion_tokens,
            model, provider, trace_session_id, trace_tags,
            project_id, parent_span_id, _peerdb_is_deleted, created_at
        ORDER BY (project_id, _peerdb_is_deleted, parent_span_id, start_time)
    )
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/spans', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (project_id, toDate(created_at), trace_id, id)
SETTINGS index_granularity = 8192, deduplicate_merge_projection_mode = 'drop';
"""

# ---------------------------------------------------------------------------
# spans_mv — Materialized View populating ``spans``
#
# Reads from the CDC landing table tracer_observation_span, enriches each
# row with trace context via dictGet, and shreds the span_attributes JSON
# into typed Map(String, T) columns using JSONExtract* functions.
#
# JSON shredding strategy:
#   1. Extract all top-level keys with JSONExtractKeys.
#   2. For each key, attempt JSONExtractFloat64 / JSONExtractBool.
#      If the float extraction returns 0 *and* the raw string value is
#      not literally '0' or '0.0', the key is treated as a string.
#   3. Build maps via mapFromArrays over the filtered key/value arrays.
# ---------------------------------------------------------------------------
SPANS_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS spans_mv
TO spans
AS
SELECT
    -- Span core columns
    s.id                         AS id,
    s.trace_id                   AS trace_id,
    s.project_id                 AS project_id,
    s.project_version_id         AS project_version_id,
    s.org_id                     AS org_id,
    s.parent_span_id             AS parent_span_id,
    s.name                       AS name,
    s.observation_type           AS observation_type,
    s.operation_name             AS operation_name,
    s.status                     AS status,
    s.status_message             AS status_message,
    s.start_time                 AS start_time,
    s.end_time                   AS end_time,
    s.latency_ms                 AS latency_ms,
    s.model                      AS model,
    s.provider                   AS provider,
    s.prompt_tokens              AS prompt_tokens,
    s.completion_tokens          AS completion_tokens,
    s.total_tokens               AS total_tokens,
    s.cost                       AS cost,
    s.input                      AS input,
    s.output                     AS output,

    -- Typed attribute maps shredded from span_attributes JSON -----------
    -- String attributes: keys whose float extraction is 0 and raw value
    -- is not a numeric literal, and whose bool extraction is also 0.
    mapFromArrays(
        arrayFilter(
            (k) -> JSONExtractFloat(s.span_attributes, k) = 0
                AND JSONExtractRaw(s.span_attributes, k) NOT IN ('0', '0.0', 'true', 'false'),
            JSONExtractKeys(s.span_attributes)
        ),
        arrayMap(
            (k) -> JSONExtractString(s.span_attributes, k),
            arrayFilter(
                (k) -> JSONExtractFloat(s.span_attributes, k) = 0
                    AND JSONExtractRaw(s.span_attributes, k) NOT IN ('0', '0.0', 'true', 'false'),
                JSONExtractKeys(s.span_attributes)
            )
        )
    ) AS span_attr_str,

    -- Numeric attributes: keys where JSONExtractFloat returns non-zero,
    -- or the raw value is literally '0' or '0.0'.
    mapFromArrays(
        arrayFilter(
            (k) -> JSONExtractFloat(s.span_attributes, k) != 0
                OR JSONExtractRaw(s.span_attributes, k) IN ('0', '0.0'),
            arrayFilter(
                (k) -> JSONExtractRaw(s.span_attributes, k) NOT IN ('true', 'false'),
                JSONExtractKeys(s.span_attributes)
            )
        ),
        arrayMap(
            (k) -> JSONExtractFloat(s.span_attributes, k),
            arrayFilter(
                (k) -> JSONExtractFloat(s.span_attributes, k) != 0
                    OR JSONExtractRaw(s.span_attributes, k) IN ('0', '0.0'),
                arrayFilter(
                    (k) -> JSONExtractRaw(s.span_attributes, k) NOT IN ('true', 'false'),
                    JSONExtractKeys(s.span_attributes)
                )
            )
        )
    ) AS span_attr_num,

    -- Boolean attributes: keys whose raw value is 'true' or 'false'.
    mapFromArrays(
        arrayFilter(
            (k) -> JSONExtractRaw(s.span_attributes, k) IN ('true', 'false'),
            JSONExtractKeys(s.span_attributes)
        ),
        arrayMap(
            (k) -> toUInt8(JSONExtractBool(s.span_attributes, k)),
            arrayFilter(
                (k) -> JSONExtractRaw(s.span_attributes, k) IN ('true', 'false'),
                JSONExtractKeys(s.span_attributes)
            )
        )
    ) AS span_attr_bool,

    -- Raw JSON kept for full-fidelity access
    s.span_attributes            AS span_attributes_raw,
    s.resource_attributes        AS resource_attributes_raw,

    -- Metadata shredded into a flat string map
    mapFromArrays(
        JSONExtractKeys(s.metadata),
        arrayMap(
            (k) -> JSONExtractString(s.metadata, k),
            JSONExtractKeys(s.metadata)
        )
    ) AS metadata_map,

    s.tags                       AS tags,
    s.span_events                AS span_events,
    s.end_user_id                AS end_user_id,
    s.custom_eval_config_id      AS custom_eval_config_id,
    s.semconv_source             AS semconv_source,
    s.schema_version             AS schema_version,
    s.prompt_version_id          AS prompt_version_id,
    s.prompt_label_id            AS prompt_label_id,

    -- Denormalized trace context via dictionary look-up
    dictGetOrDefault('trace_dict', 'name',        toUUID(s.trace_id), NULL)  AS trace_name,
    dictGetOrDefault('trace_dict', 'session_id',  toUUID(s.trace_id), NULL)  AS trace_session_id,
    dictGetOrDefault('trace_dict', 'external_id', toUUID(s.trace_id), NULL)  AS trace_external_id,
    dictGetOrDefault('trace_dict', 'tags',        toUUID(s.trace_id), '[]')  AS trace_tags,

    s.created_at                 AS created_at,
    s.updated_at                 AS updated_at,
    s._peerdb_is_deleted         AS _peerdb_is_deleted,
    s._peerdb_version            AS _peerdb_version

FROM tracer_observation_span AS s;
"""

# ============================================================================
# LAYER 3 — Pre-aggregated Hourly Rollups
# ============================================================================

# ---------------------------------------------------------------------------
# span_metrics_hourly — AggregatingMergeTree
# Dimensions: project_id, hour, observation_type, model, status
# ---------------------------------------------------------------------------
SPAN_METRICS_HOURLY_TABLE = """
CREATE TABLE IF NOT EXISTS span_metrics_hourly (
    -- Dimensions
    project_id UUID,
    hour DateTime,
    observation_type LowCardinality(String),
    model LowCardinality(Nullable(String)),
    status LowCardinality(Nullable(String)),

    -- Aggregates
    span_count SimpleAggregateFunction(sum, UInt64),
    trace_count AggregateFunction(uniq, String),
    error_count SimpleAggregateFunction(sum, UInt64),

    -- Token sums
    total_prompt_tokens SimpleAggregateFunction(sum, Int64),
    total_completion_tokens SimpleAggregateFunction(sum, Int64),
    total_tokens SimpleAggregateFunction(sum, Int64),

    -- Latency quantile sketch
    latency_quantile AggregateFunction(quantiles(0.5, 0.90, 0.95, 0.99), Float64),

    -- Cost
    total_cost SimpleAggregateFunction(sum, Float64)
)
ENGINE = ReplicatedAggregatingMergeTree('/clickhouse/tables/{shard}/span_metrics_hourly', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour, observation_type, model, status)
TTL hour + INTERVAL 365 DAY
SETTINGS index_granularity = 8192, allow_nullable_key = 1;
"""

SPAN_METRICS_HOURLY_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS span_metrics_hourly_mv
TO span_metrics_hourly
AS
SELECT
    project_id,
    toStartOfHour(created_at) AS hour,
    observation_type,
    model,
    status,

    count()                                          AS span_count,
    uniqState(trace_id)                              AS trace_count,
    countIf(status = 'ERROR')                        AS error_count,

    sum(toInt64(ifNull(prompt_tokens, 0)))      AS total_prompt_tokens,
    sum(toInt64(ifNull(completion_tokens, 0)))  AS total_completion_tokens,
    sum(toInt64(ifNull(total_tokens, 0)))       AS total_tokens,

    quantilesState(0.5, 0.90, 0.95, 0.99)(
        toFloat64(ifNull(latency_ms, 0))
    ) AS latency_quantile,

    sum(ifNull(cost, 0))                        AS total_cost

FROM spans
WHERE _peerdb_is_deleted = 0
GROUP BY
    project_id,
    hour,
    observation_type,
    model,
    status;
"""

# ---------------------------------------------------------------------------
# eval_metrics_hourly — AggregatingMergeTree
# Dimensions: custom_eval_config_id, project_id (resolved via trace dict),
#             hour
# ---------------------------------------------------------------------------
EVAL_METRICS_HOURLY_TABLE = """
CREATE TABLE IF NOT EXISTS eval_metrics_hourly (
    -- Dimensions
    custom_eval_config_id Nullable(UUID),
    project_id UUID,
    hour DateTime,

    -- Aggregates
    eval_count SimpleAggregateFunction(sum, UInt64),
    float_sum SimpleAggregateFunction(sum, Float64),
    float_count SimpleAggregateFunction(sum, UInt64),
    bool_pass SimpleAggregateFunction(sum, UInt64),
    bool_fail SimpleAggregateFunction(sum, UInt64),
    error_count SimpleAggregateFunction(sum, UInt64)
)
ENGINE = ReplicatedAggregatingMergeTree('/clickhouse/tables/{shard}/eval_metrics_hourly', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, custom_eval_config_id, hour)
TTL hour + INTERVAL 365 DAY
SETTINGS index_granularity = 8192, allow_nullable_key = 1;
"""

EVAL_METRICS_HOURLY_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS eval_metrics_hourly_mv
TO eval_metrics_hourly
AS
SELECT
    coalesce(e.custom_eval_config_id, toUUID('00000000-0000-0000-0000-000000000000')) AS custom_eval_config_id,
    -- Resolve project_id from the parent trace via the dictionary
    dictGetOrDefault('trace_dict', 'project_id', toUUID(e.trace_id), toUUID('00000000-0000-0000-0000-000000000000')) AS project_id,
    toStartOfHour(e.created_at)                                    AS hour,

    count()                                                        AS eval_count,
    ifNull(sumIf(e.output_float, e.output_float IS NOT NULL), 0)   AS float_sum,
    countIf(e.output_float IS NOT NULL)                            AS float_count,
    countIf(e.output_bool = 1)                                     AS bool_pass,
    countIf(e.output_bool = 0 AND e.output_bool IS NOT NULL)       AS bool_fail,
    countIf(e.error = 1)                                           AS error_count

FROM tracer_eval_logger AS e
WHERE e._peerdb_is_deleted = 0
GROUP BY
    custom_eval_config_id,
    project_id,
    hour;
"""

# ============================================================================
# LAYER 4 — Dataset Analytics (CDC + Dictionaries + View + MVs)
# ============================================================================

# ---------------------------------------------------------------------------
# 4a. CDC landing tables for model_hub tables
# ---------------------------------------------------------------------------
CDC_MODEL_HUB_DATASET = """
CREATE TABLE IF NOT EXISTS model_hub_dataset (
    id UUID,
    name String,
    source String,
    organization_id UUID,
    workspace_id UUID,
    user_id Nullable(UUID),
    column_order Array(String),
    model_type Nullable(String),
    column_config String DEFAULT '{}',
    dataset_config String DEFAULT '{}',
    synthetic_dataset_config String DEFAULT '{}',
    eval_reasons String DEFAULT '{}',
    eval_reason_last_updated Nullable(DateTime64(3)),
    eval_reason_status Nullable(String),
    created_at DateTime64(6),
    updated_at DateTime64(6),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_dataset', '{replica}', _peerdb_version)
ORDER BY id;
"""

CDC_MODEL_HUB_COLUMN = """
CREATE TABLE IF NOT EXISTS model_hub_column (
    id UUID,
    name String,
    data_type String,
    dataset_id UUID,
    source String,
    source_id Nullable(String),
    metadata String DEFAULT '{}',
    status String DEFAULT '',
    created_at DateTime64(6),
    updated_at DateTime64(6),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_column', '{replica}', _peerdb_version)
ORDER BY (dataset_id, id);
"""

CDC_MODEL_HUB_ROW = """
CREATE TABLE IF NOT EXISTS model_hub_row (
    id UUID,
    dataset_id UUID,
    `order` UInt32,
    metadata String DEFAULT '{}',
    created_at DateTime64(6),
    updated_at DateTime64(6),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_row', '{replica}', _peerdb_version)
ORDER BY (dataset_id, id);
"""

CDC_MODEL_HUB_CELL = """
CREATE TABLE IF NOT EXISTS model_hub_cell (
    id UUID,
    dataset_id UUID,
    column_id UUID,
    row_id UUID,
    value String DEFAULT '',
    value_infos String DEFAULT '[]',
    feedback_info String DEFAULT '{}',
    status String DEFAULT '',
    column_metadata String DEFAULT '{}',
    prompt_tokens Nullable(UInt32),
    completion_tokens Nullable(UInt32),
    response_time Nullable(Float64),
    created_at DateTime64(6),
    updated_at DateTime64(6),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_cell', '{replica}', _peerdb_version)
ORDER BY (dataset_id, column_id, row_id);
"""

# ---------------------------------------------------------------------------
# 4b. Dictionaries for dataset lookups
# ---------------------------------------------------------------------------
COLUMN_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS column_dict (
    id UUID,
    name String,
    data_type String,
    dataset_id UUID,
    source String,
    source_id Nullable(String)
) PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, name, data_type, dataset_id, source, source_id FROM {_CH_DATABASE}.model_hub_column WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"""

DATASET_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS dataset_dict (
    id UUID,
    name String,
    organization_id UUID,
    workspace_id UUID
) PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, name, organization_id, workspace_id FROM {_CH_DATABASE}.model_hub_dataset WHERE _peerdb_is_deleted = 0 AND deleted = 0'
    DB '{_CH_DATABASE}'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"""

# ---------------------------------------------------------------------------
# 4c. Denormalized dataset cells view
# ---------------------------------------------------------------------------
DATASET_CELLS_VIEW = """
CREATE VIEW IF NOT EXISTS dataset_cells AS
SELECT
    c.id,
    c.dataset_id,
    c.column_id,
    c.row_id,
    c.value,
    c.status,
    c.prompt_tokens,
    c.completion_tokens,
    c.response_time,
    c.created_at,
    c.updated_at,
    dictGet('column_dict', 'name', c.column_id) AS column_name,
    dictGet('column_dict', 'data_type', c.column_id) AS column_data_type,
    dictGet('column_dict', 'source', c.column_id) AS column_source,
    dictGet('column_dict', 'source_id', c.column_id) AS column_source_id,
    dictGet('dataset_dict', 'name', c.dataset_id) AS dataset_name,
    dictGet('dataset_dict', 'organization_id', c.dataset_id) AS org_id,
    dictGet('dataset_dict', 'workspace_id', c.dataset_id) AS workspace_id,
    if(column_data_type IN ('float', 'integer') OR column_source = 'evaluation',
       toFloat64OrNull(c.value), NULL) AS value_float,
    if(lower(c.value) IN ('true', 'pass', 'passed', '1'), 1,
       if(lower(c.value) IN ('false', 'fail', 'failed', '0'), 0, NULL)) AS value_bool
FROM model_hub_cell AS c FINAL
WHERE c._peerdb_is_deleted = 0;
"""

# ============================================================================
# LAYER 5 — Simulation Analytics (CDC + Dictionaries + Denormalized View)
# ============================================================================

# ---------------------------------------------------------------------------
# 5a. CDC landing tables for simulation tables
# ---------------------------------------------------------------------------

CDC_SIMULATE_TEST_EXECUTION = """
CREATE TABLE IF NOT EXISTS simulate_test_execution (
    id UUID,

    -- Relationships
    run_test_id Nullable(UUID),
    simulator_agent_id Nullable(UUID),
    agent_definition_id Nullable(UUID),
    agent_version_id Nullable(UUID),
    agent_optimiser_id Nullable(UUID),

    -- Status & Timing
    status LowCardinality(String),
    started_at Nullable(DateTime64(3)),
    completed_at Nullable(DateTime64(3)),

    -- Call counts
    total_scenarios Int32 DEFAULT 0,
    total_calls Int32 DEFAULT 0,
    completed_calls Int32 DEFAULT 0,
    failed_calls Int32 DEFAULT 0,

    -- Metadata
    scenario_ids String DEFAULT '[]',
    execution_metadata String DEFAULT '{}',
    error_reason Nullable(String),

    -- Eval summaries
    eval_explanation_summary String DEFAULT '{}',
    eval_explanation_summary_status LowCardinality(Nullable(String)),
    eval_explanation_summary_last_updated Nullable(DateTime64(3)),

    -- Executor
    picked_up_by_executor UInt8 DEFAULT 0,

    -- Soft-delete
    deleted_at Nullable(DateTime64(3)),

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_run_test_id run_test_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_agent_definition_id agent_definition_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_agent_version_id agent_version_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_status status TYPE set(10) GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/simulate_test_execution', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (agent_definition_id, created_at, id)
SETTINGS index_granularity = 8192, allow_nullable_key = 1;
"""

CDC_SIMULATE_CALL_EXECUTION = """
CREATE TABLE IF NOT EXISTS simulate_call_execution (
    id UUID,

    -- Relationships
    test_execution_id UUID,
    scenario_id UUID,
    agent_version_id Nullable(UUID),

    -- Call type & communication
    simulation_call_type LowCardinality(String) DEFAULT 'voice',
    phone_number Nullable(String),
    customer_number Nullable(String),

    -- Status & timeline
    status LowCardinality(String),
    started_at Nullable(DateTime64(3)),
    completed_at Nullable(DateTime64(3)),
    ended_at Nullable(DateTime64(3)),
    duration_seconds Nullable(Int32),

    -- Cost breakdown (cents)
    cost_cents Nullable(Int32),
    stt_cost_cents Nullable(Int32),
    llm_cost_cents Nullable(Int32),
    tts_cost_cents Nullable(Int32),
    customer_cost_cents Nullable(Int32),

    -- Performance metrics
    overall_score Nullable(Float64),
    response_time_ms Nullable(Int32),
    message_count Nullable(Int32),

    -- Conversation metrics
    avg_agent_latency_ms Nullable(Int32),
    user_interruption_count Nullable(Int32),
    user_interruption_rate Nullable(Float64),
    user_wpm Nullable(Float64),
    bot_wpm Nullable(Float64),
    talk_ratio Nullable(Float64),
    ai_interruption_count Nullable(Int32),
    ai_interruption_rate Nullable(Float64),
    avg_stop_time_after_interruption_ms Nullable(Int32),

    -- Evaluation outputs (JSONB -> String)
    eval_outputs String DEFAULT '{}',
    tool_outputs String DEFAULT '{}',

    -- Call metadata (JSONB -> String, contains persona, row_data, prompts)
    call_metadata String DEFAULT '{}' CODEC(ZSTD(3)),

    -- Tracking
    ended_reason Nullable(String),
    error_message Nullable(String),
    call_summary Nullable(String) CODEC(ZSTD(3)),
    transcript_available UInt8 DEFAULT 0,
    recording_available UInt8 DEFAULT 0,

    -- Dataset row reference
    row_id Nullable(UUID),

    -- Additional fields from PG
    recording_url Nullable(String),
    call_type Nullable(String),
    assistant_id Nullable(String),
    vapi_cost_cents Nullable(Int32),
    storage_cost_cents Nullable(Float64),
    analysis_data String DEFAULT '{}',
    evaluation_data String DEFAULT '{}',
    stereo_recording_url Nullable(String),
    monitor_call_data String DEFAULT '{}',
    conversation_metrics_data String DEFAULT '{}',
    customer_cost_breakdown String DEFAULT '{}',
    customer_latency_metrics String DEFAULT '{}',
    customer_log_url Nullable(String),
    customer_logs_summary String DEFAULT '{}',
    logs_ingested_at Nullable(DateTime64(3)),
    logs_summary String DEFAULT '{}',
    customer_call_id Nullable(String),
    provider_call_data String DEFAULT '{}',
    service_provider_call_id Nullable(String),

    -- Soft-delete
    deleted_at Nullable(DateTime64(3)),

    -- Timestamps
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_test_execution_id test_execution_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_scenario_id scenario_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_agent_version_id agent_version_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_status status TYPE set(10) GRANULARITY 1,
    INDEX idx_call_type simulation_call_type TYPE set(5) GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/simulate_call_execution', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (scenario_id, created_at, id)
SETTINGS index_granularity = 8192;
"""

# ---------------------------------------------------------------------------
# 5b. Dictionaries for simulation lookups
# ---------------------------------------------------------------------------

SIMULATE_SCENARIO_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS simulate_scenario_dict (
    id UUID,
    name String,
    scenario_type String,
    agent_definition_id Nullable(UUID),
    organization_id UUID,
    workspace_id Nullable(UUID)
) PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, name, scenario_type, agent_definition_id, organization_id, workspace_id FROM {_CH_DATABASE}.simulate_scenarios WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"""

SIMULATE_AGENT_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS simulate_agent_dict (
    id UUID,
    agent_name String,
    agent_type String,
    provider Nullable(String),
    organization_id UUID,
    workspace_id Nullable(UUID)
) PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, agent_name, agent_type, provider, organization_id, workspace_id FROM {_CH_DATABASE}.simulate_agent_definition WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"""

SIMULATE_VERSION_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS simulate_version_dict (
    id UUID,
    version_number UInt32,
    version_name Nullable(String),
    status String,
    agent_definition_id UUID
) PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, version_number, version_name, status, agent_definition_id FROM {_CH_DATABASE}.simulate_agent_version WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"""

SIMULATE_RUN_TEST_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS simulate_run_test_dict (
    id UUID,
    name String,
    agent_definition_id Nullable(UUID),
    agent_version_id Nullable(UUID),
    simulator_agent_id Nullable(UUID),
    organization_id UUID,
    workspace_id Nullable(UUID)
) PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, name, agent_definition_id, agent_version_id, simulator_agent_id, organization_id, workspace_id FROM {_CH_DATABASE}.simulate_run_test WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"""

SIMULATE_TEST_EXECUTION_DICT = f"""
CREATE DICTIONARY IF NOT EXISTS simulate_test_execution_dict (
    id UUID,
    run_test_id Nullable(UUID),
    agent_definition_id Nullable(UUID),
    agent_version_id Nullable(UUID),
    status String
) PRIMARY KEY id
SOURCE(CLICKHOUSE(
    QUERY 'SELECT id, run_test_id, agent_definition_id, agent_version_id, status FROM {_CH_DATABASE}.simulate_test_execution FINAL WHERE _peerdb_is_deleted = 0'
    DB '{_CH_DATABASE}'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"""

# ---------------------------------------------------------------------------
# 5c. CDC landing tables for dimension tables (for dictionaries)
# ---------------------------------------------------------------------------

CDC_SIMULATE_SCENARIOS = """
CREATE TABLE IF NOT EXISTS simulate_scenarios (
    id UUID,
    name String,
    scenario_type LowCardinality(String),
    source_type LowCardinality(String) DEFAULT 'agent_definition',
    source Nullable(String),
    description Nullable(String),
    agent_definition_id Nullable(UUID),
    dataset_id Nullable(UUID),
    simulator_agent_id Nullable(UUID),
    prompt_template_id Nullable(UUID),
    prompt_version_id Nullable(UUID),
    organization_id UUID,
    workspace_id Nullable(UUID),
    metadata String DEFAULT '{}',
    status Nullable(String),
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/simulate_scenarios', '{replica}', _peerdb_version)
ORDER BY id;
"""

CDC_SIMULATE_AGENT_DEFINITION = """
CREATE TABLE IF NOT EXISTS simulate_agent_definition (
    id UUID,
    agent_name String,
    agent_type LowCardinality(String) DEFAULT 'voice',
    provider Nullable(String),
    contact_number Nullable(String),
    inbound UInt8 DEFAULT 0,
    description Nullable(String),
    assistant_id Nullable(String),
    language Nullable(String),
    websocket_url Nullable(String),
    websocket_headers String DEFAULT '{}',
    knowledge_base_id Nullable(UUID),
    api_key Nullable(String),
    observability_provider_id Nullable(UUID),
    authentication_method Nullable(String),
    languages Array(String),
    model Nullable(String),
    model_details String DEFAULT '{}',
    organization_id UUID,
    workspace_id Nullable(UUID),
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/simulate_agent_definition', '{replica}', _peerdb_version)
ORDER BY id;
"""

CDC_SIMULATE_AGENT_VERSION = """
CREATE TABLE IF NOT EXISTS simulate_agent_version (
    id UUID,
    version_number UInt32,
    version_name Nullable(String),
    status LowCardinality(String) DEFAULT 'draft',
    score Nullable(Decimal(3, 1)),
    pass_rate Nullable(Decimal(3, 1)),
    test_count Nullable(Int64),
    description Nullable(String),
    release_notes Nullable(String),
    configuration_snapshot String DEFAULT '{}',
    commit_message Nullable(String),
    agent_definition_id UUID,
    organization_id UUID,
    workspace_id Nullable(UUID),
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/simulate_agent_version', '{replica}', _peerdb_version)
ORDER BY (agent_definition_id, id);
"""

CDC_SIMULATE_RUN_TEST = """
CREATE TABLE IF NOT EXISTS simulate_run_test (
    id UUID,
    name String,
    agent_definition_id Nullable(UUID),
    agent_version_id Nullable(UUID),
    simulator_agent_id Nullable(UUID),
    organization_id UUID,
    workspace_id Nullable(UUID),
    dataset_row_ids Array(String),
    description Nullable(String),
    enable_tool_evaluation UInt8 DEFAULT 0,
    source_type Nullable(String),
    prompt_template_id Nullable(UUID),
    prompt_version_id Nullable(UUID),
    created_at DateTime64(3),
    updated_at DateTime64(3),
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(3)),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64
) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/simulate_run_test', '{replica}', _peerdb_version)
ORDER BY id;
"""

# ---------------------------------------------------------------------------
# 5d. Denormalized simulation calls view
# ---------------------------------------------------------------------------

SIMULATE_CALLS_VIEW = """
CREATE VIEW IF NOT EXISTS simulate_calls AS
SELECT
    c.id,
    c.test_execution_id,
    c.scenario_id,
    c.agent_version_id,
    c.simulation_call_type,
    c.status,
    c.started_at,
    c.completed_at,
    c.ended_at,
    c.duration_seconds,
    c.cost_cents,
    c.stt_cost_cents,
    c.llm_cost_cents,
    c.tts_cost_cents,
    c.customer_cost_cents,
    c.overall_score,
    c.response_time_ms,
    c.message_count,
    c.avg_agent_latency_ms,
    c.user_interruption_count,
    c.user_interruption_rate,
    c.user_wpm,
    c.bot_wpm,
    c.talk_ratio,
    c.ai_interruption_count,
    c.ai_interruption_rate,
    c.avg_stop_time_after_interruption_ms,
    c.eval_outputs,
    c.ended_reason,
    c.error_message,
    c.transcript_available,
    c.recording_available,
    c.row_id,
    c.created_at,
    c.updated_at,
    -- Denormalized scenario fields
    dictGetOrDefault('simulate_scenario_dict', 'name', c.scenario_id, '') AS scenario_name,
    dictGetOrDefault('simulate_scenario_dict', 'scenario_type', c.scenario_id, '') AS scenario_type,
    -- Denormalized agent version fields
    dictGetOrDefault('simulate_version_dict', 'version_number', c.agent_version_id, toUInt32(0)) AS version_number,
    dictGetOrDefault('simulate_version_dict', 'version_name', c.agent_version_id, NULL) AS version_name,
    dictGetOrDefault('simulate_version_dict', 'agent_definition_id', c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')) AS agent_definition_id,
    -- Denormalized agent definition fields
    dictGetOrDefault('simulate_agent_dict', 'agent_name',
        dictGetOrDefault('simulate_version_dict', 'agent_definition_id', c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')),
        '') AS agent_name,
    dictGetOrDefault('simulate_agent_dict', 'agent_type',
        dictGetOrDefault('simulate_version_dict', 'agent_definition_id', c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')),
        '') AS agent_type,
    -- Persona from call_metadata (embedded in row_data during test execution)
    -- Persona is stored as a Python dict string (single-quoted), convert to JSON for parsing
    JSONExtractString(c.call_metadata, 'row_data', 'persona') AS persona_raw,
    if(JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'name') != '',
       JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'name'),
       JSONExtractString(c.call_metadata, 'row_data', 'persona')
    ) AS persona_name,
    -- Parsed persona attributes for analytics breakdown/filtering
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'gender') AS persona_gender,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'age_group') AS persona_age_group,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'location') AS persona_location,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'profession') AS persona_profession,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'personality') AS persona_personality,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'communication_style') AS persona_communication_style,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'accent') AS persona_accent,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'language') AS persona_language,
    JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34)), 'conversation_speed') AS persona_conversation_speed,
    -- Workspace from scenario (for access control)
    dictGetOrDefault('simulate_scenario_dict', 'workspace_id', c.scenario_id, NULL) AS workspace_id,
    dictGetOrDefault('simulate_scenario_dict', 'organization_id', c.scenario_id, toUUID('00000000-0000-0000-0000-000000000000')) AS organization_id
FROM simulate_call_execution AS c FINAL
WHERE c._peerdb_is_deleted = 0;
"""

# ---------------------------------------------------------------------------
# Layer 6: Usage / Eval analytics — APICallLog
#   Central eval results table. Every eval execution (tracer, dataset,
#   simulation, SDK, playground) writes here with source_id = eval_template_id.
# ---------------------------------------------------------------------------

CDC_USAGE_APICALLLOG = """
CREATE TABLE IF NOT EXISTS usage_apicalllog (
    id Int64,
    log_id UUID,

    -- Tenant scoping
    organization_id UUID,
    workspace_id Nullable(UUID),
    user_id Nullable(UUID),

    -- Eval execution metadata
    api_call_type_id Nullable(Int64),
    cost Decimal(16, 8) DEFAULT 0,
    deducted_cost Decimal(16, 8) DEFAULT 0,
    status LowCardinality(String) DEFAULT 'not_started',
    refund_parent_id Nullable(String),
    reference_id Nullable(String),

    -- JSON config containing eval output: {"output": {"output": <score>, "reason": "..."}, ...}
    config String DEFAULT '{}',

    -- Materialized columns: pre-extracted from config JSON at insert time.
    -- Config is double-encoded (JSONB string), so JSONExtractString unwraps first.
    eval_score Float64 MATERIALIZED JSONExtractFloat(JSONExtractString(config), 'output', 'output'),
    eval_output_str String MATERIALIZED JSONExtractString(JSONExtractString(config), 'output', 'output'),
    eval_trace_id String MATERIALIZED JSONExtractString(JSONExtractString(config), 'trace_id'),
    eval_dataset_id String MATERIALIZED JSONExtractString(JSONExtractString(config), 'dataset_id'),

    -- Token usage
    input_token_count Nullable(UInt32),

    -- Source tracking: source = where eval ran, source_id = eval_template_id
    source LowCardinality(String) DEFAULT '',
    source_id String DEFAULT '',

    -- Soft-delete
    deleted UInt8 DEFAULT 0,
    deleted_at Nullable(DateTime64(6)),

    -- Timestamps (DateTime64(6) = microsecond precision, matching PG timestamptz
    -- and PeerDB Avro timestamp-micros encoding during initial copy)
    created_at DateTime64(6),
    updated_at DateTime64(6),

    -- PeerDB CDC meta-columns
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted UInt8,
    _peerdb_version Int64,

    -- Secondary indexes
    INDEX idx_source_id source_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_org_id organization_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_status status TYPE set(10) GRANULARITY 1,
    INDEX idx_eval_score eval_score TYPE minmax GRANULARITY 1
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/usage_apicalllog', '{replica}', _peerdb_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (organization_id, source_id, created_at, id)
SETTINGS index_granularity = 8192;
"""

# ============================================================================
# Ordered list of all DDL statements
# ============================================================================

# ---------------------------------------------------------------------------
# 7. falcon_analysis_log
#    Stores conversation history from headless Falcon analysis runs.
#    Not CDC — written directly by the background analysis task.
# ---------------------------------------------------------------------------
FALCON_ANALYSIS_LOG = """
CREATE TABLE IF NOT EXISTS falcon_analysis_log (
    id UUID DEFAULT generateUUIDv4(),
    conversation_id UUID,
    trace_id UUID,
    project_id UUID,
    organization_id UUID,

    -- Agent loop metadata
    model String DEFAULT '',
    mode String DEFAULT '',
    skill_slug String DEFAULT '',
    input_tokens UInt32 DEFAULT 0,
    output_tokens UInt32 DEFAULT 0,

    -- Tool calls log (JSON array)
    tool_calls String DEFAULT '[]',

    -- Final response
    response String DEFAULT '',

    -- Analysis results summary
    errors_found UInt16 DEFAULT 0,
    overall_score Nullable(Float32),
    recommended_priority String DEFAULT '',

    -- Timestamps
    started_at DateTime64(3) DEFAULT now64(),
    completed_at DateTime64(3) DEFAULT now64()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(started_at)
ORDER BY (organization_id, project_id, trace_id, started_at);
"""


SCHEMA_DDL_STATEMENTS: List[Tuple[str, str]] = [
    # Layer 1 — CDC landing tables
    ("tracer_observation_span", CDC_OBSERVATION_SPAN),
    ("tracer_trace", CDC_TRACE),
    ("trace_session", CDC_TRACE_SESSION),
    ("tracer_eval_logger", CDC_EVAL_LOGGER),
    ("trace_annotation", CDC_TRACE_ANNOTATION),
    ("model_hub_score", CDC_MODEL_HUB_SCORE),
    ("tracer_enduser", CDC_TRACER_ENDUSER),
    # Layer 2 — Dictionaries + denormalized spans
    ("trace_dict", TRACE_DICT),
    ("enduser_dict", ENDUSER_DICT),
    ("model_hub_promptversion", CDC_MODEL_HUB_PROMPTVERSION),
    ("model_hub_prompttemplate", CDC_MODEL_HUB_PROMPTTEMPLATE),
    ("model_hub_promptlabel", CDC_MODEL_HUB_PROMPTLABEL),
    ("prompt_lookup", PROMPT_LOOKUP_TABLE),
    ("prompt_dict", PROMPT_DICT),
    ("prompt_label_lookup", PROMPT_LABEL_LOOKUP_TABLE),
    ("prompt_label_dict", PROMPT_LABEL_DICT),
    ("spans", SPANS_TABLE),
    ("spans_mv", SPANS_MV),
    # Layer 3 — Pre-aggregated rollups
    ("span_metrics_hourly", SPAN_METRICS_HOURLY_TABLE),
    ("span_metrics_hourly_mv", SPAN_METRICS_HOURLY_MV),
    ("eval_metrics_hourly", EVAL_METRICS_HOURLY_TABLE),
    ("eval_metrics_hourly_mv", EVAL_METRICS_HOURLY_MV),
    # Layer 4 — Dataset analytics
    ("model_hub_dataset", CDC_MODEL_HUB_DATASET),
    ("model_hub_column", CDC_MODEL_HUB_COLUMN),
    ("model_hub_row", CDC_MODEL_HUB_ROW),
    ("model_hub_cell", CDC_MODEL_HUB_CELL),
    ("column_dict", COLUMN_DICT),
    ("dataset_dict", DATASET_DICT),
    ("dataset_cells", DATASET_CELLS_VIEW),
    # Layer 5 — Simulation analytics (dimension tables first, then CDC, then dicts, then view)
    ("simulate_scenarios", CDC_SIMULATE_SCENARIOS),
    ("simulate_agent_definition", CDC_SIMULATE_AGENT_DEFINITION),
    ("simulate_agent_version", CDC_SIMULATE_AGENT_VERSION),
    ("simulate_run_test", CDC_SIMULATE_RUN_TEST),
    ("simulate_test_execution", CDC_SIMULATE_TEST_EXECUTION),
    ("simulate_call_execution", CDC_SIMULATE_CALL_EXECUTION),
    ("simulate_scenario_dict", SIMULATE_SCENARIO_DICT),
    ("simulate_agent_dict", SIMULATE_AGENT_DICT),
    ("simulate_version_dict", SIMULATE_VERSION_DICT),
    ("simulate_run_test_dict", SIMULATE_RUN_TEST_DICT),
    ("simulate_test_execution_dict", SIMULATE_TEST_EXECUTION_DICT),
    ("simulate_calls", SIMULATE_CALLS_VIEW),
    # Layer 6 — Usage / Eval analytics
    ("usage_apicalllog", CDC_USAGE_APICALLLOG),
    # Layer 7 — Falcon analysis conversation logs
    ("falcon_analysis_log", FALCON_ANALYSIS_LOG),
]


# ============================================================================
# Public helpers
# ============================================================================


# ALTER statements that must run immediately after their base table is ensured,
# before dependent dictionaries/views are created. CREATE TABLE IF NOT EXISTS
# does not reconcile older local/PeerDB-created tables with newer DDL.
PRE_DDL_ALTERS_BY_TABLE: dict[str, List[str]] = {
    "tracer_observation_span": [
        "ALTER TABLE tracer_observation_span ADD COLUMN IF NOT EXISTS "
        "schema_version String DEFAULT '1.0' AFTER semconv_source",
    ],
}


# Post-DDL ALTER statements to ensure materialized columns exist on CDC tables
# that PeerDB may recreate without them during RESYNC operations.
POST_DDL_ALTERS: List[str] = [
    "ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS "
    "eval_score Float64 MATERIALIZED "
    "JSONExtractFloat(JSONExtractString(config), 'output', 'output')",
    "ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS "
    "eval_output_str String MATERIALIZED "
    "JSONExtractString(JSONExtractString(config), 'output', 'output')",
    "ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS "
    "eval_trace_id String MATERIALIZED "
    "JSONExtractString(JSONExtractString(config), 'trace_id')",
    "ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS "
    "eval_dataset_id String MATERIALIZED "
    "JSONExtractString(JSONExtractString(config), 'dataset_id')",
]


def get_all_schema_ddl() -> List[Tuple[str, str]]:
    """Return all schema DDL statements in correct creation order.

    The order ensures that dependencies are satisfied:
      1. CDC landing tables (no deps)
      2. trace_dict (depends on tracer_trace)
      3. spans + spans_mv (depends on tracer_observation_span + trace_dict)
      4. span_metrics_hourly + MV (depends on spans)
      5. eval_metrics_hourly + MV (depends on tracer_eval_logger + trace_dict)
    """
    if _USE_REPLICATED_ENGINES:
        return list(SCHEMA_DDL_STATEMENTS)

    return [
        (name, _to_single_node_engine(ddl))
        for name, ddl in SCHEMA_DDL_STATEMENTS
    ]


def get_drop_statements() -> List[str]:
    """Return DROP statements in reverse dependency order for clean teardown.

    Materialized views are dropped first so that the underlying target
    tables and dictionaries can be removed safely.
    """
    drops: List[str] = []
    for name, _ in reversed(SCHEMA_DDL_STATEMENTS):
        if name.endswith("_mv"):
            drops.append(f"DROP VIEW IF EXISTS {name};")
        elif name.endswith("_dict"):
            drops.append(f"DROP DICTIONARY IF EXISTS {name};")
        elif name == "dataset_cells":
            drops.append(f"DROP VIEW IF EXISTS {name};")
        else:
            drops.append(f"DROP TABLE IF EXISTS {name};")
    return drops


def get_backfill_statements() -> List[str]:
    """Return INSERT … SELECT statements for initial backfill of derived tables.

    After the CDC landing tables have been populated by PeerDB, run these
    statements once to seed the ``spans`` table with all historical data.
    The materialized view (spans_mv) will handle future incremental inserts
    automatically.

    Note: The hourly rollup tables (span_metrics_hourly, eval_metrics_hourly)
    are populated via their own MVs reading from ``spans`` and
    ``tracer_eval_logger`` respectively, so they will be filled as part of
    this backfill cascade.
    """
    backfill_spans = """
INSERT INTO spans
SELECT
    s.id,
    s.trace_id,
    s.project_id,
    s.project_version_id,
    s.org_id,
    s.parent_span_id,

    s.name,
    s.observation_type,
    s.operation_name,
    s.status,
    s.status_message,

    s.start_time,
    s.end_time,
    s.latency_ms,

    s.model,
    s.provider,
    s.prompt_tokens,
    s.completion_tokens,
    s.total_tokens,
    s.cost,

    s.input,
    s.output,

    -- String attributes
    mapFromArrays(
        arrayFilter(
            (k) -> JSONExtractFloat(s.span_attributes, k) = 0
                AND JSONExtractRaw(s.span_attributes, k) NOT IN ('0', '0.0', 'true', 'false'),
            JSONExtractKeys(s.span_attributes)
        ),
        arrayMap(
            (k) -> JSONExtractString(s.span_attributes, k),
            arrayFilter(
                (k) -> JSONExtractFloat(s.span_attributes, k) = 0
                    AND JSONExtractRaw(s.span_attributes, k) NOT IN ('0', '0.0', 'true', 'false'),
                JSONExtractKeys(s.span_attributes)
            )
        )
    ),

    -- Numeric attributes
    mapFromArrays(
        arrayFilter(
            (k) -> JSONExtractFloat(s.span_attributes, k) != 0
                OR JSONExtractRaw(s.span_attributes, k) IN ('0', '0.0'),
            arrayFilter(
                (k) -> JSONExtractRaw(s.span_attributes, k) NOT IN ('true', 'false'),
                JSONExtractKeys(s.span_attributes)
            )
        ),
        arrayMap(
            (k) -> JSONExtractFloat(s.span_attributes, k),
            arrayFilter(
                (k) -> JSONExtractFloat(s.span_attributes, k) != 0
                    OR JSONExtractRaw(s.span_attributes, k) IN ('0', '0.0'),
                arrayFilter(
                    (k) -> JSONExtractRaw(s.span_attributes, k) NOT IN ('true', 'false'),
                    JSONExtractKeys(s.span_attributes)
                )
            )
        )
    ),

    -- Boolean attributes
    mapFromArrays(
        arrayFilter(
            (k) -> JSONExtractRaw(s.span_attributes, k) IN ('true', 'false'),
            JSONExtractKeys(s.span_attributes)
        ),
        arrayMap(
            (k) -> toUInt8(JSONExtractBool(s.span_attributes, k)),
            arrayFilter(
                (k) -> JSONExtractRaw(s.span_attributes, k) IN ('true', 'false'),
                JSONExtractKeys(s.span_attributes)
            )
        )
    ),

    -- Raw JSON
    s.span_attributes,
    s.resource_attributes,

    -- Metadata map
    mapFromArrays(
        JSONExtractKeys(s.metadata),
        arrayMap(
            (k) -> JSONExtractString(s.metadata, k),
            JSONExtractKeys(s.metadata)
        )
    ),

    s.tags,
    s.span_events,
    s.end_user_id,
    s.custom_eval_config_id,
    s.semconv_source,
    s.schema_version,

    -- Trace context via dictionary
    dictGetOrDefault('trace_dict', 'name',        toUUID(s.trace_id), NULL),
    dictGetOrDefault('trace_dict', 'session_id',  toUUID(s.trace_id), NULL),
    dictGetOrDefault('trace_dict', 'external_id', toUUID(s.trace_id), NULL),
    dictGetOrDefault('trace_dict', 'tags',        toUUID(s.trace_id), '[]'),

    s.created_at,
    s.updated_at,
    s._peerdb_is_deleted,
    s._peerdb_version

FROM tracer_observation_span AS s
WHERE s._peerdb_is_deleted = 0;
"""
    return [backfill_spans.strip()]
