-- Dataset Analytics: CDC Table Definitions
-- These tables are populated by PeerDB CDC from PostgreSQL model_hub tables.

-- Replicated from model_hub.Dataset
CREATE TABLE IF NOT EXISTS model_hub_dataset (
    id UUID,
    name String,
    source String,
    organization_id UUID,
    workspace_id UUID,
    created_at DateTime64(6),
    updated_at DateTime64(6),
    deleted UInt8 DEFAULT 0,
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplacingMergeTree(_peerdb_version)
ORDER BY id;

-- Replicated from model_hub.Column
CREATE TABLE IF NOT EXISTS model_hub_column (
    id UUID,
    name String,
    data_type String,
    dataset_id UUID,
    source String,
    source_id Nullable(String),
    metadata String DEFAULT '{}',
    status String DEFAULT '',
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplacingMergeTree(_peerdb_version)
ORDER BY (dataset_id, id);

-- Replicated from model_hub.Row
CREATE TABLE IF NOT EXISTS model_hub_row (
    id UUID,
    dataset_id UUID,
    `order` UInt32,
    source_trace_id Nullable(UUID),
    source_span_id Nullable(String),
    metadata String DEFAULT '{}',
    created_at DateTime64(6),
    updated_at DateTime64(6),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplacingMergeTree(_peerdb_version)
ORDER BY (dataset_id, id);

-- Replicated from model_hub.Cell
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
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplacingMergeTree(_peerdb_version)
ORDER BY (dataset_id, column_id, row_id);

-- Replicated from model_hub.UserEvalMetric
CREATE TABLE IF NOT EXISTS model_hub_userevalmetric (
    id UUID,
    name String,
    template_id UUID,
    dataset_id UUID,
    config String DEFAULT '{}',
    status String DEFAULT '',
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplacingMergeTree(_peerdb_version)
ORDER BY (dataset_id, id);

-- Replicated from model_hub.Feedback
CREATE TABLE IF NOT EXISTS model_hub_feedback (
    id UUID,
    source String,
    source_id Nullable(String),
    user_eval_metric_id Nullable(UUID),
    eval_template_id Nullable(UUID),
    value String DEFAULT '',
    explanation String DEFAULT '',
    row_id Nullable(String),
    organization_id UUID,
    workspace_id Nullable(UUID),
    created_at DateTime64(6),
    _peerdb_synced_at DateTime64(6),
    _peerdb_is_deleted Int8 DEFAULT 0,
    _peerdb_version Int64
) ENGINE = ReplacingMergeTree(_peerdb_version)
ORDER BY (organization_id, eval_template_id, row_id);
