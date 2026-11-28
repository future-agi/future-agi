-- Immutable current span activity, not persisted user/window metrics.
-- Reassignments retract the old canonical-user key in the same attempt.
-- Read only an exact pinned manifest allowlist; FINAL is not authority.
-- cluster-shard-key: list_current_user_activity = cityHash64(project_id)
CREATE TABLE IF NOT EXISTS list_current_user_activity
(
    org_id UUID,
    project_id UUID,
    observation_type LowCardinality(String),
    service_name LowCardinality(String),
    start_hour DateTime('UTC'),
    trace_id String,
    span_id String,
    unit_start DateTime64(6, 'UTC'),
    end_time Nullable(DateTime64(6, 'UTC')),
    raw_session_id String,
    canonical_session_id String,
    raw_user_id String,
    canonical_user_id String,
    cost Float64,
    total_tokens Int32,
    prompt_tokens Int32,
    completion_tokens Int32,
    source_version UInt64,
    is_deleted UInt8,
    generation UInt64,
    attempt_id UUID,
    _version UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, project_id, canonical_user_id, observation_type, service_name, start_hour, trace_id, span_id, generation, attempt_id);
