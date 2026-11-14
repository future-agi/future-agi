"""Offline contract/real replay-hook tests. No native engine or client."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

import observe_trace_fullrow_reference as ref

PROJECT = '11111111-1111-4111-8111-111111111111'


def coordinate(trace='independent', **changes):
    # Independent raw discovery may include only a child here; candidate root
    # identities must never supply or replace these mock query results.
    return dict(dict(project_id=PROJECT, trace_id=trace, id='raw-child',
                     observation_type='SPAN', service_name='discovered-service',
                     coordinate_hour=datetime(2026, 9, 5, 12, tzinfo=timezone.utc)), **changes)


def test_root_subquery_explicitly_projects_generated_columns():
    inner = ref.ROOT_SQL.split('FROM (', 1)[1].split('FROM spans FINAL', 1)[0]
    inner = '\n'.join(line for line in inner.splitlines() if not line.lstrip().startswith('--'))
    assert '*' not in inner
    columns = {column.strip() for column in inner.strip().removeprefix('SELECT ').split(',')}
    assert columns == set('project_id trace_id id start_time observation_type service_name '
                          '_version trace_name name status end_time latency_ms cost total_tokens '
                          'prompt_tokens completion_tokens model provider trace_session_id '
                          'input output attributes_extra attrs_string attrs_number attrs_bool metadata'.split())


class Reader:
    mode = 'reference_diagnostic'
    def __init__(self, replies):
        self.replies, self.calls, self.closed = deepcopy(replies), [], False
        self.deadline = float('inf')
    def execute_ch_query(self, sql, params, settings):
        self.calls.append((sql, deepcopy(params), deepcopy(settings)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(data=reply)
    def close(self):
        self.closed = True


@pytest.fixture
def sample():
    now = datetime(2026, 8, 31, 12, 30)
    root = dict.fromkeys(ref.ROOT_FIELDS, '')
    root.update(project_id=PROJECT, trace_id='independent', root_span_id='root',
                start_time=now, _root_start_hour=now.replace(minute=0), _root_version=2,
                end_time=None, trace_session_id=None, input=None, output='',
                cost=0.125, latency_ms=12, total_tokens=9, prompt_tokens=6, completion_tokens=3,
                attrs_bool={}, attrs_number={}, attrs_string={}, attributes_extra='{}',
                metadata='{"revision":"latest"}', trace_tags='["tag"]')
    actual = {**root, 'replay_requested_attributes': {'k': [False], 'null': [None]}}
    payload = dict(query_complete=True, query_exact=True, table=[actual],
                   query_layer_coverage=dict(selection=True, content=True, requested_attributes=True))
    case = dict(id='fixture', blocked=False, surface='traces', variant='fixture', attributes=['k'],
                period='7D', target_ms=5000,
                window={'start': '2026-08-31T00:00:00Z', 'end': '2026-09-01T00:00:00Z'},
                request={'target_rows': 25, 'params': {'attribute_keys': '["k","null","missing"]'}})
    roots = [{k: v for k, v in root.items() if k != 'trace_tags'}]
    attrs = [dict(project_id=PROJECT, trace_id='independent', attribute_key=k, value_json=v)
             for k, v in [('k', 'false'), ('null', 'null')]]
    tags = [dict(project_id=PROJECT, trace_id='independent', tags='["tag"]')]
    return case, payload, [[coordinate()], [], roots, attrs, tags]


def verify(sample, *, replies=None, ids=('independent',), status='ID_ORDER_MATCH'):
    case, payload, responses = sample
    reader = Reader(responses if replies is None else replies)
    evidence = ref.verify_trace_page(reader, case, {'project_id': PROJECT}, payload, ids,
                                     id_order_status=status)
    return evidence, reader


def test_complete_typed_fields_and_bound_reference_ids(sample):
    evidence, reader = verify(sample)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MATCH'
    assert evidence['reference_fields_sha256'] == evidence['candidate_fields_sha256']
    assert evidence['query_layer_fields_verified'] and not evidence['full_public_request']
    assert not evidence['http_e2e'] and not evidence['same_transaction_snapshot']
    assert 'PG_configuration' in evidence['unsupported']
    assert len(reader.calls) == 5
    assert evidence['coordinate_discovery_complete'] and evidence['coordinates_discovered'] == 1
    for sql, params, settings in reader.calls:
        assert params['ref_ids'] == ('independent',)
        assert params['ref_project_id'] == PROJECT
        assert not any('root_identity' in key or 'physical_key' in key for key in params)
        assert settings['read_overflow_mode'] == 'throw'
        assert sql.lstrip().startswith('SELECT')
    assert ref.ATTRIBUTE_SQL.index('ARRAY JOIN') < ref.ATTRIBUTE_SQL.index('PREWHERE')
    assert 'ref_start' not in ref.ATTRIBUTE_SQL and 'ref_end' not in ref.ATTRIBUTE_SQL
    assert 'argMax' not in ref.ROOT_SQL + ref.ATTRIBUTE_SQL + ref.TAG_SQL
    assert all('FINAL' in sql for sql in (ref.ROOT_SQL, ref.ATTRIBUTE_SQL, ref.TAG_SQL))


@pytest.mark.parametrize('field,value', [
    ('cost', 9.0), ('input', 'stale'), ('metadata', '{"revision":"old"}'),
    ('replay_requested_attributes', {'k': [0], 'null': [None]}),
    ('replay_requested_attributes', {'k': [False]}),
    ('replay_requested_attributes', {'k': [False], 'null': [None], 'missing': [None]}),
])
def test_same_ids_wrong_fields_detected(sample, field, value):
    sample[1]['table'][0][field] = value
    evidence, _ = verify(sample)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MISMATCH'
    assert evidence['differences'] == [{'row': 0, 'field': field, 'kind': 'value'}]
    assert evidence['reference_fields_sha256'] != evidence['candidate_fields_sha256']


def test_missing_metadata_is_not_ignored_and_extra_fields_do_not_leak(sample):
    del sample[1]['table'][0]['metadata']
    sample[1]['table'][0]['customer-secret-key'] = 'private-value'
    evidence, _ = verify(sample)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MISMATCH'
    assert {'row': 0, 'field': 'metadata', 'kind': 'missing'} in evidence['differences']
    assert 'customer-secret' not in json.dumps(evidence) and 'private-value' not in json.dumps(evidence)


def test_json_order_and_utc_normalize_but_types_do_not_collapse(sample):
    actual = sample[1]['table'][0]
    actual['metadata'] = ' { "revision" : "latest" } '
    actual['start_time'] = actual['start_time'].replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3)))
    assert verify(sample)[0]['status'] == 'QUERY_LAYER_FIELDS_MATCH'
    assert ref.field_token('x', False) != ref.field_token('x', 0)
    assert ref.field_token('x', {'decimal': '1'}) != ref.field_token('x', __import__('decimal').Decimal('1'))


@pytest.mark.parametrize('change', ['ids', 'project', 'inexact', 'keys', 'size', 'coverage', 'surface'])
def test_fail_closed_before_any_field_read(sample, change):
    case, payload, _ = sample
    if change == 'ids':
        payload['table'][0]['trace_id'] = 'candidate-controlled'
    if change == 'project':
        payload['table'][0]['project_id'] = 'foreign'
    if change == 'inexact':
        payload['ordering_exact'] = False
    if change == 'keys':
        case['request']['params']['attribute_keys'] = '[false]'
    if change == 'size':
        case['request']['target_rows'] = 0
    if change == 'coverage':
        payload['query_layer_coverage']['content'] = False
    if change == 'surface':
        case['surface'] = 'users_project'
    evidence, reader = verify(sample)
    assert evidence['status'] == 'UNVERIFIED' and not reader.calls


def test_id_mismatch_duplicate_version_and_reference_drift(sample):
    evidence, reader = verify(sample, status='ID_ORDER_MISMATCH')
    assert evidence['status'] == 'UNVERIFIED' and not reader.calls
    evidence, reader = verify(sample, replies=[[coordinate()], [{'duplicate_max_version_source': 'spans'}]])
    assert evidence['reason'] == 'FULLROW_AMBIGUOUS_DUPLICATE_MAX_VERSION' and len(reader.calls) == 2
    evidence, reader = verify(sample, replies=[[coordinate()], [], []])
    assert evidence['reason'] == 'FULLROW_REFERENCE_ROOT_DRIFT' and len(reader.calls) == 3


def test_empty_and_no_key_pages_are_explicit(sample):
    no_keys = deepcopy(sample)
    no_keys[0]['request']['params']['attribute_keys'] = '[]'
    del no_keys[1]['table'][0]['replay_requested_attributes']
    no_keys[2].pop(3)
    evidence, reader = verify(no_keys)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MATCH' and len(reader.calls) == 4
    sample[1]['table'] = []
    evidence, reader = verify(sample, ids=())
    assert evidence['status'] == 'EMPTY_PAGE_MATCH' and not evidence['query_layer_fields_verified']
    assert not reader.calls


@pytest.mark.parametrize('phase', [0, 1, 2, 3, 4])
def test_read_failure_cannot_be_a_partial_match_or_expose_values(sample, phase):
    sample[2][phase] = RuntimeError('sensitive SQL/private params')
    evidence, reader = verify(sample)
    assert evidence['status'] == 'UNVERIFIED' and not evidence['query_layer_fields_verified']
    assert len(reader.calls) == phase + 1 and 'sensitive' not in json.dumps(evidence)


@pytest.mark.parametrize('enabled,field_error', [(False, False), (True, False), (True, True)])
def test_actual_replay_hook_opt_in_after_candidate_close_preserves_id_evidence(sample, monkeypatch, enabled, field_error):
    import replay_observe_queries_readonly as queries
    import observe_trace_id_reference as ids
    case, payload, replies = sample
    if field_error:
        replies[0] = RuntimeError('private')
    candidate_reader, reference_reader = Reader([]), Reader(replies)
    clock = [1.0]
    monkeypatch.setattr(queries, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    execute = reference_reader.execute_ch_query
    def field_read(*args, **kwargs):
        assert candidate_reader.closed
        clock[0] += 10.0
        return execute(*args, **kwargs)
    reference_reader.execute_ch_query = field_read
    created = iter((candidate_reader, reference_reader))
    monkeypatch.setattr(queries, 'ReadOnlyExecutor', lambda *a, **k: next(created))
    monkeypatch.setattr(queries, 'normalize_filters', lambda request: [])
    def independent(*args):
        assert candidate_reader.closed
        return ['independent'], {'population_exhausted': True}
    monkeypatch.setattr(ids, 'reference_ids', independent)
    adapter = object.__new__(queries.CandidateQueries)
    adapter.args = SimpleNamespace(verify_trace_ids=True, verify_trace_full_rows=enabled, safety_seconds=60)
    adapter.projects, adapter.plan, adapter.metadata = [PROJECT], {'scope': {'project_id': PROJECT}}, None
    def candidate(*args):
        clock[0] += 2.0
        return payload
    adapter.entity_list = candidate
    result = adapter.run(case)
    evidence = result['independent_reference']
    assert evidence['status'] == 'ID_ORDER_MATCH' and candidate_reader.closed and reference_reader.closed
    assert result['queries'] == [] and result['elapsed_ms'] == 2000
    if enabled:
        assert evidence['query_layer_fields']['status'] == ('UNVERIFIED' if field_error else 'QUERY_LAYER_FIELDS_MATCH')
        assert evidence['elapsed_ms'] == (10000 if field_error else 50000)
    else:
        assert 'query_layer_fields' not in evidence and reference_reader.calls == []


def test_existing_readonly_sql_validator_accepts_exact_reference_scope(sample):
    import replay_observe_queries_readonly as queries
    _, reader = verify(sample)
    for sql, params, _ in reader.calls:
        assert queries.validate_select(sql, params, [PROJECT])


def test_opt_in_requires_id_oracle_before_any_plan_file_or_client(monkeypatch):
    import replay_observe_queries_readonly as queries
    monkeypatch.setattr(queries.sys, 'argv', ['replay', '--production-read-only',
        '--port', '19004', '--database', 'default', '--expected-server', 'not-used',
        '--plan', '/not-read', '--authorized-projects', '/not-read', '--ledger', '/not-created',
        '--verify-trace-full-rows'])
    monkeypatch.setattr(queries.replay, 'read_json', lambda *args: pytest.fail('No file read authorized in this test'))
    with pytest.raises(SystemExit) as caught:
        queries.main()
    assert caught.value.code == 2


@pytest.mark.parametrize('surface,count,key_count', [
    ('traces', 26, 3), ('eval_traces', 50, 10), ('traces', 100, 100),
])
def test_real_full_page_contract_not_a_one_row_proxy(sample, surface, count, key_count):
    import replay_observe_queries_readonly as queries
    import replay_observe_filters as replay
    case, payload, replies = sample
    case['surface'] = surface
    keys = [f'key-{index}' for index in range(key_count)]
    case['request'].update(method='GET', path=replay.LISTS[surface], target_rows=count)
    case['request']['params'].update(page_size=count, cursor_mode=True, attribute_keys=json.dumps(keys))
    queries.validate_preview_workload(case)  # Includes the real 50-row Eval contract.
    ids = [f'independent-{index:03}' for index in range(count)]
    root = replies[2][0]
    replies[0] = [coordinate(trace) for trace in ids]
    replies[2] = [{**deepcopy(root), 'trace_id': trace} for trace in ids]
    replies[3] = [dict(project_id=PROJECT, trace_id=trace, attribute_key=key, value_json='false')
                  for trace in ids for key in keys]
    replies[4] = [dict(project_id=PROJECT, trace_id=trace, tags='["tag"]') for trace in ids]
    payload['table'] = [{**deepcopy(row), 'trace_tags': '["tag"]',
                         'replay_requested_attributes': {key: [False] for key in keys}}
                        for row in replies[2]]
    evidence, reader = verify(sample, ids=ids)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MATCH' and evidence['rows_compared'] == count
    assert len(reader.calls) == 5 and len(reader.calls[3][1]['ref_ids']) == count
    assert all(settings['max_result_rows'] >= count * key_count for _, _, settings in reader.calls)
    # The final row/key participates; comparing only a 25-row prefix must fail.
    payload['table'][-1]['replay_requested_attributes'][keys[-1]] = [True]
    assert verify(sample, ids=ids)[0]['differences'] == [
        {'row': count - 1, 'field': 'replay_requested_attributes', 'kind': 'value'}]


def test_valid_but_larger_page_reports_unsupported_envelope_not_invalid_scope(sample):
    sample[0]['request']['target_rows'] = 101
    evidence, reader = verify(sample)
    assert evidence['status'] == 'UNSUPPORTED'
    assert evidence['reason'] == 'FULLROW_PAGE_SIZE_OUTSIDE_REFERENCE_ENVELOPE'
    assert evidence['max_page_rows'] == 100 and not reader.calls


def test_coordinate_pruning_uses_complete_raw_six_keys_only(sample):
    evidence, reader = verify(sample)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MATCH'
    sql, params, settings = reader.calls[0]
    assert sql == ref.COORDINATE_SQL and 'ref_coordinates' not in params
    assert 'SELECT DISTINCT project_id, observation_type, service_name,' in sql
    assert 'toStartOfHour(start_time) AS coordinate_hour, trace_id, id' in sql
    for forbidden in ('FINAL', 'is_deleted', 'parent_span_id', 'ref_start', 'ref_end',
                      '_version', 'attrs_', 'attributes_extra', 'input', 'output'):
        assert forbidden not in sql
    assert 'LIMIT 10001' in sql
    assert settings['max_rows_in_distinct'] == settings['max_result_rows'] == 10000
    assert settings['distinct_overflow_mode'] == settings['result_overflow_mode'] == 'throw'
    assert settings['max_bytes_to_read'] == 2 * 1024**3
    assert settings['max_memory_usage'] == 512 * 1024**2
    physical = '(project_id, observation_type, service_name, toStartOfHour(start_time))'
    expected_key = (PROJECT, 'SPAN', 'discovered-service',
                    datetime(2026, 9, 5, 12, tzinfo=timezone.utc), 'independent', 'raw-child')
    for sql, params, settings in reader.calls[1:4]:
        assert physical + ' IN %(ref_prefixes)s' in sql
        assert params['ref_prefixes'] == (expected_key[:4],)
        assert 'ref_coordinates' not in params
        assert 'trace_id IN %(ref_ids)s' in sql
        assert settings == ref.SETTINGS
    assert 'ref_coordinates' not in ref.TAG_SQL  # traces has its own project/id key.


@pytest.mark.parametrize('change', ['foreign', 'other_trace', 'missing_trace', 'duplicate', 'hour', 'kind', 'oversize', 'overflow'])
def test_bad_discovery_never_reaches_any_final_read(sample, change):
    rows = sample[2][0]
    if change == 'foreign':
        rows[0]['project_id'] = '22222222-2222-4222-8222-222222222222'
    if change == 'other_trace':
        rows[0]['trace_id'] = 'not-authorized'
    if change == 'missing_trace':
        rows.clear()
    if change == 'duplicate':
        rows.append(deepcopy(rows[0]))
    if change == 'hour':
        rows[0]['coordinate_hour'] += timedelta(microseconds=1)
    if change == 'kind':
        rows[0]['observation_type'] = None
    if change == 'oversize':
        sample[2][0] = [coordinate(id=str(i)) for i in range(10001)]
    if change == 'overflow':
        sample[2][0] = RuntimeError('private overflow details')
    evidence, reader = verify(sample)
    assert evidence['status'] == 'UNVERIFIED' and not evidence['query_layer_fields_verified']
    assert len(reader.calls) == 1 and 'private overflow' not in json.dumps(evidence)


def test_coordinate_boundary_allows_exactly_10000_and_normalizes_utc(sample):
    sample[2][0] = [coordinate(id=str(i), coordinate_hour='2026-09-05 12:00:00') for i in range(10000)]
    evidence, reader = verify(sample)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MATCH'
    assert evidence['coordinates_discovered'] == 10000
    assert reader.calls[1][1]['ref_prefixes'][0][3].tzinfo == timezone.utc
    assert evidence['coordinate_prefixes_discovered'] == 1


def test_all_six_coordinate_components_are_distinct_and_not_candidate_roots(sample):
    sample[2][0] = [coordinate(), coordinate(observation_type='EVENT'),
                    coordinate(service_name='sibling'), coordinate(id='another-child'),
                    coordinate(coordinate_hour=datetime(2026, 9, 5, 13))]
    sample[1]['table'][0]['root_span_id'] = 'wrong-candidate-root'
    evidence, reader = verify(sample)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MISMATCH'
    assert evidence['coordinates_discovered'] == 5
    assert evidence['differences'] == [{'row': 0, 'field': 'root_span_id', 'kind': 'value'}]
    assert evidence['coordinate_prefixes_discovered'] == 4
    assert all(len(key) == 4 and 'wrong-candidate-root' not in key
               for key in reader.calls[1][1]['ref_prefixes'])


def test_3878_complete_coordinates_collapse_without_parser_or_data_guard_changes(sample):
    # Source-independent fixed original guard contract, not a higher native limit.
    assert not {'max_query_size', 'max_ast_elements', 'max_expanded_ast_elements'} & ref.SETTINGS.keys()
    assert ref.SETTINGS['max_bytes_to_read'] == 2 * 1024**3
    assert ref.SETTINGS['max_memory_usage'] == 512 * 1024**2
    assert ref.SETTINGS['max_execution_time'] == 15
    sample[2][0] = [coordinate(id=f'physical-{index:032x}') for index in range(3878)]
    evidence, reader = verify(sample)
    assert evidence['status'] == 'QUERY_LAYER_FIELDS_MATCH'
    assert evidence['coordinates_discovered'] == 3878
    assert evidence['coordinate_prefixes_discovered'] == 1
    for sql, params, settings in reader.calls[1:4]:
        assert params['ref_ids'] == ('independent',)
        assert len(params['ref_prefixes']) == 1 and 'ref_coordinates' not in params
        assert settings == ref.SETTINGS
    # Ambiguity still groups on ALL six components and version, not the prefix.
    assert 'GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id, _version' in ref.duplicate_version_sql()


def test_uncollapsible_prefix_query_failure_is_unverified_not_partial_success(sample):
    sample[2][0] = [coordinate(service_name=f'service-{index}') for index in range(3878)]
    sample[2][1] = RuntimeError('Max query size exceeded: private query details')
    evidence, reader = verify(sample)
    assert evidence['status'] == 'UNVERIFIED' and not evidence['query_layer_fields_verified']
    assert evidence['coordinates_discovered'] == evidence['coordinate_prefixes_discovered'] == 3878
    assert len(reader.calls) == 2
    assert 'private query' not in json.dumps(evidence)
