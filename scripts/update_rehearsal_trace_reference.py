"""Source-only PostgreSQL Trace/ObservationSpan qualification fixture.

v1.56.1 and v1.58.0 have the same four model inventories checked below (including
0080/0082's soft telemetry FKs). Actual source columns/constraints are checked
before writes. Real project-scoped TraceSession rows are supplied even though
Trace.session is decoupled. Nothing here imports target migrations or accesses
publication tables, and bulk_create does not call product publication hooks.

Per 100k traces: 80k otherwise-live traces, and 5k each deleted-trace,
deleted-project, null-session, and no-PG-span controls. Only the FIRST 1,250 of
the otherwise-live traces are demo; the rest are prototype. This bounds eventual
external publication to 1,250 traces / 2,500 spans even at larger scales, while
exercising two full 500-row migration batches and a 250-row tail. Every trace
except the no-span control has TWO spans (root + child), hence 190k actual span
rows per block; Exists must not duplicate publications. Sessions group up to
100 traces, never across projects. Payloads have varied inputs/results, status,
latency, timestamps and tags, not cloned/default blobs. Dedicated projects and a
separate UUID namespace leave both product witnesses and ErrorFeed fixtures alone.

Plans are expectations, never measurements. The shipping driver's independent
count(*) / pg_total_relation_size measurement remains authoritative. Host code is
stdlib; fixed programs below run only in the source app via its 180s app_python.
"""
from __future__ import annotations

import time


TRACE_REFERENCE_MODELS = {
    ('tracer', 'Trace'): 'tracer_trace',
    ('tracer', 'ObservationSpan'): 'tracer_observation_span',
}
CHUNK_TRACES = 2500
ELIGIBLE_TRACES = 1250
COHORTS = ('eligible', 'non_demo', 'deleted_trace', 'deleted_project', 'null_session', 'no_span')


def trace_reference_plan(scale):
    assert type(scale) is int and scale > 0
    return dict(counts={'tracer_trace': 100000 * scale, 'tracer_observation_span': 190000 * scale},
        cohorts=dict(eligible=ELIGIBLE_TRACES, non_demo=80000 * scale - ELIGIBLE_TRACES,
            deleted_trace=5000 * scale, deleted_project=5000 * scale,
            null_session=5000 * scale, no_span=5000 * scale, eligible_spans=2 * ELIGIBLE_TRACES))


# Pure payload logic is shared verbatim by all container operations and tests.
ROWS = '\nCOHORTS = ' + repr(COHORTS) + '\n' + """
import uuid
from datetime import datetime, timedelta
NAMESPACE=uuid.uuid5(uuid.NAMESPACE_URL,'futureagi-qualification-trace-reference:'+a['namespace'])
def identity(kind,ordinal,index=0):
    return uuid.uuid5(NAMESPACE,f'{kind}:{ordinal}:{index}')
def cohort(ordinal):
    assert type(ordinal) is int and ordinal>=0
    if ordinal<1250: return 'eligible'
    local=ordinal%100000
    if local<80000: return 'non_demo'
    return COHORTS[2+(local-80000)//5000]
def project_payloads(context):
    when=datetime.fromisoformat(context['historical_at'])
    return [dict(id=identity('project',name),
        organization_id=uuid.UUID(a['organization_id']),workspace_id=uuid.UUID(a['workspace_id']),
        user_id=uuid.UUID(context['user_id']),name='qualification-trace-'+NAMESPACE.hex[:12]+'-'+name,
        model_type='GenerativeLLM',trace_type='observe',source='prototype' if name=='non_demo' else 'demo',
        metadata={'qualification_trace_reference':1,'namespace':a['namespace'],'requested_rows':a['rows'],'cohort':name},
        config=[],session_config=[],tags=['qualification-reference'],simulation_agent_id=None,
        deleted=name=='deleted_project',deleted_at=when if name=='deleted_project' else None,
        created_at=when,updated_at=when) for name in COHORTS]
def payloads(context,start,stop):
    when=datetime.fromisoformat(context['historical_at'])
    sessions,traces,spans={},[],[]
    for ordinal in range(start,stop):
        name=cohort(ordinal)
        project=identity('project',name)
        session=None if name=='null_session' else identity('session',name,ordinal//100)
        trace=identity('trace',ordinal)
        at=when+timedelta(seconds=ordinal*2)
        duration=80+ordinal%420
        failed=ordinal%17==0
        question={'question':'Find the return policy for order '+str(10000+ordinal), 'locale':('en','fr','de')[ordinal%3]}
        answer={'answer':('Policy lookup unavailable' if failed else 'Returns accepted within 30 days'),
            'order':10000+ordinal,'sources':['policy-'+str(ordinal%31)]}
        history=dict(created_at=when,updated_at=when,deleted=False,deleted_at=None)
        if session is not None:
            sessions[session]=dict(history,id=session,project_id=project,
                name='Reference support session '+str(ordinal//100),bookmarked=ordinal//100%10==0)
        traces.append(dict(history,id=trace,project_id=project,project_version_id=None,
            session_id=session,name='Support policy lookup '+str(ordinal),external_id='qr-trace-'+str(ordinal),
            metadata={'qualification_trace_reference':1,'ordinal':ordinal,'cohort':name},
            input=question,output=answer,error={'kind':'retrieval_timeout'} if failed else None,
            tags=['qualification-reference',name],error_analysis_status='skipped',
            deleted=name=='deleted_trace',deleted_at=when if name=='deleted_trace' else None))
        if name=='no_span': continue
        for index in range(2):
            # UUID-derived 32-hex IDs, like existing source demo span IDs.
            span_id=identity('span',ordinal,index).hex
            elapsed=duration if index==0 else duration-20
            spans.append(dict(history,id=span_id,project_id=project,project_version_id=None,trace_id=trace,
                parent_span_id=None if index==0 else identity('span',ordinal,0).hex,
                name=('Policy assistant' if index==0 else 'Search policy index')+' '+str(ordinal),
                observation_type='agent' if index==0 else 'retriever',operation_name=None,
                start_time=at,end_time=at+timedelta(milliseconds=elapsed),latency_ms=elapsed,
                response_time=elapsed/1000.0,input=question,output=answer,
                org_id=uuid.UUID(a['organization_id']),org_user_id=uuid.UUID(context['user_id']),
                status='ERROR' if failed else 'OK',status_message='Index request timed out' if failed else None,
                metadata={'qualification_trace_reference':1,'ordinal':ordinal,'cohort':name,'index':index},
                tags=['qualification-reference',name],span_events=[],
                span_attributes={'reference.ordinal':ordinal,'reference.child':bool(index)},
                resource_attributes={'service.name':'qualification-policy-assistant'},semconv_source='traceai'))
    return list(sessions.values()),traces,spans
"""

COMMON = """
from django.apps import apps
from django.db import connection, transaction, models
from django.db.models import Q, Exists, OuterRef
Project=apps.get_model('tracer','Project')
Session=apps.get_model('tracer','TraceSession')
Trace=apps.get_model('tracer','Trace')
Span=apps.get_model('tracer','ObservationSpan')
User=apps.get_model('accounts','User')
Workspace=apps.get_model('accounts','Workspace')
""" + ROWS + """
def physical_counts():
    counts={}
    with connection.cursor() as cursor:
        for model in (Trace,Span):
            cursor.execute('SELECT count(*) FROM '+connection.ops.quote_name(model._meta.db_table))
            counts[model._meta.db_table]=cursor.fetchone()[0]
    return counts
def subsets():
    projects=[identity('project',name) for name in COHORTS]
    return (Trace.all_objects.filter(project_id__in=projects),Span.all_objects.filter(project_id__in=projects))
def seeded_counts():
    return {query.model._meta.db_table:query.count() for query in subsets()}
def expected_objects(model,rows):
    return [model(**row) for row in rows]
def matching(model,objects):
    # Compare every concrete field, including defaults/FKs/soft-delete state,
    # not only a chunk sentinel or a namespace count. Never hide conflicts.
    fields=[f.attname for f in model._meta.concrete_fields]
    expected={str(obj.pk):tuple(getattr(obj,key) for key in fields) for obj in objects}
    actual={str(row[fields.index(model._meta.pk.attname)]):row for row in
        model.all_objects.filter(pk__in=[obj.pk for obj in objects]).values_list(*fields)}
    assert actual==expected, 'trace_reference: incompatible existing '+model.__name__
def check_context(context):
    matching(Project,expected_objects(Project,project_payloads(context)))
"""

SCHEMA_CHECK = """
# Reviewed inventories are identical in the two supported source tags. Do not
# silently drop unknown required fields, accept missing tables, or weaken FKs.
assert connection.vendor=='postgresql', 'trace_reference: PostgreSQL required'
expected_fields={
    Project:'id created_at updated_at deleted deleted_at organization workspace model_type name trace_type metadata config session_config user source tags simulation_agent',
    Session:'id created_at updated_at deleted deleted_at project bookmarked name',
    Trace:'id created_at updated_at deleted deleted_at project project_version name metadata input output error session external_id tags error_analysis_status',
    Span:'id created_at updated_at deleted deleted_at project project_version trace parent_span_id name observation_type operation_name start_time end_time input output model model_parameters latency_ms org_id org_user_id prompt_tokens completion_tokens total_tokens response_time eval_id cost status status_message tags metadata span_events provider input_images eval_input eval_attributes custom_eval_config eval_status end_user prompt_version prompt_label span_attributes resource_attributes semconv_source',
}
expected_tables={Project:'tracer_project',Session:'trace_session',Trace:'tracer_trace',Span:'tracer_observation_span'}
project_unique=models.UniqueConstraint(fields=['name','trace_type','organization','workspace'],
    condition=Q(deleted=False),name='unique_project_per_org_type')
for model,names in expected_fields.items():
    assert model._meta.managed and model._meta.db_table==expected_tables[model], 'trace_reference: table/managed drift'
    assert {f.name for f in model._meta.concrete_fields}==set(names.split()), 'trace_reference: field inventory drift'
    assert model._meta.pk.get_internal_type()==('CharField' if model==Span else 'UUIDField'), 'trace_reference: pk type drift'
    if model==Span: assert model._meta.pk.max_length==255, 'trace_reference: span id drift'
    assert not model._meta.unique_together, 'trace_reference: uniqueness drift'
    assert model._meta.constraints==([project_unique] if model==Project else []), 'trace_reference: constraints drift'
    assert {f.name for f in model._meta.concrete_fields if f.unique and not f.primary_key}==({'simulation_agent'} if model==Project else set()), 'trace_reference: unique field drift'
    for name,kind in [('deleted','BooleanField'),('deleted_at','DateTimeField'),('created_at','DateTimeField'),('updated_at','DateTimeField')]:
        assert model._meta.get_field(name).get_internal_type()==kind, 'trace_reference: history type drift'
    assert model._meta.get_field('deleted_at').null, 'trace_reference: history nullability drift'
    with connection.cursor() as cursor:
        columns={column.name for column in connection.introspection.get_table_description(cursor,model._meta.db_table)}
        assert {f.column for f in model._meta.concrete_fields}<=columns, 'trace_reference: missing physical column'
        constraints=connection.introspection.get_constraints(cursor,model._meta.db_table)
    assert any(c.get('primary_key') and c['columns']==[model._meta.pk.column] for c in constraints.values()), 'trace_reference: physical pk missing'
    for name,c in constraints.items():
        if c.get('unique') and not c.get('primary_key'):
            assert model==Project and ((name==project_unique.name and c['columns']==['name','trace_type','organization_id','workspace_id']) or c['columns']==['simulation_agent_id']), 'trace_reference: physical unique drift'
        assert not c.get('check'), 'trace_reference: unexpected physical check'
    if model==Project:
        assert constraints.get(project_unique.name,{}).get('unique'), 'trace_reference: physical project unique missing'
        assert any(c.get('unique') and c['columns']==['simulation_agent_id'] for c in constraints.values()), 'trace_reference: physical simulation unique missing'
    for field in model._meta.concrete_fields:
        if not field.many_to_one and not field.one_to_one: continue
        target=field.remote_field.model
        foreign=[c['foreign_key'] for c in constraints.values() if c.get('foreign_key') and c['columns']==[field.column]]
        assert foreign==([(target._meta.db_table,field.target_field.column)] if field.db_constraint else []), 'trace_reference: physical FK drift'
for model,name,target,nullable,constrained in [(Session,'project',Project,False,True),
        (Trace,'project',Project,False,True),(Trace,'session',Session,True,False),
        (Span,'project',Project,False,True),(Span,'trace',Trace,False,False),
        (Project,'user',User,True,True),(Project,'workspace',Workspace,True,True)]:
    field=model._meta.get_field(name)
    assert field.many_to_one and field.remote_field.model==target and field.null==nullable and field.db_constraint==constrained, 'trace_reference: relation drift'
for model,types in [(Project,dict(CharField='name source model_type trace_type',JSONField='metadata config session_config tags')),
        (Session,dict(CharField='name',BooleanField='bookmarked')),
        (Trace,dict(CharField='name external_id error_analysis_status',JSONField='metadata input output error tags')),
        (Span,dict(CharField='name parent_span_id observation_type operation_name status semconv_source',
            UUIDField='org_id org_user_id',DateTimeField='start_time end_time',IntegerField='latency_ms',
            FloatField='response_time',TextField='status_message',
            JSONField='metadata input output tags span_events span_attributes resource_attributes'))]:
    for kind,names in types.items():
        for name in names.split():
            assert model._meta.get_field(name).get_internal_type()==kind, 'trace_reference: payload type drift'
for model,name,choices in [(Project,'source',{'demo','prototype'}),(Project,'trace_type',{'observe'}),
        (Project,'model_type',{'GenerativeLLM'}),(Trace,'error_analysis_status',{'skipped'}),
        (Span,'observation_type',{'agent','retriever'}),(Span,'status',{'OK','ERROR'})]:
    assert choices <= {value for value,label in model._meta.get_field(name).flatchoices}, 'trace_reference: choice drift'
"""

PREPARE_BODY = """
user=User.objects.get(email=a['email'],organization_id=a['organization_id'],is_active=True)
workspace=Workspace.all_objects.get(id=a['workspace_id'],organization_id=a['organization_id'],deleted=False,is_active=True)
assert workspace.created_by_id==user.id, 'trace_reference: source account/workspace mismatch'
context=dict(user_id=str(user.id),historical_at=(workspace.created_at-timedelta(days=7)).isoformat())
objects=expected_objects(Project,project_payloads(context))
with transaction.atomic():
    existing=Project.all_objects.filter(pk__in=[obj.pk for obj in objects]).count()
    assert existing in (0,len(objects)), 'trace_reference: partial context'
    if not existing:
        Project.all_objects.bulk_create(objects,batch_size=100)
        Project.all_objects.filter(pk__in=[obj.pk for obj in objects]).update(
            created_at=datetime.fromisoformat(context['historical_at']),updated_at=datetime.fromisoformat(context['historical_at']))
    check_context(context)
print(json.dumps(dict(context=context,counts=physical_counts(),seeded_counts=seeded_counts())))
"""

CHUNK_BODY = """
context=a['context']
start,stop,scale=a['start'],a['stop'],a['scale']
assert type(scale) is int and scale>0 and 0<=start<stop<=100000*scale, 'trace_reference: invalid slice'
assert start%2500==0 and stop==start+2500, 'trace_reference: unbounded/misaligned slice'
check_context(context)
batches=[(model,expected_objects(model,rows)) for model,rows in zip((Session,Trace,Span),payloads(context,start,stop))]
with transaction.atomic():
    counts=[model.all_objects.filter(pk__in=[obj.pk for obj in objects]).count() for model,objects in batches]
    fresh=all(count==0 for count in counts)
    assert fresh or counts==[len(objects) for model,objects in batches], 'trace_reference: partial atomic chunk'
    if fresh:
        for model,objects in batches:
            if not objects: continue
            model.all_objects.bulk_create(objects,batch_size=500)
            model.all_objects.filter(pk__in=[obj.pk for obj in objects]).update(
                created_at=datetime.fromisoformat(context['historical_at']),updated_at=datetime.fromisoformat(context['historical_at']))
    # Rebuild: Django bulk_create overwrites auto_now(_add) on the objects.
    for model,rows in zip((Session,Trace,Span),payloads(context,start,stop)):
        matching(model,expected_objects(model,rows))
print(json.dumps(dict(skipped=not fresh)))
"""

VERIFY_BODY = """
context=a['context']
check_context(context)
# Re-read every expected value with bounded memory. Counts alone cannot detect
# replay with edited payloads, substituted IDs, different sessions or FK scope.
for start in range(0,100000*a['scale'],2500):
    for model,rows in zip((Session,Trace,Span),payloads(context,start,start+2500)):
        matching(model,expected_objects(model,rows))
traces,spans=subsets()
# The exact 0030 predicate on source tables, separately scoped to our projects.
# all_objects is intentional: historical migration managers do NOT soft-filter
# Span rows. The predicate only tests existence of a PostgreSQL span.
eligible=traces.filter(project__source='demo',project__deleted=False,deleted=False,session_id__isnull=False).filter(
    Exists(Span.all_objects.filter(trace_id=OuterRef('pk'))))
cohorts={name:traces.filter(project_id=identity('project',name)).count() for name in COHORTS if name!='eligible'}
cohorts['eligible']=eligible.count()
cohorts['eligible_spans']=spans.filter(trace_id__in=eligible.values('pk')).count()
print(json.dumps(dict(counts=physical_counts(),seeded_counts=seeded_counts(),cohorts=cohorts)))
"""

PREPARE = COMMON + SCHEMA_CHECK + PREPARE_BODY
CHUNK = COMMON + CHUNK_BODY
VERIFY = COMMON + VERIFY_BODY


def seed_trace_reference_family(driver, rows, *, prefix, producer):
    """Complete both source tables before any independent per-table measurement."""
    def require(ok):
        if not ok:
            raise producer.ManualRelease('high_volume_reference_unavailable')
    require(type(rows) is int and 100000 <= rows <= 100000000)
    scale = (rows + 99999) // 100000
    plan = trace_reference_plan(scale)
    namespaces = getattr(getattr(driver, 'witnesses', None), 'namespaces', None)
    require(isinstance(namespaces, dict))
    source = namespaces.get('source')
    require(isinstance(source, dict))
    headers, account = source.get('headers'), source.get('account')
    require(isinstance(headers, dict) and isinstance(account, dict))
    arguments = dict(namespace=source.get('namespace'), email=account.get('email'),
        organization_id=headers.get('X-Organization-Id'), workspace_id=headers.get('X-Workspace-Id'))
    require(all(isinstance(value, str) and bool(value) for value in arguments.values()))
    # No bearer token/password/API key leaves the witness corpus.
    arguments['rows'] = rows
    complete = getattr(driver, '_trace_reference', None)
    state = complete or getattr(driver, '_trace_reference_pending', None)
    if state is not None:
        require(state['arguments'] == arguments)
    else:
        prepared = driver.app_python(prefix + PREPARE, arguments)
        require(isinstance(prepared, dict) and isinstance(prepared.get('context'), dict))
        before, seeded = prepared.get('counts'), prepared.get('seeded_counts')
        require(isinstance(before, dict) and isinstance(seeded, dict))
        require(set(before) == set(plan['counts']) and set(seeded) == set(plan['counts']))
        for table, expected in plan['counts'].items():
            require(type(before[table]) is int and type(seeded[table]) is int
                and 0 <= seeded[table] <= expected and before[table] >= seeded[table])
        state = dict(arguments=arguments, context=prepared['context'],
            baseline={table: before[table] - seeded[table] for table in plan['counts']})
        driver._trace_reference_pending = state
    if complete is None:
        chunk_seconds = []
        for start in range(0, plan['counts']['tracer_trace'], CHUNK_TRACES):
            began = time.monotonic()
            result = driver.app_python(prefix + CHUNK, dict(arguments, context=state['context'],
                start=start, stop=start + CHUNK_TRACES, scale=scale))
            chunk_seconds.append(time.monotonic() - began)
            require(isinstance(result, dict) and type(result.get('skipped')) is bool)
    verified = driver.app_python(prefix + VERIFY, dict(arguments, context=state['context'], scale=scale))
    require(isinstance(verified, dict))
    counts, seeded, cohorts = (verified.get(key) for key in ('counts', 'seeded_counts', 'cohorts'))
    for actual, expected in ((seeded, plan['counts']), (cohorts, plan['cohorts'])):
        require(isinstance(actual, dict) and set(actual) == set(expected))
        require(all(type(actual[key]) is int and actual[key] == value for key, value in expected.items()))
    require(isinstance(counts, dict) and set(counts) == set(plan['counts']))
    for table, added in plan['counts'].items():
        require(type(counts[table]) is int and counts[table] >= rows
            and counts[table] >= state['baseline'][table] + added)
    if complete is not None:
        return
    if getattr(driver, 'corpus', None) is not None:
        driver.corpus.setdefault('reference_seed_operations', []).append(dict(
            table='tracer_trace_span_family', operation='trace_reference_fixture', version=1,
            requested_rows=rows, scale=scale, chunks=len(chunk_seconds), chunk_seconds=chunk_seconds,
            counts=dict(counts), cohorts=dict(cohorts)))
    driver._trace_reference = dict(state, counts=dict(counts), cohorts=dict(cohorts))
    del driver._trace_reference_pending
