"""Source-only EvalTask relation-coverage fixture (not a task backfill).

Reviewed source objects: v1.56.1 6840e44914cf7c85cd0ae65d98761f7728b8afcc,
v1.58.0 28e729aab169d75a2ca856fb4ed67fd1dda5901a. Task, Project,
CustomEvalConfig, EvalTemplate and BaseModel declarations are identical there.
The source inventory deliberately excludes target-only analysis_source_generation.

Per 100k actual tasks: 50k completed historical, 20k failed historical, 20k
paused continuous, 10k soft-deleted/delete-status historical. No entries, run
loggers or workflows are created. selectors/eval_tasks/stranded.py selects only
pending/running (optionally failed) tasks WITH undrained EvalLogger entries;
these tasks have none, even with recover-failed enabled. Temporal's
_get_task_state_sync treats paused/failed/deleted as inactive. Completed tasks
are not sweepable; bulk_create bypasses product create/resume dispatch entirely.

Three private prototype projects, six configs, two private templates and exact
one/two-config membership per task provide real, bounded FK context. These
namespaces never overlap product witnesses, TraceSpan or ErrorFeed fixtures.
Every task source column and each membership pair is checked on replay and
again through target bootstrap/restoration reads. Target-added columns and
sequence-assigned through-table PKs are outside this preservation claim.
Physical count(*) and the shipping driver's pg_total_relation_size observation,
not this plan, establish the reference. No migration or SQL write is executed.
"""
from __future__ import annotations

import time


EVAL_TASK_REFERENCE_MODELS = {('tracer', 'EvalTask'): 'tracer_eval_task'}
CHUNK_TASKS = 2500
COHORTS = {'completed': 50000, 'failed': 20000, 'paused': 20000, 'deleted': 10000}


def eval_task_reference_plan(scale):
    assert type(scale) is int and scale > 0
    return dict(counts={'tracer_eval_task': 100000 * scale},
        cohorts={name: count * scale for name, count in COHORTS.items()},
        memberships=150000 * scale)


# Reviewed source-field contracts: type, nullability, max_length, FK label.
# Kept literal, not derived from the running (possibly drifted) model.
SOURCE_FIELDS = {'EvalTask': {'created_at': ('DateTimeField', False, None, None),
              'updated_at': ('DateTimeField', False, None, None),
              'deleted': ('BooleanField', False, None, None),
              'deleted_at': ('DateTimeField', True, None, None),
              'id': ('UUIDField', False, 32, None),
              'project': ('ForeignKey', False, None, 'tracer.Project'),
              'name': ('CharField', True, 255, None),
              'filters': ('JSONField', True, None, None),
              'sampling_rate': ('FloatField', True, None, None),
              'last_run': ('DateTimeField', True, None, None),
              'spans_limit': ('IntegerField', True, None, None),
              'run_type': ('CharField', True, 255, None),
              'status': ('CharField', True, 255, None),
              'start_time': ('DateTimeField', True, None, None),
              'end_time': ('DateTimeField', True, None, None),
              'continuous_cursor': ('DateTimeField', True, None, None),
              'reclassified_evals_revision': ('CharField', True, 64, None),
              'evals_details': ('JSONField', True, None, None),
              'failed_spans': ('JSONField', True, None, None),
              'row_type': ('CharField', False, 32, None)},
 'Project': {'created_at': ('DateTimeField', False, None, None),
             'updated_at': ('DateTimeField', False, None, None),
             'deleted': ('BooleanField', False, None, None),
             'deleted_at': ('DateTimeField', True, None, None),
             'id': ('UUIDField', False, 32, None),
             'organization': ('ForeignKey', False, None, 'accounts.Organization'),
             'workspace': ('ForeignKey', True, None, 'accounts.Workspace'),
             'model_type': ('CharField', False, 50, None),
             'name': ('CharField', False, 255, None),
             'trace_type': ('CharField', False, 20, None),
             'metadata': ('JSONField', True, None, None),
             'config': ('JSONField', True, None, None),
             'session_config': ('JSONField', True, None, None),
             'user': ('ForeignKey', True, None, 'accounts.User'),
             'source': ('CharField', False, 20, None),
             'tags': ('JSONField', False, None, None),
             'simulation_agent': ('OneToOneField', True, None, 'simulate.AgentDefinition')},
 'CustomEvalConfig': {'created_at': ('DateTimeField', False, None, None),
                      'updated_at': ('DateTimeField', False, None, None),
                      'deleted': ('BooleanField', False, None, None),
                      'deleted_at': ('DateTimeField', True, None, None),
                      'id': ('UUIDField', False, 32, None),
                      'eval_template': ('ForeignKey', False, None, 'model_hub.EvalTemplate'),
                      'name': ('CharField', True, 255, None),
                      'config': ('JSONField', True, None, None),
                      'mapping': ('JSONField', True, None, None),
                      'project': ('ForeignKey', False, None, 'tracer.Project'),
                      'filters': ('JSONField', True, None, None),
                      'error_localizer': ('BooleanField', False, None, None),
                      'kb_id': ('ForeignKey', True, None, 'model_hub.KnowledgeBaseFile'),
                      'model': ('CharField', True, 255, None),
                      'eval_group': ('ForeignKey', True, None, 'model_hub.EvalGroup')},
 'EvalTemplate': {'created_at': ('DateTimeField', False, None, None),
                  'updated_at': ('DateTimeField', False, None, None),
                  'deleted': ('BooleanField', False, None, None),
                  'deleted_at': ('DateTimeField', True, None, None),
                  'id': ('UUIDField', False, 32, None),
                  'name': ('CharField', False, 2000, None),
                  'description': ('CharField', True, 1000, None),
                  'organization': ('ForeignKey', True, None, 'accounts.Organization'),
                  'workspace': ('ForeignKey', True, None, 'accounts.Workspace'),
                  'owner': ('CharField', False, 50, None),
                  'eval_tags': ('ArrayField', False, None, None),
                  'config': ('JSONField', False, None, None),
                  'eval_id': ('IntegerField', False, None, None),
                  'criteria': ('CharField', True, 100000, None),
                  'choices': ('JSONField', True, None, None),
                  'multi_choice': ('BooleanField', True, None, None),
                  'model': ('CharField', True, 255, None),
                  'visible_ui': ('BooleanField', False, None, None),
                  'proxy_agi': ('BooleanField', False, None, None),
                  'evaluator_id': ('UUIDField', True, 32, None),
                  'template_type': ('CharField', False, 20, None),
                  'eval_type': ('CharField', False, 10, None),
                  'allow_edit': ('BooleanField', False, None, None),
                  'allow_copy': ('BooleanField', False, None, None),
                  'output_type_normalized': ('CharField', True, 20, None),
                  'pass_threshold': ('FloatField', True, None, None),
                  'choice_scores': ('JSONField', True, None, None),
                  'error_localizer_enabled': ('BooleanField', False, None, None),
                  'aggregation_enabled': ('BooleanField', False, None, None),
                  'aggregation_function': ('CharField', False, 20, None),
                  'composite_child_axis': ('CharField', False, 20, None)},
 'EvalTask_evals': {'id': ('BigAutoField', False, None, None),
                    'evaltask': ('ForeignKey', False, None, 'tracer.EvalTask'),
                    'customevalconfig': ('ForeignKey', False, None, 'tracer.CustomEvalConfig')}}

ROWS = '\nSOURCE_FIELDS = ' + repr(SOURCE_FIELDS) + '\n' + """
import uuid
from datetime import datetime, timedelta
NAMESPACE=uuid.uuid5(uuid.NAMESPACE_URL,'futureagi-qualification-eval-task-reference:'+a['namespace'])
ROW_TYPES=('spans','traces','sessions')
def identity(kind,ordinal):
    return uuid.uuid5(NAMESPACE,f'{kind}:{ordinal}')
def cohort(ordinal):
    local=ordinal%100000
    return 'completed' if local<50000 else ('failed' if local<70000 else ('paused' if local<90000 else 'deleted'))
def history(when):
    return dict(created_at=when,updated_at=when,deleted=False,deleted_at=None)
def support_payloads(context):
    when=datetime.fromisoformat(context['historical_at'])
    projects=[dict(history(when),id=identity('project',row_type),
        organization_id=uuid.UUID(a['organization_id']),workspace_id=uuid.UUID(a['workspace_id']),
        user_id=uuid.UUID(context['user_id']),name='qualification-task-'+NAMESPACE.hex[:12]+'-'+row_type,
        model_type='GenerativeLLM',trace_type='observe',source='prototype',
        metadata={'qualification_eval_task_reference':1,'namespace':a['namespace'],'requested_rows':a['rows']},
        config=[],session_config=[],tags=['qualification-task-reference'],simulation_agent_id=None)
        for row_type in ROW_TYPES]
    templates=[dict(history(when),id=identity('template',index),
        name='qualification-task-'+NAMESPACE.hex[:12]+'-'+str(index),
        description='Qualification task history; never dispatched.',
        organization_id=uuid.UUID(a['organization_id']),workspace_id=uuid.UUID(a['workspace_id']),
        owner='user',eval_tags=[],config={'output':'Pass/Fail'},eval_id=0,criteria='',choices=[],
        multi_choice=False,model=None,visible_ui=False,proxy_agi=True,evaluator_id=None,
        template_type='single',eval_type='llm',allow_edit=True,allow_copy=True,
        output_type_normalized='pass_fail',pass_threshold=1.0,choice_scores=None,
        error_localizer_enabled=False,aggregation_enabled=True,aggregation_function='weighted_avg',
        composite_child_axis='') for index in range(2)]
    configs=[dict(history(when),id=identity('config',row_type+str(index)),
        name='qualification-task-'+NAMESPACE.hex[:12]+'-'+row_type+'-'+str(index),
        project_id=identity('project',row_type),eval_template_id=identity('template',index),
        config={},mapping={'input':'input'},filters={},error_localizer=False,
        kb_id_id=None,model=None,eval_group_id=None) for row_type in ROW_TYPES for index in range(2)]
    return projects,templates,configs
def payloads(context,start,stop):
    when=datetime.fromisoformat(context['historical_at'])
    tasks,members=[],[]
    for ordinal in range(start,stop):
        status=cohort(ordinal)
        row_type=ROW_TYPES[ordinal%3]
        project=identity('project',row_type)
        task=identity('task',ordinal)
        created=when+timedelta(days=ordinal%300,seconds=ordinal%86400)
        start_time=created-timedelta(days=1+ordinal%7)
        continuous=status=='paused'
        end_time=None if continuous else created-timedelta(hours=1)
        filters={'project_id':str(project),'filters':[]}
        if ordinal%3 and end_time is not None:
            filters['date_range']=[start_time.isoformat(),end_time.isoformat()]
        if row_type=='spans' and ordinal%2:
            filters['filters']=[{'column_id':'ended_reason','filter_config':{
                'col_type':'SPAN_ATTRIBUTE','filter_op':'not_in','filter_type':'text',
                'filter_value':['voicemail','silence-timed-out']}}]
        tasks.append(dict(id=task,project_id=project,
            name='Qualification '+status+' '+row_type+' '+str(ordinal),filters=filters,
            sampling_rate=(1.0,10.0,25.0,50.0,100.0)[ordinal%5],
            spans_limit=(100,1000,10000)[ordinal%3],run_type='continuous' if continuous else 'historical',
            status=status,row_type=row_type,start_time=start_time,end_time=end_time,
            continuous_cursor=created-timedelta(minutes=5+ordinal%60) if continuous else None,
            reclassified_evals_revision=None,evals_details=[],failed_spans=[],
            last_run=created+timedelta(hours=1) if ordinal%4 else None,
            created_at=created,updated_at=created+timedelta(hours=2),deleted=status=='deleted',
            deleted_at=created+timedelta(hours=2) if status=='deleted' else None))
        for index in range(1+ordinal%2):
            members.append(dict(evaltask_id=task,customevalconfig_id=identity('config',row_type+str(index))))
    return tasks,members
"""

COMMON = """
from django.apps import apps
from django.db import connection, transaction, models
from django.db.models import Q
Project=apps.get_model('tracer','Project')
Template=apps.get_model('model_hub','EvalTemplate')
Config=apps.get_model('tracer','CustomEvalConfig')
Task=apps.get_model('tracer','EvalTask')
Log=apps.get_model('tracer','EvalLogger')
Run=apps.get_model('tracer','EvalTaskLogger')
User=apps.get_model('accounts','User')
Workspace=apps.get_model('accounts','Workspace')
Membership=Task._meta.get_field('evals').remote_field.through
""" + ROWS + """
def objects(model,rows):
    return [model(**row) for row in rows]
def matching(model,rows):
    # Only the reviewed source columns: target-added columns are not history.
    fields=[model._meta.get_field(name).attname for name in SOURCE_FIELDS[model._meta.object_name]]
    expected={str(row['id']):tuple(row[key] for key in fields) for row in rows}
    actual={str(row[fields.index('id')]):row for row in
        model.all_objects.filter(pk__in=[row['id'] for row in rows]).values_list(*fields)}
    assert actual==expected, 'eval_task_reference: incompatible existing '+model.__name__
def check_context(context):
    for model,rows in zip((Project,Template,Config),support_payloads(context)):
        matching(model,rows)
def check_memberships(tasks,members):
    pairs=list(Membership.objects.filter(evaltask_id__in=[row['id'] for row in tasks]).values_list(
        'evaltask_id','customevalconfig_id'))
    expected={(row['evaltask_id'],row['customevalconfig_id']) for row in members}
    assert len(pairs)==len(expected) and set(pairs)==expected, 'eval_task_reference: membership conflict'
def check_no_work(tasks):
    ids=[row['id'] for row in tasks]
    assert not Log.all_objects.filter(eval_task_id__in=[str(pk) for pk in ids]).exists(), 'eval_task_reference: unexpected evaluation entries'
    assert not Run.all_objects.filter(eval_task_id__in=ids).exists(), 'eval_task_reference: unexpected runs'
def subset():
    return Task.all_objects.filter(project_id__in=[identity('project',row_type) for row_type in ROW_TYPES])
def observation():
    with connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM '+connection.ops.quote_name(Task._meta.db_table))
        count=cursor.fetchone()[0]
    tasks=subset()
    return dict(counts={'tracer_eval_task':count},seeded_counts={'tracer_eval_task':tasks.count()},
        cohorts={name:tasks.filter(status=name).count() for name in ('completed','failed','paused','deleted')},
        memberships=Membership.objects.filter(evaltask_id__in=tasks.values('id')).count())
"""

SCHEMA_CHECK = """
assert connection.vendor=='postgresql', 'eval_task_reference: PostgreSQL required'
expected_tables={Project:'tracer_project',Template:'model_hub_evaltemplate',
    Config:'tracer_custom_eval_config',Task:'tracer_eval_task',Membership:'tracer_eval_task_evals'}
unique={Project:[models.UniqueConstraint(fields=['name','trace_type','organization','workspace'],
    condition=Q(deleted=False),name='unique_project_per_org_type')],
    Config:[models.UniqueConstraint(fields=['name','project'],condition=Q(deleted=False),name='unique_name_project_idx')]}
for model in (Project,Template,Config,Task,Membership):
    specs=SOURCE_FIELDS[model._meta.object_name]
    assert model._meta.managed and model._meta.db_table==expected_tables[model], 'eval_task_reference: table/managed drift'
    assert {f.name for f in model._meta.concrete_fields}==set(specs), 'eval_task_reference: field inventory drift'
    assert model._meta.constraints==unique.get(model,[]), 'eval_task_reference: constraints drift'
    expected_unique={('evaltask','customevalconfig')} if model==Membership else set()
    assert {tuple(fields) for fields in model._meta.unique_together}==expected_unique, 'eval_task_reference: membership uniqueness drift'
    assert {f.name for f in model._meta.concrete_fields if f.unique and not f.primary_key}==({'simulation_agent'} if model==Project else set()), 'eval_task_reference: unique field drift'
    assert model._meta.pk.name=='id', 'eval_task_reference: pk drift'
    for name,(kind,nullable,length,target) in specs.items():
        field=model._meta.get_field(name)
        assert (field.get_internal_type(),field.null,field.max_length)==(kind,nullable,length), 'eval_task_reference: field type drift '+name
        if target:
            assert field.remote_field.model._meta.label==target and field.db_constraint, 'eval_task_reference: FK drift '+name
            assert field.target_field.name=='id' and field.target_field.get_internal_type()=='UUIDField', 'eval_task_reference: FK identity drift'
            assert field.remote_field.on_delete==models.CASCADE, 'eval_task_reference: FK deletion drift'
    with connection.cursor() as cursor:
        columns=connection.introspection.get_table_description(cursor,model._meta.db_table)
        assert {f.column for f in model._meta.concrete_fields}=={c.name for c in columns}, 'eval_task_reference: physical columns drift'
        assert {f.column:f.null for f in model._meta.concrete_fields}=={c.name:c.null_ok for c in columns}, 'eval_task_reference: physical nullability drift'
        actual=connection.introspection.get_constraints(cursor,model._meta.db_table)
    assert any(c.get('primary_key') and c['columns']==['id'] for c in actual.values()), 'eval_task_reference: physical pk missing'
    allowed_unique={tuple(model._meta.get_field(f).column for f in constraint.fields) for constraint in unique.get(model,[])}
    if model==Project: allowed_unique.add(('simulation_agent_id',))
    if model==Membership: allowed_unique.add(('evaltask_id','customevalconfig_id'))
    assert {tuple(c['columns']) for c in actual.values() if c.get('unique') and not c.get('primary_key')}==allowed_unique, 'eval_task_reference: physical uniqueness drift'
    assert not any(c.get('check') for c in actual.values()), 'eval_task_reference: unexpected physical check'
    for constraint in unique.get(model,[]):
        assert actual.get(constraint.name,{}).get('unique'), 'eval_task_reference: named unique missing'
    expected_fk={}
    for field in model._meta.concrete_fields:
        if field.many_to_one or field.one_to_one:
            expected_fk[(field.column,)]=(field.remote_field.model._meta.db_table,field.target_field.column)
    found=[(tuple(c['columns']),c['foreign_key']) for c in actual.values() if c.get('foreign_key')]
    assert len(found)==len(expected_fk) and dict(found)==expected_fk, 'eval_task_reference: physical FK drift'
field=Task._meta.get_field('evals')
assert field.many_to_many and field.remote_field.model==Config and Membership._meta.auto_created==Task, 'eval_task_reference: through drift'
assert {f.name for f in Task._meta.many_to_many}=={'evals'}, 'eval_task_reference: extra task relations'
for name,choices in [('status',{'pending','running','completed','failed','paused','deleted'}),
        ('run_type',{'historical','continuous'}),('row_type',{'spans','traces','sessions','voiceCalls'})]:
    assert {value for value,label in Task._meta.get_field(name).flatchoices}==choices, 'eval_task_reference: task choice drift'
for model,name,choices in [(Project,'source',{'prototype'}),(Project,'trace_type',{'observe'}),
        (Project,'model_type',{'GenerativeLLM'}),(Template,'owner',{'user'}),
        (Template,'output_type_normalized',{'pass_fail'}),(Template,'eval_type',{'llm'}),
        (Template,'aggregation_function',{'weighted_avg'})]:
    assert choices <= {value for value,label in model._meta.get_field(name).flatchoices}, 'eval_task_reference: context choice drift'
assert Template._meta.get_field('eval_tags').base_field.get_internal_type()=='CharField' and Template._meta.get_field('eval_tags').base_field.max_length==100, 'eval_task_reference: template tags drift'
for model,name in [(Log,'eval_task_id'),(Run,'eval_task')]:
    field=model._meta.get_field(name)
    assert field.get_internal_type()==('CharField' if model==Log else 'ForeignKey'), 'eval_task_reference: work identity drift'
    if model==Run: assert field.remote_field.model==Task, 'eval_task_reference: run relation drift'
"""

PREPARE_BODY = """
user=User.objects.get(email=a['email'],organization_id=a['organization_id'],is_active=True)
workspace=Workspace.all_objects.get(id=a['workspace_id'],organization_id=a['organization_id'],deleted=False,is_active=True)
assert workspace.created_by_id==user.id, 'eval_task_reference: source account/workspace mismatch'
context=dict(user_id=str(user.id),historical_at=(workspace.created_at-timedelta(days=400)).isoformat())
batches=list(zip((Project,Template,Config),support_payloads(context)))
with transaction.atomic():
    counts=[model.all_objects.filter(pk__in=[row['id'] for row in rows]).count() for model,rows in batches]
    fresh=all(count==0 for count in counts)
    assert fresh or counts==[len(rows) for model,rows in batches], 'eval_task_reference: partial context'
    if fresh:
        for model,rows in batches:
            model.all_objects.bulk_create(objects(model,rows),batch_size=100)
            model.all_objects.filter(pk__in=[row['id'] for row in rows]).update(
                created_at=datetime.fromisoformat(context['historical_at']),updated_at=datetime.fromisoformat(context['historical_at']))
    check_context(context)
print(json.dumps(dict(observation(),context=context)))
"""

SLICE = """
context=a['context']
start,stop,scale=a['start'],a['stop'],a['scale']
assert type(scale) is int and scale>0 and 0<=start<stop<=100000*scale, 'eval_task_reference: invalid slice'
assert start%2500==0 and stop==start+2500, 'eval_task_reference: unbounded/misaligned slice'
check_context(context)
tasks,members=payloads(context,start,stop)
"""

CHUNK_BODY = SLICE + """
with transaction.atomic():
    ids=[row['id'] for row in tasks]
    counts=[Task.all_objects.filter(pk__in=ids).count(),Membership.objects.filter(evaltask_id__in=ids).count()]
    fresh=counts==[0,0]
    assert fresh or counts==[len(tasks),len(members)], 'eval_task_reference: partial atomic chunk'
    check_no_work(tasks)
    if fresh:
        batch=objects(Task,tasks)
        Task.all_objects.bulk_create(batch,batch_size=500)
        # bulk_create applies auto_now(_add); restore the varied source history
        # using normal ORM updates, without raw SQL or disabled constraints.
        for obj,row in zip(batch,tasks):
            obj.created_at,obj.updated_at=row['created_at'],row['updated_at']
        Task.all_objects.bulk_update(batch,['created_at','updated_at'],batch_size=500)
        Membership.objects.bulk_create(objects(Membership,members),batch_size=500)
    matching(Task,tasks)
    check_memberships(tasks,members)
print(json.dumps(dict(skipped=not fresh)))
"""

VERIFY_BODY = """
check_context(a['context'])
# Like the TraceSpan verifier, stream bounded batches in one 180s app operation,
# rather than booting Django for every read slice. A timeout is a failed check.
assert type(a['scale']) is int and a['scale']>0, 'eval_task_reference: invalid scale'
for start in range(0,100000*a['scale'],2500):
    tasks,members=payloads(a['context'],start,start+2500)
    matching(Task,tasks)
    check_memberships(tasks,members)
    check_no_work(tasks)
print(json.dumps(observation()))
"""
PREPARE = COMMON + SCHEMA_CHECK + PREPARE_BODY
CHUNK = COMMON + CHUNK_BODY
VERIFY = COMMON + VERIFY_BODY


def _require(producer, ok):
    if not ok:
        raise producer.ManualRelease('high_volume_reference_unavailable')


def verify_eval_task_reference(driver, *, prefix, producer):
    """Read-only, bounded full source-payload check, including after bootstrap/restore.

    Kept separate from API witnesses: ORM fixtures are not product_api witnesses.
    A pending/incomplete fixture cannot pass a preservation read.
    """
    state = getattr(driver, '_eval_task_reference', None)
    _require(producer, not hasattr(driver, '_eval_task_reference_pending'))
    if state is None:
        return
    _verify(driver, state, prefix=prefix, producer=producer)
    if getattr(driver, 'corpus', None) is not None:
        driver.corpus['eval_task_reference_preservation_reads'] = driver.corpus.get('eval_task_reference_preservation_reads', 0) + 1


def _verify(driver, state, *, prefix, producer):
    arguments = state['arguments']
    scale = (arguments['rows'] + 99999) // 100000
    plan = eval_task_reference_plan(scale)
    result = driver.app_python(prefix + VERIFY, dict(arguments, context=state['context'], scale=scale))
    _require(producer, isinstance(result, dict))
    for key, expected in (('seeded_counts', plan['counts']), ('cohorts', plan['cohorts'])):
        actual = result.get(key)
        _require(producer, isinstance(actual, dict) and set(actual) == set(expected)
            and all(type(actual[k]) is int and actual[k] == v for k, v in expected.items()))
    counts = result.get('counts')
    _require(producer, isinstance(counts, dict) and set(counts) == {'tracer_eval_task'})
    count = counts['tracer_eval_task']
    _require(producer, type(count) is int and count >= state['baseline'] + plan['counts']['tracer_eval_task'])
    _require(producer, type(result.get('memberships')) is int and result['memberships'] == plan['memberships'])
    return result


def seed_eval_task_reference(driver, rows, *, prefix, producer):
    require = lambda ok: _require(producer, ok)
    require(type(rows) is int and 100000 <= rows <= 100000000)
    scale = (rows + 99999) // 100000
    plan = eval_task_reference_plan(scale)
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
    complete = getattr(driver, '_eval_task_reference', None)
    state = complete or getattr(driver, '_eval_task_reference_pending', None)
    if state is not None:
        require(state['arguments'] == arguments)
    else:
        prepared = driver.app_python(prefix + PREPARE, arguments)
        require(isinstance(prepared, dict) and isinstance(prepared.get('context'), dict))
        before, seeded = prepared.get('counts'), prepared.get('seeded_counts')
        require(isinstance(before, dict) and isinstance(seeded, dict)
            and set(before) == set(seeded) == {'tracer_eval_task'})
        before, seeded = before['tracer_eval_task'], seeded['tracer_eval_task']
        require(type(before) is int and type(seeded) is int and 0 <= seeded <= plan['counts']['tracer_eval_task'] and before >= seeded)
        state = dict(arguments=arguments, context=prepared['context'], baseline=before-seeded)
        driver._eval_task_reference_pending = state
    seconds = []
    if complete is None:
        for start in range(0, plan['counts']['tracer_eval_task'], CHUNK_TASKS):
            began = time.monotonic()
            result = driver.app_python(prefix + CHUNK, dict(arguments, context=state['context'],
                start=start, stop=start + CHUNK_TASKS, scale=scale))
            seconds.append(time.monotonic() - began)
            require(isinstance(result, dict) and type(result.get('skipped')) is bool)
    verified = _verify(driver, state, prefix=prefix, producer=producer)
    if complete is not None:
        return
    if getattr(driver, 'corpus', None) is not None:
        driver.corpus.setdefault('reference_seed_operations', []).append(dict(
            table='tracer_eval_task', operation='eval_task_reference_fixture', version=1,
            requested_rows=rows, scale=scale, chunks=len(seconds), chunk_seconds=seconds,
            counts=verified['counts'], cohorts=verified['cohorts'], memberships=verified['memberships']))
    driver._eval_task_reference = state
    del driver._eval_task_reference_pending
