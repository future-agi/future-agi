"""Session numeric witnesses preserve exact latest state and time boundaries."""
import re
import struct
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine

CANDIDATE = graph._session_membership_plan


def ORIGINAL(*args, **kwargs):
    """Reference route: retain full membership without optional witness pruning."""
    return replace(CANDIDATE(*args, **kwargs), scalar_witness_predicate=None)


@pytest.fixture(autouse=True,scope='session')
def _drop_legacy_ch_spans_mvs():
    yield

@pytest.fixture(autouse=True,scope='session')
def _ensure_test_score_tenant_column():
    yield

A,B,C,D,NEW=(str(UUID(int=i)) for i in (100,200,300,400,900))
LO=START+timedelta(minutes=15,microseconds=123456)
HI=START+timedelta(days=7,minutes=30,microseconds=654321)

def typed(value):
    if type(value) is float:
        return ('float',struct.pack('!d',value).hex())
    if type(value) is dict:
        return ('dict',tuple((k,typed(v)) for k,v in sorted(value.items())))
    if type(value) in (list,tuple):
        return (type(value).__name__,tuple(map(typed,value)))
    return (type(value).__name__,value)


def _assert_public_reader_absence_gate(
    run, monkeypatch, record_property, filters, *, raw_present, expected_value,
):
    """Compare the current public reader with only its absence probe disabled."""
    outputs, captures = [], []
    for probe_enabled in (False, True):
        calls = []

        def execute(sql, params, *, _calls=calls, **options):
            rows = run(sql, params)
            # The fixture's JSON transport encodes the native datetime response.
            for row in rows:
                if isinstance(row.get('time_bucket'), str):
                    row['time_bucket'] = datetime.fromisoformat(row['time_bucket'])
            _calls.append((sql, params, options, rows))
            return SimpleNamespace(data=rows, columns=list(rows[0]) if rows else [])

        with monkeypatch.context() as control:
            if not probe_enabled:
                control.setattr(graph, '_session_numeric_absence_probe_sql', lambda **_: None)
            outputs.append(graph.read_exact_session_system_graph(
                analytics=SimpleNamespace(execute_ch_query=execute), project_id=PROJECT,
                filters=filters, interval='day', metric_id='latency',
            ))
        captures.append(calls)

    baseline, candidate = captures
    def is_probe(call):
        return call[0].lstrip().startswith('SELECT 1 AS has_raw_witness')
    assert len(baseline) == 1 and not is_probe(baseline[0])
    assert sum(is_probe(call) for call in candidate) == 1
    assert sum(not is_probe(call) for call in candidate) == int(raw_present)
    assert is_probe(candidate[0])
    probe_sql, probe_params, _, probe_rows = candidate[0]
    assert re.findall(r'\bFROM\s+([A-Za-z0-9_.]+)', probe_sql, re.IGNORECASE) == ['spans']
    assert not re.search(r'\bFINAL\b', probe_sql, re.IGNORECASE)
    assert bool(probe_rows) is raw_present
    def as_us(value):
        return (value - datetime(1970, 1, 1)) // timedelta(microseconds=1)
    assert probe_params['project_id'] == PROJECT
    assert probe_params['session_absence_start_us'] == as_us(LO.replace(minute=0, second=0, microsecond=0))
    assert probe_params['session_absence_end_us'] == as_us(HI.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    assert baseline[0][1]['start_date_us'] == as_us(LO)
    assert baseline[0][1]['end_date_us'] == as_us(HI)
    if raw_present:
        assert candidate[1][0] == baseline[0][0]
        assert typed(candidate[1][1]) == typed(baseline[0][1])
        assert candidate[1][2]['settings'] == baseline[0][2]['settings']
        assert typed(candidate[1][3]) == typed(baseline[0][3])
    else:
        assert baseline[0][3] == []

    first_day = LO.replace(hour=0, minute=0, second=0, microsecond=0)
    expected_points = [
        {'timestamp': (first_day + timedelta(days=day)).isoformat(),
         'value': float(expected_value) if day == 0 and expected_value is not None else 0.0,
         'primary_traffic': int(day == 0 and expected_value is not None)}
        for day in range(8)
    ]
    for result, calls in zip(outputs, captures, strict=True):
        assert typed(result['data']) == typed(expected_points)
        assert result['metric_name'] == 'latency'
        assert result['query_count'] == len(calls)
        assert result['query_rows_returned'] == int(expected_value is not None)
        assert result['query_complete'] is True and result['query_sampled'] is False
        assert result['query_status'] == 'complete'
    def comparable(result):
        return {k: v for k, v in result.items() if k not in {'query_count', 'query_elapsed_ms'}}
    assert typed(comparable(outputs[0])) == typed(comparable(outputs[1]))
    record_property('absence_gate_branch', 'raw-positive:probe1,full1' if raw_present else 'raw-absent:probe1,full0')
    record_property('baseline_full_queries', 1)

@pytest.mark.parametrize('scenario',['live','clear','deleted','session-null','session-move','before','after','other-hour','tied','wrong-type'])
def test_numeric_range_keeps_latest_alias_membership_and_all_roots(engine,monkeypatch,scenario,record_property):  # noqa: F811 - pytest injects the re-exported engine fixture.
    run,insert=engine
    run('ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)')
    run('CREATE TABLE trace_session_id_remap (old_id UUID,new_id UUID,version UInt64) ENGINE=ReplacingMergeTree(version) ORDER BY old_id')
    run('SET max_threads=1,max_block_size=512,max_memory_usage=536870912,use_skip_indexes_if_final=0,optimize_aggregation_in_order=1')
    run('INSERT INTO trace_session_id_remap VALUES (%(a)s,%(new)s,1),(%(b)s,%(new)s,1),(%(c)s,%(new)s,1)',{'a':A,'b':B,'c':C,'new':NEW})
    # Canonical A has no roots: B/C metrics and NEW's child witness must merge.
    for name,session,latency,tokens,cost in [('root-b',B,10,5,0.375),('root-c',C,30,7,0.625),('root-d',D,50,23,2.0)]:
        insert(id=name,trace_id=name,parent_span_id='',trace_session_id=session,
            start_time=LO+timedelta(minutes=5),end_time=LO+timedelta(minutes=6),
            attrs_number={},latency_ms=latency,total_tokens=tokens,cost=cost,status='OK')
    at=HI-timedelta(minutes=5) if scenario=='after' else LO+timedelta(minutes=10)
    child={'id': 'child','trace_id': 'child-without-own-root','parent_span_id': 'root-b',
        'trace_session_id': NEW,'start_time': at,'attrs_number': {'agent.duration_s':2}}
    insert(**child)
    changes={
        'clear':{'attrs_number':{}},'deleted':{'is_deleted':1},
        'session-null':{'trace_session_id':None},'session-move':{'trace_session_id':D},
        'before':{'start_time':LO-timedelta(microseconds=1)},
        'after':{'start_time':HI+timedelta(microseconds=1)},
        'other-hour':{'start_time':at+timedelta(hours=1),'is_deleted':1},
        'wrong-type':{'attrs_number':{},'attrs_string':{'agent.duration_s':'2'}},
    }.get(scenario)
    if changes:
        updated={**child,**changes,'_version':2}
        def hour(t):
            return t.replace(minute=0,second=0,microsecond=0)
        assert (hour(at)==hour(updated['start_time']))==(scenario!='other-hour')
        insert(**updated)
    if scenario=='tied':
        # Both retained choices satisfy >1; do not invent an arbitrary winner.
        insert(**{**child,'attrs_number':{'agent.duration_s':3}})
    insert(**{**child,'project_id':OTHER_PROJECT,'_version':99,'is_deleted':1})
    # Independent whole physical FINAL first, then mutable predicates in Python.
    final=run('SELECT * FROM spans FINAL')
    live=[r for r in final if r['project_id']==PROJECT and not r['is_deleted'] and LO<=r['start_time']<HI]
    remaps=run('SELECT * FROM trace_session_id_remap FINAL')
    assert {r['old_id'] for r in remaps}=={A,B,C} and {r['new_id'] for r in remaps}=={NEW}
    aliases=dict.fromkeys((A, B, C, NEW), A)
    def canonical(r):
        return aliases.get(r['trace_session_id'],r['trace_session_id'])
    matched={canonical(r) for r in live if r['trace_session_id'] not in (None,str(UUID(int=0))) and 'agent.duration_s' in r['attrs_number'] and r['attrs_number']['agent.duration_s']>1}
    roots=[r for r in live if not r['parent_span_id'] and canonical(r) in matched]
    expected={}
    for session in {canonical(r) for r in roots}:
        rows=[r for r in roots if canonical(r)==session]
        expected[session]=(sum(int(r['latency_ms']) for r in rows)/len(rows),sum(int(r['total_tokens']) for r in rows),sum(r['cost'] for r in rows),len({r['trace_id'] for r in rows}))
    gold=({D:(50.0,23,2.0,1)} if scenario=='session-move' else {A:(20.0,12,1.0,2)} if scenario in ('live','other-hour','tied') else {})
    assert expected==gold
    filters=[time_filter(LO,HI),{'column_id':'agent.duration_s','filter_config':{'col_type':'SPAN_ATTRIBUTE','filter_type':'number','filter_op':'greater_than','filter_value':1}}]
    outputs=[]
    for build in (ORIGINAL,CANDIDATE):
        with monkeypatch.context() as m:
            m.setattr(graph,'_session_membership_plan',build)
            sql,params=graph._session_aggregate_source_sql(project_id=PROJECT,filters=filters,start_date=LO,end_date=HI,include_trace_ids=False,anchor_by_session_start=True,use_scalar_witness=True)
        assert ('session_scalar_witness_ids AS (' in sql)==(build is CANDIDATE)
        rows=run(sql,{**params,'start_date':LO,'end_date':HI})
        actual={r['session_id']:(r['session_avg_latency'],int(r['session_total_tokens']),r['session_total_cost'],int(r['session_traces'])) for r in rows}
        assert actual==expected
        outputs.append(rows)
    assert typed(outputs[0])==typed(outputs[1])
    _assert_public_reader_absence_gate(
        run, monkeypatch, record_property, filters, raw_present=True,
        expected_value=next(iter(gold.values()))[0] if gold else None,
    )


@pytest.mark.parametrize('boundary,delta,expected_match',[
    ('lo',0,True),('lo',1,True),('hi',-1,True),('hi',0,False)])
def test_microsecond_latest_winner_half_open_controls(engine,monkeypatch,boundary,delta,expected_match,record_property):  # noqa: F811 - pytest injects the re-exported engine fixture.
    run,insert=engine
    lo,hi=LO,HI
    run('ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)')
    run('CREATE TABLE trace_session_id_remap (old_id UUID,new_id UUID,version UInt64) ENGINE=ReplacingMergeTree(version) ORDER BY old_id')
    run('SET max_threads=1,max_block_size=512,max_memory_usage=536870912,use_skip_indexes_if_final=0,optimize_aggregation_in_order=1')
    run('INSERT INTO trace_session_id_remap VALUES (%(a)s,%(new)s,1),(%(b)s,%(new)s,1)',{'a':A,'b':B,'new':NEW})
    for trace,session,latency in [('a',A,10),('b',B,30)]:
        insert(id=trace,trace_id=trace,parent_span_id='',trace_session_id=session,
            attrs_number={},latency_ms=latency,start_time=lo+timedelta(minutes=5),end_time=lo+timedelta(minutes=6))
    target=(lo if boundary=='lo' else hi)+timedelta(microseconds=delta)
    at=lo+timedelta(minutes=10) if boundary=='lo' else hi-timedelta(minutes=5)
    assert at.replace(minute=0,second=0,microsecond=0)==target.replace(minute=0,second=0,microsecond=0)
    child={'id': 'child','trace_id': 'child-without-own-root','parent_span_id': 'parent','trace_session_id': NEW,
        'attrs_number': {'agent.duration_s':2},'start_time': at}
    insert(**child)
    insert(**{**child,'start_time':target,'_version':2})
    final=run('SELECT * FROM spans FINAL')
    latest,=[r for r in final if r['id']=='child']
    assert int(latest['_version'])==2 and latest['start_time']==target
    assert (lo<=latest['start_time']<hi)==expected_match
    filters=[time_filter(lo,hi),{'column_id':'agent.duration_s','filter_config':{'col_type':'SPAN_ATTRIBUTE','filter_type':'number','filter_op':'greater_than','filter_value':1}}]
    outputs=[]
    for plan in (ORIGINAL,CANDIDATE):
        with monkeypatch.context() as m:
            m.setattr(graph,'_session_membership_plan',plan)
            sql,params=graph._session_aggregate_source_sql(project_id=PROJECT,filters=filters,start_date=lo,end_date=hi,include_trace_ids=False,anchor_by_session_start=True,use_scalar_witness=True)
        rows=run(sql,params)
        assert len(rows)==int(expected_match)
        if expected_match:
            assert rows[0]['session_id']==A
            assert rows[0]['session_avg_latency']==20 and int(rows[0]['session_traces'])==2
        outputs.append(rows)
    assert typed(outputs[0])==typed(outputs[1])
    _assert_public_reader_absence_gate(
        run, monkeypatch, record_property, filters, raw_present=True,
        expected_value=20 if expected_match else None,
    )


def test_public_reader_raw_absence_skips_full_query_on_nonempty_table(engine, monkeypatch, record_property):  # noqa: F811 - pytest injects the re-exported engine fixture.
    run, insert = engine
    run('ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)')
    run('CREATE TABLE trace_session_id_remap (old_id UUID,new_id UUID,version UInt64) ENGINE=ReplacingMergeTree(version) ORDER BY old_id')
    run('SET max_threads=1,max_block_size=512,max_memory_usage=536870912,use_skip_indexes_if_final=0,optimize_aggregation_in_order=1')
    insert(id='root', trace_id='root', parent_span_id='', trace_session_id=A,
        start_time=LO + timedelta(minutes=5), attrs_number={'agent.duration_s': 0}, latency_ms=20)
    insert(id='string-only', attrs_number={}, attrs_string={'agent.duration_s': '2'}, trace_session_id=A)
    insert(id='foreign-positive', project_id=OTHER_PROJECT, attrs_number={'agent.duration_s': 2})
    insert(id='before-hour-positive', start_time=LO.replace(minute=0, second=0, microsecond=0) - timedelta(microseconds=1), attrs_number={'agent.duration_s': 2})
    insert(id='after-hour-positive', start_time=HI.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), attrs_number={'agent.duration_s': 2})
    assert run('SELECT count() AS n FROM spans')[0]['n'] == 5
    filters = [time_filter(LO, HI), {'column_id': 'agent.duration_s', 'filter_config': {
        'col_type': 'SPAN_ATTRIBUTE', 'filter_type': 'number', 'filter_op': 'greater_than', 'filter_value': 1}}]
    _assert_public_reader_absence_gate(
        run, monkeypatch, record_property, filters, raw_present=False, expected_value=None,
    )
