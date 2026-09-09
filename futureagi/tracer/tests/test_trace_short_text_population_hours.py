"""Short text empty-gap discovery retains complete latest physical history."""
import re
import struct
from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2 as Actual,
)
from tracer.tests import test_trace_boolean_candidate_seed as b
from tracer.tests import test_trace_numeric_primary_seed as n
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_trace_primary_prefix import trace_engine as trace_engine


def ORIGINAL(self):
    # The pre-optimization company_id route did not admit root-gap discovery.
    return False


candidate = Actual.supports_filter_empty_seed_root_time_discovery

SORTING_KEY = 'project_id,observation_type,service_name,toStartOfHour(start_time),trace_id,id'

@pytest.fixture(autouse=True, scope='session')
def _drop_legacy_ch_spans_mvs():
    yield

@pytest.fixture(autouse=True, scope='session')
def _ensure_test_score_tenant_column():
    yield

def subject():
    return n.subject(leaves=[n.number_filter('in',['a','b','c'],key='company_id',
        filter_type='text',attribute_value_types=['string']*3)],page_size=25)

def typed(value):
    if type(value) is float:
        return ('float',struct.pack('!d',value).hex())
    if type(value) is dict:
        return ('dict',tuple((k,typed(v)) for k,v in sorted(value.items())))
    if type(value) in (list,tuple):
        return (type(value).__name__,tuple(map(typed,value)))
    return (type(value).__name__,value)

def fixture_times(hours):
    at=n.END-timedelta(hours=hours,minutes=20,microseconds=123456)
    same_hour=at-timedelta(minutes=10)
    different_hour=at-timedelta(hours=1)
    def hour(t):
        return t.replace(minute=0,second=0,microsecond=0)
    assert hour(at)==hour(same_hour)
    assert hour(at)!=hour(different_hour)
    return at,same_hour,different_hour

def physical_key(row):
    return (row['project_id'],row['observation_type'],row['service_name'],
        row['start_time'].replace(minute=0,second=0,microsecond=0),row['trace_id'],row['id'])

def unique_latest(rows):
    winners={}
    for row in rows:
        key=physical_key(row)
        prev=winners.get(key)
        assert prev is None or int(prev['_version'])!=int(row['_version']), 'fixture must not pick an arbitrary tie'
        if prev is None or int(prev['_version'])<int(row['_version']):
            winners[key]=row
    return list(winners.values())

@pytest.mark.parametrize('hours',[2,49])
def test_same_hour_replacements_different_hour_controls_and_old_child(trace_engine,monkeypatch,hours):  # noqa: F811 - pytest injects the re-exported trace_engine fixture.
    run,_=trace_engine
    metadata,=run("SELECT sorting_key,engine_full FROM system.tables WHERE database=currentDatabase() AND name='spans'")
    def compact(value):
        return re.sub(r'[\s`]', '',value)
    sorting_key=compact(metadata['sorting_key'])
    if sorting_key.startswith('tuple(') and sorting_key.endswith(')'):
        sorting_key=sorting_key[6:-1]
    elif sorting_key.startswith('(') and sorting_key.endswith(')'):
        sorting_key=sorting_key[1:-1]
    assert sorting_key==SORTING_KEY
    assert compact(metadata['engine_full']).split('PARTITIONBY', 1)[0]=='ReplacingMergeTree(_version,is_deleted)'
    at,same_hour,different_hour=fixture_times(hours)
    run("""INSERT INTO spans
        (project_id,observation_type,service_name,start_time,trace_id,id,parent_span_id,attrs_string,_version)
        SELECT toUUID(%(project)s),'span','service-a',fromUnixTimestamp64Micro(%(at)s),
        concat('w-',leftPad(toString(number+1),5,'0')),'root','',map('company_id','a'),1
        FROM numbers(225)""",{'project':n.PROJECT,'at':b.us(at)})
    # SAME immutable key: newer value correction, clear, and tombstone.
    run("""INSERT INTO spans
        (project_id,observation_type,service_name,start_time,trace_id,id,parent_span_id,attrs_string,is_deleted,_version)
        SELECT project_id,observation_type,service_name,fromUnixTimestamp64Micro(%(at)s),
        trace_id,id,parent_span_id,
        if(trace_id<='w-00190',map('company_id','not-matching'),map()),
        toUInt8(trace_id>'w-00200'),2
        FROM spans WHERE trace_id>'w-00175'""",{'at':b.us(same_hour)})
    # DIFFERENT immutable hour key: these newer rows cannot replace the originals.
    for trace,deleted in [('y-hour-clear',0),('x-hour-delete',1)]:
        run("""INSERT INTO spans
          (project_id,observation_type,service_name,start_time,trace_id,id,parent_span_id,attrs_string,is_deleted,_version)
          VALUES (toUUID(%(project)s),'span','service-a',fromUnixTimestamp64Micro(%(at)s),%(trace)s,'root','',map('company_id','a'),0,1),
          (toUUID(%(project)s),'span','service-a',fromUnixTimestamp64Micro(%(other)s),%(trace)s,'root','',map(),%(deleted)s,2)
          """,{'project':n.PROJECT,'at':b.us(at),'other':b.us(different_hour),'trace':trace,'deleted':deleted})
    run("""INSERT INTO spans
        (project_id,observation_type,service_name,start_time,trace_id,id,parent_span_id,attrs_string,_version)
        VALUES (toUUID(%(project)s),'span','service-a',fromUnixTimestamp64Micro(%(at)s),'z-old-child','root','',map(),1),
        (toUUID(%(project)s),'span','service-b',fromUnixTimestamp64Micro(%(child)s),'z-old-child','child','root',map('company_id','a'),1)
        """,{'project':n.PROJECT,'at':b.us(at),'child':b.us(n.END-timedelta(days=8))})
    # Entire raw fixture first, independently reduce the real six-key identity.
    raw=run('SELECT project_id,observation_type,service_name,start_time,trace_id,id,parent_span_id,attrs_string,is_deleted,_version FROM spans')
    assert len(raw)==281
    winners=unique_latest(raw)
    assert len(winners)==231
    by_trace={name:[r for r in winners if r['trace_id']==name] for name in {r['trace_id'] for r in winners}}
    for number in range(176,226):
        row,=by_trace[f'w-{number:05}']
        assert int(row['_version'])==2 and row['start_time']==b.plain(same_hour)
        assert row['attrs_string']==({'company_id':'not-matching'} if number<=190 else {})
        assert bool(row['is_deleted'])==(number>200)
    for name in ('y-hour-clear','x-hour-delete'):
        assert len(by_trace[name])==2
        live_original,=[r for r in by_trace[name] if int(r['_version'])==1]
        assert not live_original['is_deleted'] and live_original['attrs_string']=={'company_id':'a'}
    live=[r for r in winners if not r['is_deleted']]
    matches={r['trace_id'] for r in live if r['attrs_string'].get('company_id') in {'a','b','c'}}
    roots=[r for r in live if not r['parent_span_id'] and b.plain(n.END-timedelta(days=7))<=r['start_time']<b.plain(n.END) and r['trace_id'] in matches]
    canonical={}
    for row in roots:
        key=(row['start_time'],row['id'],row['observation_type'],row['service_name'],physical_key(row)[3])
        if row['trace_id'] not in canonical or key>canonical[row['trace_id']][0]:
            canonical[row['trace_id']]=(key,row)
    expected=[r['trace_id'] for _,r in sorted(canonical.values(),key=lambda pair:(pair[1]['start_time'],pair[1]['trace_id']),reverse=True)[:25]]
    assert expected==['z-old-child','y-hour-clear','x-hour-delete',*b.names(175,154)]
    assert len(canonical)==178
    baseline_transport=b.Transport(run)
    with monkeypatch.context() as m:
        m.setattr(Actual,'supports_filter_empty_seed_root_time_discovery',ORIGINAL)
        baseline=n.page(subject(),baseline_transport)
    transport=b.Transport(run)
    with monkeypatch.context() as m:
        m.setattr(Actual,'supports_filter_empty_seed_root_time_discovery',candidate)
        actual=n.page(subject(),transport)
    assert baseline.complete and actual.complete and baseline.has_more and actual.has_more
    assert b.ids(baseline)==expected and b.ids(actual)==expected
    assert typed(actual.rows)==typed(baseline.rows)
    assert not b.probes(baseline_transport) and b.probes(transport)
    hour=at.replace(minute=0,second=0,microsecond=0)
    replay=b.seeds(transport)[1][1]
    assert replay['filter_slice_start']==b.plain(hour)
    assert replay['filter_slice_end']==b.plain(hour+timedelta(hours=1))
    assert not b.globals_used(transport)
