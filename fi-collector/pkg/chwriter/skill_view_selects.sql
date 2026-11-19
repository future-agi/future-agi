-- Code generated from 028_skill_attribution.sql by go generate; DO NOT EDIT.
WITH
formatQuerySingleLine(concat($mv$SELECT project_id, trace_id, id AS span_id, start_time,
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
FROM $mv$, currentDatabase(), $mv$.spans
WHERE name = 'skill.load' AND attrs_string['fi.skill.version_id'] != ''$mv$)) AS expected_links,
formatQuerySingleLine(concat($mv$SELECT project_id, trace_id, id AS span_id, start_time,
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
FROM $mv$, currentDatabase(), $mv$.spans
WHERE name = 'skill.load' AND status = 'ERROR' AND attrs_string['fi.skill.version_id'] = ''$mv$)) AS expected_failures
