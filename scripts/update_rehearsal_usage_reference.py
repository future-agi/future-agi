"""Source-compatible APICallLog history, not billing activity or an API witness.

Reviewed v1.56.1 (6840e44914cf7c85cd0ae65d98761f7728b8afcc) and v1.58.0
(28e729aab169d75a2ca856fb4ed67fd1dda5901a): usage models and BaseModel
are identical. usage_entries.check_if_api_call_is_rate_limited counts
success/processing; usage_sqls selects deducted_cost > 0;
refund_cost_for_api_call rejects nonpositive deductions. We create only terminal failures with both monetary fields zero,
never invoke those helpers, metering, resource logging, Redis or workflows.

post_migrate calls create_usage_entries, which normally creates these canonical
lookup rows but catches failures. Read them first; create only a missing source
choice with its canonical description, never run the broad bootstrap helper.
Existing (including deleted) conflicts refuse; no lookup is updated or revived.

Financial snapshots bracket each atomic write, scoped to the witness organization
and applicable pricing/limits, not unrelated organizations. They are NOT a global
freeze across normal target bootstrap/product activity. Preservation reads check
all source log fields, assigned PKs, owned relations, exact cohorts and no events
attributable to fixture source IDs. Lookup auto_now timestamps and target-added
columns are outside that claim. Physical SQL counts and the driver's independent
pg_total_relation_size measurement, never this plan, establish reference scale.
"""
from __future__ import annotations

import time

USAGE_REFERENCE_MODELS = {('usage', 'APICallLog'): 'usage_apicalllog'}
CHUNK_ROWS = 2500
COHORTS = {'error': 40000, 'rate_limited': 30000, 'insufficient_credits': 20000, 'resource_limit': 10000}
# Literal source-bootstrap payloads, not target-only jev_evaluator.
CALL_TYPES = {
    'prompt_bench': 'This is the API for PROMPT BENCH',
    'dataset_protect': 'This is the API for protect',
    'auto_annotation': 'This is the API for auto annotation',
    'observe_add': 'This is the API for adding observe',
}
BASE_FIELDS = {
    'id': ('BigAutoField', False, None, None),
    'created_at': ('DateTimeField', False, None, None),
    'updated_at': ('DateTimeField', False, None, None),
    'deleted': ('BooleanField', False, None, None),
    'deleted_at': ('DateTimeField', True, None, None),
}
SOURCE_FIELDS = {
    'APICallType': dict(BASE_FIELDS, name=('CharField', False, 50, None), description=('TextField', False, None, None)),
    'APICallLog': dict(BASE_FIELDS,
        log_id=('UUIDField', False, 32, None),
        organization=('ForeignKey', False, None, 'accounts.Organization'),
        workspace=('ForeignKey', True, None, 'accounts.Workspace'),
        user=('ForeignKey', True, None, 'accounts.User'),
        api_call_type=('ForeignKey', True, None, 'usage.APICallType'),
        cost=('DecimalField', False, None, None),
        deducted_cost=('DecimalField', False, None, None),
        status=('CharField', False, 50, None),
        refund_parent_id=('CharField', True, 100, None),
        reference_id=('CharField', True, 100, None),
        config=('JSONField', False, None, None),
        input_token_count=('PositiveIntegerField', True, None, None),
        source=('CharField', True, 100, None), source_id=('CharField', True, 100, None)),
}


def usage_reference_plan(scale):
    assert type(scale) is int and scale > 0
    return dict(counts={'usage_apicalllog': 100000 * scale},
        cohorts={name: count * scale for name, count in COHORTS.items()}, deleted=10000 * scale)


ROWS = '\nSOURCE_FIELDS = ' + repr(SOURCE_FIELDS) + '\nCALL_TYPES = ' + repr(CALL_TYPES) + '\n' + """
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
NAMESPACE=uuid.uuid5(uuid.NAMESPACE_URL,'futureagi-qualification-usage-reference:'+a['namespace'])
SOURCE_ID='qualification-usage-'+NAMESPACE.hex
def identity(kind,ordinal):
    return uuid.uuid5(NAMESPACE,f'{kind}:{ordinal}')
def cohort(ordinal):
    local=ordinal%100000
    return 'error' if local<40000 else ('rate_limited' if local<70000 else ('insufficient_credits' if local<90000 else 'resource_limit'))
def payloads(context,start,stop):
    when=datetime.fromisoformat(context['historical_at'])
    rows=[]
    for ordinal in range(start,stop):
        status=cohort(ordinal)
        kind={'error':'prompt_bench','rate_limited':'dataset_protect',
            'insufficient_credits':'auto_annotation','resource_limit':'observe_add'}[status]
        created=when+timedelta(days=ordinal%300,seconds=ordinal%86400)
        updated=created+timedelta(minutes=1+ordinal%60)
        deleted=status=='resource_limit'
        rows.append(dict(log_id=identity('log',ordinal),
            organization_id=uuid.UUID(a['organization_id']),
            workspace_id=uuid.UUID(a['workspace_id']) if ordinal%4!=0 else None,
            user_id=uuid.UUID(context['user_id']) if ordinal%3!=0 else None,
            api_call_type_id=context['call_types'][kind],cost=Decimal('0'),deducted_cost=Decimal('0'),
            status=status,refund_parent_id=None,
            reference_id=str(identity('request',ordinal)) if ordinal%4 else None,
            config={'qualification_usage_reference':1,'namespace':a['namespace'],'requested_rows':a['rows'],
                'ordinal':ordinal,'operation':kind,'outcome':status,'attempt':1+ordinal%3,
                'input':{'format':('text','json')[ordinal%2],'items':1+ordinal%16}},
            input_token_count=None if ordinal%5==0 else (0 if ordinal%5==1 else 32+ordinal%4096),
            source=kind,source_id=SOURCE_ID,created_at=created,updated_at=updated,
            deleted=deleted,deleted_at=updated if deleted else None))
    return rows
"""

COMMON = """
from django.apps import apps
from django.db import connection, transaction, models
from django.db.models import Q
from django.core.serializers.json import DjangoJSONEncoder
from tfc.middleware.workspace_context import get_current_workspace, get_current_organization
import hashlib
# all_objects includes deleted rows but still honors workspace ContextVars.
# These isolated programs must have no request context: never accept a silently
# scoped replay check, or let BaseModel.save auto-assign a lookup's context.
assert get_current_workspace() is None and get_current_organization() is None, 'usage_reference: unexpected request context'
Log=apps.get_model('usage','APICallLog')
CallType=apps.get_model('usage','APICallType')
User=apps.get_model('accounts','User')
Workspace=apps.get_model('accounts','Workspace')
""" + ROWS + """
def owner_context():
    user=User.objects.get(email=a['email'],organization_id=a['organization_id'],is_active=True)
    workspace=Workspace.all_objects.get(id=a['workspace_id'],organization_id=a['organization_id'],deleted=False,is_active=True)
    assert workspace.created_by_id==user.id, 'usage_reference: source account/workspace mismatch'
    return dict(user_id=str(user.id),historical_at=(workspace.created_at-timedelta(days=400)).isoformat())
def check_lookup(obj,name):
    assert obj.name==name and obj.description==CALL_TYPES[name] and not obj.deleted and obj.deleted_at is None, 'usage_reference: lookup conflict'
def check_context(context):
    assert {key:context[key] for key in ('user_id','historical_at')}==owner_context(), 'usage_reference: owner drift'
    assert set(context['call_types'])==set(CALL_TYPES), 'usage_reference: lookup inventory drift'
    for name,pk in context['call_types'].items():
        check_lookup(CallType.all_objects.get(pk=pk),name)
def subset():
    # No default-manager soft-delete/workspace filters; foreign owners conflict.
    return Log.all_objects.filter(source_id=SOURCE_ID)
def matching(rows):
    fields=[Log._meta.get_field(name).attname for name in SOURCE_FIELDS['APICallLog'] if name!='id']
    expected={row['log_id']:tuple(row[key] for key in fields) for row in rows}
    actual={row[fields.index('log_id')]:row for row in
        Log.all_objects.filter(log_id__in=list(expected)).values_list(*fields)}
    assert actual==expected, 'usage_reference: incompatible existing log'
def identity_digest(rows,digest):
    # Sequence-assigned PKs are not manufactured, but must survive B/restore.
    actual=dict(Log.all_objects.filter(log_id__in=[r['log_id'] for r in rows]).values_list('log_id','id'))
    for row in rows:
        pk=actual[row['log_id']]
        assert type(pk) is int and pk>0, 'usage_reference: invalid assigned PK'
        digest.update((str(row['log_id'])+':'+str(pk)+'\\n').encode())
def check_no_events():
    events=apps.get_model('usage','UsageEventLog').objects.filter(organization_id=a['organization_id'])
    assert not events.filter(properties__source_id=SOURCE_ID).exists(), 'usage_reference: unexpected usage event'
def financial_snapshot():
    # Bounded and owner-scoped. Hashes never expose financial row payloads.
    result={}
    for name in ('OrganizationSubscription','OrganizationBilling','CreditBalance','UsageEventLog',
            'UsageSummary','Invoice','InvoiceLineItem','UsageBudget','Pricing','RateLimit','ResourceLimits',
            'PlanPricing','PlanEntitlement'):
        model=apps.get_model('usage',name)
        manager=getattr(model,'all_objects',model._default_manager)
        if name=='InvoiceLineItem':
            query=manager.filter(invoice__organization_id=a['organization_id'])
        else:
            scope=Q(organization_id=a['organization_id'])
            if name in ('Pricing','RateLimit'):
                scope |= Q(organization_id__isnull=True,api_call_type__name__in=list(CALL_TYPES))
            elif name in ('ResourceLimits','PlanPricing','PlanEntitlement'):
                scope |= Q(organization_id__isnull=True)
            query=manager.filter(scope)
        # Includes values, identities and cardinality, including soft-deleted rows.
        records=list(query.order_by('pk').values()[:4097])
        assert len(records)<=4096, 'usage_reference: financial baseline unbounded'
        result[name]=hashlib.sha256(json.dumps(records,cls=DjangoJSONEncoder,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result
def observation():
    with connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM '+connection.ops.quote_name(Log._meta.db_table))
        count=cursor.fetchone()[0]
    logs=subset()
    return dict(counts={'usage_apicalllog':count},seeded_counts={'usage_apicalllog':logs.count()},
        cohorts={name:logs.filter(status=name).count() for name in ('error','rate_limited','insufficient_credits','resource_limit')},
        deleted=logs.filter(deleted=True).count())
"""

SCHEMA_CHECK = """
assert connection.vendor=='postgresql', 'usage_reference: PostgreSQL required'
for model,table,unique_name in ((Log,'usage_apicalllog','log_id'),(CallType,'usage_apicalltype','name')):
    specs=SOURCE_FIELDS[model._meta.object_name]
    assert model._meta.managed and model._meta.db_table==table, 'usage_reference: table/managed drift'
    assert {f.name for f in model._meta.concrete_fields}==set(specs), 'usage_reference: field inventory drift'
    assert not model._meta.constraints and not model._meta.unique_together and not model._meta.many_to_many, 'usage_reference: constraints drift'
    assert model._meta.pk.name=='id', 'usage_reference: PK drift'
    assert {f.name for f in model._meta.concrete_fields if f.unique and not f.primary_key}=={unique_name}, 'usage_reference: uniqueness drift'
    expected_fk={}
    for name,(kind,nullable,length,target) in specs.items():
        field=model._meta.get_field(name)
        assert (field.get_internal_type(),field.null,field.max_length)==(kind,nullable,length), 'usage_reference: field type drift '+name
        if target:
            assert field.remote_field.model._meta.label==target and field.db_constraint, 'usage_reference: FK drift'
            assert field.target_field.name=='id' and field.target_field.get_internal_type()==('BigAutoField' if target=='usage.APICallType' else 'UUIDField'), 'usage_reference: FK identity drift'
            assert field.remote_field.on_delete==models.CASCADE, 'usage_reference: FK deletion drift'
            expected_fk[(field.column,)]=(field.remote_field.model._meta.db_table,field.target_field.column)
    with connection.cursor() as cursor:
        columns=connection.introspection.get_table_description(cursor,table)
        actual=connection.introspection.get_constraints(cursor,table)
    assert {f.column:f.null for f in model._meta.concrete_fields}=={c.name:c.null_ok for c in columns}, 'usage_reference: physical columns drift'
    assert any(c.get('primary_key') and c['columns']==['id'] for c in actual.values()), 'usage_reference: physical PK missing'
    assert {tuple(c['columns']) for c in actual.values() if c.get('unique') and not c.get('primary_key')}=={(unique_name,)}, 'usage_reference: physical uniqueness drift'
    found=[(tuple(c['columns']),c['foreign_key']) for c in actual.values() if c.get('foreign_key')]
    assert len(found)==len(expected_fk) and dict(found)==expected_fk, 'usage_reference: physical FK drift'
    checks=[tuple(c['columns']) for c in actual.values() if c.get('check')]
    assert checks==([('input_token_count',)] if model==Log else []), 'usage_reference: physical check drift'
for name in ('cost','deducted_cost'):
    field=Log._meta.get_field(name)
    assert (field.max_digits,field.decimal_places)==(16,8), 'usage_reference: monetary precision drift'
assert {v for v,label in Log._meta.get_field('status').flatchoices}=={'success','error','not_started','rate_limited','processing','insufficient_credits','resource_limit'}, 'usage_reference: status choices drift'
assert set(CALL_TYPES)<={v for v,label in CallType._meta.get_field('name').flatchoices}, 'usage_reference: source call choices missing'
"""

PREPARE_BODY = """
context=owner_context()
with transaction.atomic():
    before=financial_snapshot()
    call_types={}
    for name,description in CALL_TYPES.items():
        obj=CallType.all_objects.filter(name=name).first()
        if obj is None:
            # No bootstrap/billing helpers, and no update_or_create on shared rows.
            obj=CallType.all_objects.create(name=name,description=description)
        check_lookup(obj,name)
        call_types[name]=obj.pk
    context['call_types']=call_types
    check_context(context)
    check_no_events()
    assert financial_snapshot()==before, 'usage_reference: preparation changed financial baseline'
print(json.dumps(dict(observation(),context=context)))
"""
SLICE = """
context=a['context']
start,stop,scale=a['start'],a['stop'],a['scale']
assert type(scale) is int and scale>0 and 0<=start<stop<=100000*scale, 'usage_reference: invalid slice'
assert start%2500==0 and stop==start+2500, 'usage_reference: unbounded/misaligned slice'
check_context(context)
rows=payloads(context,start,stop)
"""
CHUNK_BODY = SLICE + """
with transaction.atomic():
    before=financial_snapshot()
    count=Log.all_objects.filter(log_id__in=[row['log_id'] for row in rows]).count()
    fresh=count==0
    assert fresh or count==len(rows), 'usage_reference: partial atomic chunk'
    if fresh:
        batch=[Log(**row) for row in rows]
        Log.all_objects.bulk_create(batch,batch_size=500)
        # auto_now(_add) applies during bulk_create; restore only our new history.
        for obj,row in zip(batch,rows):
            obj.created_at,obj.updated_at=row['created_at'],row['updated_at']
        Log.all_objects.bulk_update(batch,['created_at','updated_at'],batch_size=500)
    matching(rows)
    check_no_events()
    assert financial_snapshot()==before, 'usage_reference: chunk changed financial baseline'
print(json.dumps(dict(skipped=not fresh)))
"""
VERIFY_BODY = """
check_context(a['context'])
assert type(a['scale']) is int and a['scale']>0, 'usage_reference: invalid scale'
digest=hashlib.sha256()
for start in range(0,100000*a['scale'],2500):
    rows=payloads(a['context'],start,start+2500)
    matching(rows)
    identity_digest(rows,digest)
check_no_events()
print(json.dumps(dict(observation(),identity_sha256=digest.hexdigest())))
"""
PREPARE = COMMON + SCHEMA_CHECK + PREPARE_BODY
CHUNK = COMMON + CHUNK_BODY
VERIFY = COMMON + VERIFY_BODY


def _require(producer, ok):
    if not ok:
        raise producer.ManualRelease('high_volume_reference_unavailable')


def _verify(driver, state, *, prefix, producer):
    arguments = state['arguments']
    scale = (arguments['rows'] + 99999) // 100000
    plan = usage_reference_plan(scale)
    result = driver.app_python(prefix + VERIFY, dict(arguments, context=state['context'], scale=scale))
    _require(producer, isinstance(result, dict))
    for key, expected in (('seeded_counts', plan['counts']), ('cohorts', plan['cohorts'])):
        actual = result.get(key)
        _require(producer, isinstance(actual, dict) and set(actual) == set(expected)
            and all(type(actual[k]) is int and actual[k] == v for k, v in expected.items()))
    counts = result.get('counts')
    _require(producer, isinstance(counts, dict) and set(counts) == {'usage_apicalllog'})
    count = counts['usage_apicalllog']
    _require(producer, type(count) is int and count >= state['baseline'] + plan['counts']['usage_apicalllog'])
    _require(producer, type(result.get('deleted')) is int and result['deleted'] == plan['deleted'])
    digest = result.get('identity_sha256')
    _require(producer, isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest))
    if 'identity_sha256' in state:
        _require(producer, state['identity_sha256'] == digest)
    return result


def verify_usage_reference(driver, *, prefix, producer):
    """Read-only source fixture preservation; never emits product_api witnesses."""
    _require(producer, not hasattr(driver, '_usage_reference_pending'))
    state = getattr(driver, '_usage_reference', None)
    if state is None:
        return
    _verify(driver, state, prefix=prefix, producer=producer)
    if getattr(driver, 'corpus', None) is not None:
        driver.corpus['usage_reference_preservation_reads'] = driver.corpus.get('usage_reference_preservation_reads', 0) + 1


def seed_usage_reference(driver, rows, *, prefix, producer):
    require = lambda ok: _require(producer, ok)
    require(type(rows) is int and 100000 <= rows <= 100000000)
    scale = (rows + 99999) // 100000
    plan = usage_reference_plan(scale)
    namespaces = getattr(getattr(driver, 'witnesses', None), 'namespaces', None)
    require(isinstance(namespaces, dict))
    source = namespaces.get('source')
    require(isinstance(source, dict))
    account, headers = source.get('account'), source.get('headers')
    require(isinstance(account, dict) and isinstance(headers, dict))
    arguments = dict(namespace=source.get('namespace'), email=account.get('email'),
        organization_id=headers.get('X-Organization-Id'), workspace_id=headers.get('X-Workspace-Id'))
    require(all(isinstance(value, str) and bool(value) for value in arguments.values()))
    arguments['rows'] = rows
    complete = getattr(driver, '_usage_reference', None)
    state = complete or getattr(driver, '_usage_reference_pending', None)
    if state is not None:
        require(state['arguments'] == arguments)
    else:
        prepared = driver.app_python(prefix + PREPARE, arguments)
        require(isinstance(prepared, dict) and isinstance(prepared.get('context'), dict))
        before, seeded = prepared.get('counts'), prepared.get('seeded_counts')
        require(isinstance(before, dict) and isinstance(seeded, dict)
            and set(before) == set(seeded) == {'usage_apicalllog'})
        before, seeded = before['usage_apicalllog'], seeded['usage_apicalllog']
        require(type(before) is int and type(seeded) is int and 0 <= seeded <= plan['counts']['usage_apicalllog'] and before >= seeded)
        state = dict(arguments=arguments, context=prepared['context'], baseline=before-seeded)
        driver._usage_reference_pending = state
    seconds = []
    if complete is None:
        for start in range(0, plan['counts']['usage_apicalllog'], CHUNK_ROWS):
            began = time.monotonic()
            result = driver.app_python(prefix + CHUNK, dict(arguments, context=state['context'],
                start=start, stop=start + CHUNK_ROWS, scale=scale))
            seconds.append(time.monotonic() - began)
            require(isinstance(result, dict) and type(result.get('skipped')) is bool)
    verified = _verify(driver, state, prefix=prefix, producer=producer)
    if complete is not None:
        return
    if getattr(driver, 'corpus', None) is not None:
        driver.corpus.setdefault('reference_seed_operations', []).append(dict(
            table='usage_apicalllog', operation='usage_reference_fixture', version=1,
            requested_rows=rows, scale=scale, chunks=len(seconds), chunk_seconds=seconds,
            counts=verified['counts'], cohorts=verified['cohorts'], deleted=verified['deleted']))
    state['identity_sha256'] = verified['identity_sha256']
    driver._usage_reference = state
    del driver._usage_reference_pending
