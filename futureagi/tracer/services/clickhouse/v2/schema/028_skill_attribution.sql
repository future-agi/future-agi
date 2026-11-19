-- Runtime load evidence, not fetch accounting or proof of model use.
-- Like 022, this only adds metadata for existing parts on the boot path.
-- Historical rows are handled by ch25_backfill_skill_trace_links, off boot.
-- Readers must collapse revisions (FINAL or argMax) before excluding tombstones.
-- Full-row replays must preserve the load identity and start_time ordering key.

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS skill_version_ids Array(String)
    MATERIALIZED if(position(attributes_extra, '"fi.skills.version_ids"') > 0,
                    JSONExtract(attributes_extra, 'fi.skills.version_ids', 'Array(String)'), []);

ALTER TABLE spans
    ADD INDEX IF NOT EXISTS idx_skill_version_ids skill_version_ids
    TYPE bloom_filter(0.01) GRANULARITY 1;

CREATE TABLE IF NOT EXISTS skill_trace_links
(
    project_id          UUID,
    trace_id            String,
    span_id             String,
    start_time          DateTime64(6, 'UTC'),
    skill_id            String,
    skill_version_id    String,
    skill_name          String,
    skill_version       UInt32,
    skill_digest        String,
    requested_label     String,
    requested_version   UInt32,
    load_via            LowCardinality(String),
    evidence            LowCardinality(String),
    cached              UInt8,
    root_file           UInt8,
    status              LowCardinality(String),
    service_name        LowCardinality(String),
    agent_name          String,
    context_id          String,
    context_version_id  String,
    via_context_version_id String,
    binding_id          String,
    is_deleted          UInt8,
    _version            UInt64,
    INDEX idx_stl_version skill_version_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_stl_agent   agent_name       TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_stl_context context_id       TYPE bloom_filter(0.01) GRANULARITY 1,
    PROJECTION by_trace (SELECT * ORDER BY project_id, trace_id, span_id)
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toYYYYMM(start_time)
ORDER BY (project_id, skill_id, skill_version_id, toStartOfHour(start_time), trace_id, span_id)
SETTINGS deduplicate_merge_projection_mode = 'rebuild';

CREATE MATERIALIZED VIEW IF NOT EXISTS skill_trace_links_mv TO skill_trace_links AS
SELECT project_id, trace_id, id AS span_id, start_time,
       attrs_string['fi.skill.id']                         AS skill_id,
       attrs_string['fi.skill.version_id']                 AS skill_version_id,
       attrs_string['fi.skill.name']                       AS skill_name,
       toUInt32(attrs_number['fi.skill.version'])          AS skill_version,
       attrs_string['fi.skill.digest']                     AS skill_digest,
       attrs_string['fi.skill.requested_label']            AS requested_label,
       toUInt32(attrs_number['fi.skill.requested_version']) AS requested_version,
       attrs_string['fi.skill.load.via']                   AS load_via,
       attrs_string['fi.skill.load.evidence']              AS evidence,
       toUInt8(attrs_bool['fi.skill.load.cached'])         AS cached,
       toUInt8(attrs_bool['fi.skill.load.root_file'])      AS root_file,
       status, service_name,
       if(attrs_string['gen_ai.agent.name'] != '', attrs_string['gen_ai.agent.name'],
          attrs_string['fi.context.name'])                 AS agent_name,
       attrs_string['fi.context.id']                       AS context_id,
       attrs_string['fi.context.version_id']               AS context_version_id,
       attrs_string['fi.skill.via_context.version_id']     AS via_context_version_id,
       attrs_string['fi.context.binding_id']               AS binding_id,
       is_deleted, _version
FROM spans
WHERE name = 'skill.load' AND attrs_string['fi.skill.version_id'] != '';

-- Failed loads have no verified version/id/digest. Keep their name-level
-- evidence separate; a context.load alone must populate neither table.
CREATE TABLE IF NOT EXISTS skill_load_failures
(
    project_id          UUID,
    trace_id            String,
    span_id             String,
    start_time          DateTime64(6, 'UTC'),
    skill_name          String,
    requested_label     String,
    requested_version   UInt32,
    load_via            LowCardinality(String),
    error_type          LowCardinality(String),
    service_name        LowCardinality(String),
    agent_name          String,
    context_id          String,
    context_version_id  String,
    is_deleted          UInt8,
    _version            UInt64,
    INDEX idx_slf_name skill_name TYPE bloom_filter(0.01) GRANULARITY 1,
    PROJECTION by_trace (SELECT * ORDER BY project_id, trace_id, span_id)
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toYYYYMM(start_time)
ORDER BY (project_id, skill_name, toStartOfHour(start_time), trace_id, span_id)
SETTINGS deduplicate_merge_projection_mode = 'rebuild';

CREATE MATERIALIZED VIEW IF NOT EXISTS skill_load_failures_mv TO skill_load_failures AS
SELECT project_id, trace_id, id AS span_id, start_time,
       attrs_string['fi.skill.name']                       AS skill_name,
       attrs_string['fi.skill.requested_label']            AS requested_label,
       toUInt32(attrs_number['fi.skill.requested_version']) AS requested_version,
       attrs_string['fi.skill.load.via']                   AS load_via,
       attrs_string['error.type']                          AS error_type,
       service_name,
       if(attrs_string['gen_ai.agent.name'] != '', attrs_string['gen_ai.agent.name'],
          attrs_string['fi.context.name'])                 AS agent_name,
       attrs_string['fi.context.id']                       AS context_id,
       attrs_string['fi.context.version_id']               AS context_version_id,
       is_deleted, _version
FROM spans
WHERE name = 'skill.load' AND status = 'ERROR' AND attrs_string['fi.skill.version_id'] = '';
