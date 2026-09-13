"""Independent FINAL selected-page field comparison, never an HTTP oracle.

Called AFTER candidate timing and the unchanged independent trace ID oracle.
No application builder, hydration/merge helper, candidate root keys, native
engine, transport, cache or settings override is imported here.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from uuid import UUID

import replay_observe_filters as replay

CONTRACT = 'independent_FINAL_trace_query_layer_fields.v1'
MAX_PAGE_ROWS = 100
MAX_ATTRIBUTE_KEYS = 100
MAX_COORDINATES = 10_000
UNSUPPORTED = ['PG_configuration', 'end_user_labels_and_remap', 'eval_cells',
               'annotation_cells', 'other_relational_fields', 'HTTP_serializer_and_preview',
               'UI', 'pagination', 'same_transaction_snapshot']
SETTINGS = dict(optimize_move_to_prewhere=0, optimize_move_to_prewhere_if_final=0,
                use_skip_indexes_if_final=0,
                enable_optimize_predicate_expression_to_final_subquery=0,
                query_plan_merge_expressions=0, max_execution_time=15,
                max_bytes_to_read=2 * 1024**3, max_memory_usage=512 * 1024**2,
                max_result_rows=MAX_PAGE_ROWS * MAX_ATTRIBUTE_KEYS, max_result_bytes=32 * 1024**2,
                result_overflow_mode='throw', read_overflow_mode='throw')
ROOT_FIELDS = ('project_id trace_id root_span_id start_time observation_type '
               '_root_observation_type _root_service_name _root_start_hour _root_version '
               'trace_name span_name status end_time latency_ms cost total_tokens '
               'prompt_tokens completion_tokens model provider trace_session_id input output '
               'attributes_extra attrs_string attrs_number attrs_bool metadata trace_tags').split()
JSON_FIELDS = {'attributes_extra', 'metadata', 'trace_tags'}
PHYSICAL_PREFIX = '(project_id, observation_type, service_name, toStartOfHour(start_time))'
COORDINATE_PREDICATE = PHYSICAL_PREFIX + ' IN %(ref_prefixes)s'
COORDINATE_SQL = f"""
SELECT DISTINCT project_id, observation_type, service_name,
    toStartOfHour(start_time) AS coordinate_hour, trace_id, id
FROM spans
PREWHERE project_id=%(ref_project_id)s AND trace_id IN %(ref_ids)s
LIMIT {MAX_COORDINATES + 1}
"""
ROOT_SQL = f"""
SELECT toString(project_id) AS project_id, trace_id, id AS root_span_id,
    start_time, observation_type, observation_type AS _root_observation_type,
    service_name AS _root_service_name, toStartOfHour(start_time) AS _root_start_hour,
    _version AS _root_version, trace_name, name AS span_name, status, end_time,
    latency_ms, cost, total_tokens, prompt_tokens, completion_tokens, model,
    provider, trace_session_id, input, output, attributes_extra,
    attrs_string, attrs_number, attrs_bool, toJSONString(metadata) AS metadata
FROM (
    -- SELECT * can omit MATERIALIZED/ALIAS columns required by the outer query.
    SELECT project_id, trace_id, id, start_time, observation_type, service_name,
        _version, trace_name, name, status, end_time, latency_ms, cost,
        total_tokens, prompt_tokens, completion_tokens, model, provider,
        trace_session_id, input, output, attributes_extra, attrs_string,
        attrs_number, attrs_bool, metadata
    FROM spans FINAL
    PREWHERE project_id=%(ref_project_id)s AND trace_id IN %(ref_ids)s
      AND {COORDINATE_PREDICATE}
    WHERE is_deleted=0 AND (parent_span_id IS NULL OR parent_span_id='')
      AND start_time>=fromUnixTimestamp64Micro(%(ref_start_us)s)
      AND start_time<fromUnixTimestamp64Micro(%(ref_end_us)s)
    ORDER BY start_time DESC, id DESC, observation_type DESC, service_name DESC,
        toStartOfHour(start_time) DESC
    LIMIT 1 BY project_id, trace_id
)
ORDER BY start_time DESC, trace_id DESC, project_id DESC
"""
ATTRIBUTE_SQL = f"""
SELECT project_id, trace_id, attribute_key, value_json
FROM (
    SELECT toString(project_id) AS project_id, trace_id, id, observation_type,
        service_name, start_time, attribute_key,
        coalesce(
            nullIf(JSONExtractRaw(attributes_extra, attribute_key), ''),
            if(mapContains(attrs_bool, attribute_key),
               if(attrs_bool[attribute_key]=0, 'false', 'true'), NULL),
            if(mapContains(attrs_number, attribute_key),
               if(isFinite(attrs_number[attribute_key]),
                  toString(attrs_number[attribute_key]), 'null'), NULL),
            if(mapContains(attrs_string, attribute_key),
               toJSONString(attrs_string[attribute_key]), NULL)
        ) AS value_json
    FROM spans FINAL
    ARRAY JOIN %(ref_keys)s AS attribute_key
    PREWHERE project_id=%(ref_project_id)s AND trace_id IN %(ref_ids)s
      AND {COORDINATE_PREDICATE}
    WHERE is_deleted=0
)
WHERE value_json IS NOT NULL
ORDER BY start_time DESC, id DESC, observation_type DESC, service_name DESC
LIMIT 1 BY project_id, trace_id, attribute_key
"""
TAG_SQL = """
SELECT toString(project_id) AS project_id, toString(id) AS trace_id, tags
FROM traces FINAL
PREWHERE project_id=%(ref_project_id)s AND id IN %(ref_ids)s
WHERE is_deleted=0
"""


def duplicate_version_sql():
    # Conservative: even identical duplicate maximum versions stay ambiguous.
    # Read only keys/version; no full payload raw aggregation. LIMIT 1 proves
    # existence, never truncates a successful absence proof (overflow throws).
    branches = []
    for table, identity, trace in (
        ('spans', 'project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id', 'trace_id'),
        ('traces', 'project_id, id', 'id'),
    ):
        coordinate_scope = ' AND ' + COORDINATE_PREDICATE if table == 'spans' else ''
        branches.append(f"""SELECT '{table}' AS duplicate_max_version_source FROM (
            SELECT {identity}, _version, count() AS copies FROM {table}
            PREWHERE project_id=%(ref_project_id)s AND {trace} IN %(ref_ids)s{coordinate_scope}
            GROUP BY {identity}, _version ORDER BY _version DESC LIMIT 1 BY {identity}
        ) WHERE copies>1 LIMIT 1""")
    return 'SELECT duplicate_max_version_source FROM (' + ' UNION ALL '.join(f'({branch})' for branch in branches) + ')'


def discover_coordinates(reader, params):
    """Complete raw six-key superset for independent IDs, never candidate keys.

    No FINAL/date/root/deletion/value/version filter: every replacement sibling
    survives pruning. An oversize/incomplete discovery is UNVERIFIED, not a cap
    on the page or its attributes. Concurrent inserts remain a disclosed risk.
    """
    rows = reader.execute_ch_query(COORDINATE_SQL, params, settings=dict(
        SETTINGS, max_rows_in_distinct=MAX_COORDINATES, distinct_overflow_mode='throw')).data
    if len(rows) > MAX_COORDINATES:
        raise replay.ReplayError('FULLROW_COORDINATE_LIMIT_EXCEEDED')
    coordinates = []
    for row in rows:
        project = str(row['project_id'])
        kind, service, trace, span = (row[k] for k in ('observation_type', 'service_name', 'trace_id', 'id'))
        hour = row['coordinate_hour']
        if isinstance(hour, str):
            hour = datetime.fromisoformat(hour)
        if not isinstance(hour, datetime):
            raise replay.ReplayError('FULLROW_COORDINATE_HOUR_INVALID')
        hour = hour.replace(tzinfo=timezone.utc) if hour.tzinfo is None else hour.astimezone(timezone.utc)
        if (project != params['ref_project_id'] or trace not in params['ref_ids']
                or not all(isinstance(v, str) for v in (kind, service, trace, span))
                or (hour.minute, hour.second, hour.microsecond) != (0, 0, 0)):
            raise replay.ReplayError('FULLROW_COORDINATE_SCOPE_OR_KEY_INVALID')
        coordinates.append((project, kind, service, hour, trace, span))
    if (len(set(coordinates)) != len(coordinates)
            or {key[4] for key in coordinates} != set(params['ref_ids'])):
        raise replay.ReplayError('FULLROW_COORDINATE_DISCOVERY_INCOMPLETE_OR_DUPLICATE')
    return tuple(coordinates)


def normalized(value):
    if isinstance(value, datetime):
        return ['datetime', (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat(timespec='microseconds')]
    if isinstance(value, UUID):
        value = str(value)
    if isinstance(value, Decimal):
        return ['decimal', str(value)]
    if isinstance(value, float):
        return ['float', value.hex() if math.isfinite(value) else repr(value)]
    if value is None or type(value) in (str, bool, int):
        return [type(value).__name__, value]
    if isinstance(value, dict):
        return ['object', [[k, normalized(v)] for k, v in sorted(value.items())]]
    if isinstance(value, (tuple, list)):
        return ['array', [normalized(v) for v in value]]
    raise TypeError('unsupported field type')


def field_token(field, value):
    if field in JSON_FIELDS and isinstance(value, str):
        value = json.loads(value)
    return replay.canonical(normalized(value))  # bool != numeric zero; NULL != missing.


def compare_fields(actual, expected):
    differences = []
    if len(actual) != len(expected):
        differences.append({'field': 'row_count'})
    for index, (left, right) in enumerate(zip(actual, expected)):
        for field in sorted(set(left) | set(right)):
            kind = ('missing' if field not in left else 'extra' if field not in right else
                    'value' if field_token(field, left[field]) != field_token(field, right[field]) else None)
            if kind:
                # No IDs, keys, values, SQL or raw exception text in public evidence.
                differences.append({'row': index, 'field': field if field in ROOT_FIELDS or field == 'replay_requested_attributes' else 'unsupported_extra_field', 'kind': kind})
    return differences


def verify_trace_page(reader, case, scope, payload, expected_ids, *, id_order_status):
    evidence = dict(contract=CONTRACT, status='UNVERIFIED', query_layer_fields_verified=False,
                    full_public_request=False, http_e2e=False, ui_e2e=False,
                    same_transaction_snapshot=False, unsupported=list(UNSUPPORTED))
    try:
        if (getattr(reader, 'mode', None) != 'reference_diagnostic'
                or case['surface'] not in {'traces', 'task_traces', 'eval_traces'}):
            raise replay.ReplayError('FULLROW_UNSUPPORTED_ROUTE')
        if id_order_status != 'ID_ORDER_MATCH':
            raise replay.ReplayError('FULLROW_REQUIRES_INDEPENDENT_ID_MATCH')
        # collect_selector_page omits ordering_exact; the independently checked
        # ID order above is authoritative. An explicit false/inexact flag fails.
        if (payload.get('query_complete') is not True or payload.get('query_exact') is not True
                or payload.get('ordering_exact') is False or payload.get('query_sampled')
                or payload.get('approximate_fields')):
            raise replay.ReplayError('FULLROW_REQUIRES_EXACT_COMPLETE_PAGE')
        coverage = payload.get('query_layer_coverage') or {}
        if not all(coverage.get(k) is True for k in ('selection', 'content', 'requested_attributes')):
            raise replay.ReplayError('FULLROW_HYDRATION_COVERAGE_MISSING')
        project, actual = str(scope['project_id']), payload['table']
        target_rows = case['request']['target_rows']
        if type(target_rows) is not int or target_rows < 1:
            raise replay.ReplayError('FULLROW_TARGET_ROWS_INVALID')
        if target_rows > MAX_PAGE_ROWS:
            return {**evidence, 'status': 'UNSUPPORTED', 'reason': 'FULLROW_PAGE_SIZE_OUTSIDE_REFERENCE_ENVELOPE',
                    'max_page_rows': MAX_PAGE_ROWS}
        if (len(expected_ids) > target_rows
                or len(set(expected_ids)) != len(expected_ids)
                or any(not isinstance(value, str) or not value for value in expected_ids)
                or [str(row.get('trace_id', '')) for row in actual] != list(expected_ids)
                or any(str(row.get('project_id', '')) != project for row in actual)):
            raise replay.ReplayError('FULLROW_PAGE_SCOPE_OR_ORDER_INVALID')
        raw_keys = (case['request'].get('params') or {}).get('attribute_keys', '[]')
        keys = json.loads(raw_keys) if isinstance(raw_keys, str) else raw_keys
        if not isinstance(keys, list) or len(keys) > MAX_ATTRIBUTE_KEYS or any(not isinstance(k, str) or not k or len(k.encode()) > 2048 for k in keys):
            raise replay.ReplayError('FULLROW_ATTRIBUTE_KEYS_INVALID')
        keys = list(dict.fromkeys(keys))
        if not expected_ids:
            return {**evidence, 'status': 'EMPTY_PAGE_MATCH', 'rows_compared': 0}
        from observe_trace_id_reference import unix_microseconds
        params = dict(ref_project_id=project, ref_ids=tuple(expected_ids), ref_keys=keys,
                      ref_start_us=unix_microseconds(replay.utc(case['window']['start'])),
                      ref_end_us=unix_microseconds(replay.utc(case['window']['end'])))
        coordinates = discover_coordinates(reader, params)
        # Project the COMPLETE independent raw six-key set, never candidate
        # keys. An immutable replacement-key prefix retains every version;
        # the independent trace-ID predicate still constrains all three reads.
        # Fewer literals, not fewer rows: FINAL and ambiguity grouping stay exact.
        params['ref_prefixes'] = tuple(dict.fromkeys(key[:4] for key in coordinates))
        evidence.update(coordinate_discovery_complete=True,
                        coordinates_discovered=len(coordinates),
                        coordinate_prefixes_discovered=len(params['ref_prefixes']))
        def read(sql):
            return reader.execute_ch_query(sql, params, settings=dict(SETTINGS)).data
        if read(duplicate_version_sql()):
            raise replay.ReplayError('FULLROW_AMBIGUOUS_DUPLICATE_MAX_VERSION')
        roots = read(ROOT_SQL)
        if ([str(row.get('trace_id', '')) for row in roots] != list(expected_ids)
                or any(str(row.get('project_id', '')) != project for row in roots)):
            raise replay.ReplayError('FULLROW_REFERENCE_ROOT_DRIFT')
        attributes, tags = read(ATTRIBUTE_SQL) if keys else [], read(TAG_SQL)
        projected, seen, tag_map = {}, set(), {}
        for row in attributes:
            coordinate = (str(row['project_id']), str(row['trace_id']), row['attribute_key'])
            if coordinate in seen or coordinate[0] != project or coordinate[1] not in expected_ids or coordinate[2] not in keys:
                raise replay.ReplayError('FULLROW_ATTRIBUTE_SCOPE_OR_DUPLICATE')
            seen.add(coordinate)
            projected.setdefault(coordinate[1], {})[coordinate[2]] = [json.loads(row['value_json'])]
        for row in tags:
            trace = str(row['trace_id'])
            if str(row['project_id']) != project or trace not in expected_ids or trace in tag_map:
                raise replay.ReplayError('FULLROW_TAG_SCOPE_OR_DUPLICATE')
            tag_map[trace] = row['tags'] or '[]'
        for row in roots:
            row['trace_tags'] = tag_map.get(row['trace_id'], '[]')
            if set(row) != set(ROOT_FIELDS):
                raise replay.ReplayError('FULLROW_REFERENCE_FIELD_CONTRACT')
            if keys:
                row['replay_requested_attributes'] = projected.get(row['trace_id'], {})
        differences = compare_fields(actual, roots)
        def fingerprint(rows):
            return replay.digest([{field: field_token(field, value) for field, value in row.items()} for row in rows])
        evidence.update(status='QUERY_LAYER_FIELDS_MISMATCH' if differences else 'QUERY_LAYER_FIELDS_MATCH',
                        query_layer_fields_verified=not differences, rows_compared=len(roots),
                        fields_compared=ROOT_FIELDS + (['replay_requested_attributes'] if keys else []),
                        differences=differences, possible_concurrent_drift=True,
                        reference_fields_sha256=fingerprint(roots), candidate_fields_sha256=fingerprint(actual),
                        duplicate_max_versions_observed=False)
    except Exception as exc:
        evidence.update(status='UNVERIFIED', query_layer_fields_verified=False,
                        exception_class=type(exc).__name__, error_code=getattr(exc, 'code', None))
        if isinstance(exc, replay.ReplayError):
            evidence['reason'] = str(exc)
    return evidence
