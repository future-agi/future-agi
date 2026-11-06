"""Pure/fake qualification regressions; not PostgreSQL, timing or native evidence."""
import ast
from collections import Counter
from contextlib import contextmanager, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_shipping as s
from scripts import update_rehearsal_qualification as q
from scripts import update_rehearsal_eval_task_reference as t
from deploy.tests.test_update_trace_reference import Query as TraceQuery


TABLE = 'tracer_eval_task'
ENTRY = next(row for row in p.load_high_volume_registry()
             if row['database']=='postgres' and row['table']==TABLE)
ARGS = dict(namespace='task-reference-source', email='task-reference@example.test', rows=100000,
    organization_id=str(uuid.UUID(int=1)), workspace_id=str(uuid.UUID(int=2)))
CONTEXT = dict(user_id=str(uuid.UUID(int=3)), historical_at='2025-01-01T00:00:00+00:00')


class FakeApp:
    def __init__(self, scale=1):
        self.scale, self.baseline = scale, 11
        self.calls, self.committed, self.skipped = [], set(), []
        self.fail_at = None
        self.alter_verified = self.alter_prepared = lambda value: value

    def __call__(self, program, args):
        self.calls.append((program, copy.deepcopy(args)))
        if program==s.DJANGO_PREFIX+t.PREPARE:
            seeded=len(self.committed)*2500
            return self.alter_prepared(dict(context=copy.deepcopy(CONTEXT),
                counts={TABLE:self.baseline+seeded},seeded_counts={TABLE:seeded}))
        if program==s.DJANGO_PREFIX+t.CHUNK:
            assert args['stop']==args['start']+2500 and args['scale']==self.scale
            if self.fail_at==args['start']:
                self.fail_at=None
                raise RuntimeError('synthetic interruption')
            skipped=args['start'] in self.committed
            if skipped: self.skipped.append(args['start'])
            self.committed.add(args['start'])
            return dict(skipped=skipped)
        if program==s.DJANGO_PREFIX+t.VERIFY:
            assert self.committed==set(range(0,100000*self.scale,2500)), 'premature verify'
            plan=t.eval_task_reference_plan(self.scale)
            return self.alter_verified(dict(counts={TABLE:self.baseline+100000*self.scale},
                seeded_counts=plan['counts'],cohorts=plan['cohorts'],memberships=plan['memberships']))
        raise AssertionError('unexpected app operation')


def driver_for(fake=None, cls=s.ShippingRehearsal):
    driver=object.__new__(cls)
    driver.witnesses=SimpleNamespace(namespaces={'source':dict(namespace=ARGS['namespace'],
        account={'email':ARGS['email'],'password':'DO-NOT-FORWARD'}, headers={
            'X-Organization-Id':ARGS['organization_id'],'X-Workspace-Id':ARGS['workspace_id'],
            'Authorization':'Bearer SECRET','X-Api-Key':'SECRET'})})
    driver.app_python=fake if fake is not None else FakeApp()
    driver.corpus={}
    return driver


def payload_env():
    env={'a':copy.deepcopy(ARGS)}
    exec(t.ROWS,env)
    return env


class Query(TraceQuery):
    # TraceQuery uses an 'exists' attribute internally; these fixtures need the
    # ORM's exists() method instead. No Exists expressions are used here.
    def __init__(self, model, conditions=(), exists=()):
        self.model,self.conditions=model,conditions

    def filter(self, **kwargs):
        return Query(self.model,self.conditions+(kwargs,))

    def rows(self, outer=None):
        pks=next((c['pk__in'] for c in self.conditions if 'pk__in' in c),None)
        source=self.model.records.values() if pks is None else (
            self.model.records[pk] for pk in pks if pk in self.model.records)
        for row in source:
            if all(self.matches(row,condition,outer) for condition in self.conditions): yield row

    def exists(self): return any(True for row in self.rows())

    def bulk_create(self, objects, batch_size):
        assert batch_size in (100,500)
        if self.model.fail_insert: raise RuntimeError('synthetic insert failure')
        for obj in objects:
            if not hasattr(obj,'id'): obj.id=len(self.model.records)+1
            if obj.id in self.model.records: raise AssertionError('duplicate PK')
            if self.model.__name__!='EvalTask_evals':
                obj.created_at=obj.updated_at=datetime.now(timezone.utc)
            self.model.records[obj.id]=copy.deepcopy(vars(obj))

    def bulk_update(self, objects, fields, batch_size):
        assert fields==['created_at','updated_at'] and batch_size==500
        for obj in objects:
            self.model.records[obj.id].update({key:getattr(obj,key) for key in fields})


def orm_env():
    env=payload_env()
    for alias,name in [('Project','Project'),('Template','EvalTemplate'),('Config','CustomEvalConfig'),
            ('Task','EvalTask'),('Membership','EvalTask_evals'),('Log','EvalLogger'),('Run','EvalTaskLogger')]:
        def init(self, **kwargs): self.__dict__.update(copy.deepcopy(kwargs))
        model=type(name,(),{'records':{},'fail_insert':False,'__init__':init,'pk':property(lambda obj:obj.id)})
        fields={key:SimpleNamespace(attname=key+('_id' if spec[3] else ''))
                for key,spec in t.SOURCE_FIELDS.get(name,{}).items()}
        model._meta=SimpleNamespace(object_name=name,get_field=fields.__getitem__)
        model.all_objects=model.objects=Query(model)
        env[alias]=model
    for model,rows in zip((env['Project'],env['Template'],env['Config']),env['support_payloads'](CONTEXT)):
        for row in rows: model.records[row['id']]=copy.deepcopy(row)
    @contextmanager
    def atomic():
        models=[env[key] for key in ('Project','Template','Config','Task','Membership','Log','Run')]
        before=[copy.deepcopy(model.records) for model in models]
        try: yield
        except BaseException:
            for model,records in zip(models,before): model.records=records
            raise
    functions=ast.Module(body=[node for node in ast.parse(t.COMMON).body
        if isinstance(node,ast.FunctionDef)],type_ignores=[])
    exec(compile(functions,'<fixture-common>','exec'),env)
    env.update(json=json,transaction=SimpleNamespace(atomic=atomic))
    env['a'].update(context=copy.deepcopy(CONTEXT),start=0,stop=2500,scale=1)
    return env


def execute(env, source):
    with redirect_stdout(io.StringIO()) as output: exec(source,env)
    return json.loads(output.getvalue()) if output.getvalue() else None


def schema_env():
    """Contract double, not an observation of PostgreSQL or installed source."""
    class Unique:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
        def __eq__(self, other): return type(other) is Unique and vars(self)==vars(other)
    cascade=object()
    env={'SOURCE_FIELDS':copy.deepcopy(t.SOURCE_FIELDS),
        'models':SimpleNamespace(UniqueConstraint=Unique,CASCADE=cascade),'Q':lambda **kw:kw}
    labels={'Project':('tracer.Project','tracer_project'),
        'Template':('model_hub.EvalTemplate','model_hub_evaltemplate'),
        'Config':('tracer.CustomEvalConfig','tracer_custom_eval_config'),
        'Task':('tracer.EvalTask',TABLE),'Membership':('tracer.EvalTask_evals','tracer_eval_task_evals')}
    targets={}
    def target(label):
        if label not in targets:
            targets[label]=type(label.split('.')[-1],(),{'_meta':SimpleNamespace(label=label,
                db_table='context_'+label,pk=SimpleNamespace(name='id',column='id',get_internal_type=lambda:'UUIDField'))})
        return targets[label]
    for alias,(label,table) in labels.items(): env[alias]=target(label)
    columns,constraints={},{}
    for alias,(label,table) in labels.items():
        model=env[alias]
        fields={}
        for name,(kind,nullable,length,remote) in t.SOURCE_FIELDS[model.__name__].items():
            field=SimpleNamespace(name=name,column=name+('_id' if remote else ''),null=nullable,
                max_length=length,get_internal_type=lambda kind=kind:kind,
                unique=name in ('id','simulation_agent'),primary_key=name=='id',
                many_to_one=kind=='ForeignKey',one_to_one=kind=='OneToOneField',db_constraint=True,flatchoices=[])
            if remote: field.remote_field=SimpleNamespace(model=target(remote),on_delete=cascade)
            fields[name]=field
        unique=[]
        if alias=='Project': unique=[Unique(fields=['name','trace_type','organization','workspace'],
            condition={'deleted':False},name='unique_project_per_org_type')]
        if alias=='Config': unique=[Unique(fields=['name','project'],condition={'deleted':False},name='unique_name_project_idx')]
        model._meta=SimpleNamespace(object_name=model.__name__,label=label,db_table=table,managed=True,
            concrete_fields=list(fields.values()),pk=fields['id'],get_field=fields.__getitem__,constraints=unique,
            unique_together=[('evaltask','customevalconfig')] if alias=='Membership' else [],many_to_many=[])
        columns[table]=[SimpleNamespace(name=f.column,null_ok=f.null) for f in fields.values()]
    for alias,(label,table) in labels.items():
        model=env[alias]
        actual={'pk':dict(primary_key=True,unique=True,columns=['id'])}
        for field in model._meta.concrete_fields:
            if field.many_to_one or field.one_to_one:
                field.target_field=field.remote_field.model._meta.pk
                actual['fk_'+field.column]=dict(columns=[field.column],foreign_key=(
                    field.remote_field.model._meta.db_table,field.target_field.column))
            if field.unique and not field.primary_key: actual['unique_'+field.column]=dict(unique=True,columns=[field.column])
        for constraint in model._meta.constraints:
            actual[constraint.name]=dict(unique=True,columns=[model._meta.get_field(f).column for f in constraint.fields])
        if alias=='Membership': actual['pair_unique']=dict(unique=True,columns=['evaltask_id','customevalconfig_id'])
        constraints[table]=actual
    task_fields=env['Task']._meta.get_field.__self__
    task_fields['evals']=SimpleNamespace(name='evals',many_to_many=True,
        remote_field=SimpleNamespace(model=env['Config'],through=env['Membership']))
    env['Task']._meta.many_to_many=[task_fields['evals']]
    env['Membership']._meta.auto_created=env['Task']
    for alias,name,values in [('Task','status',['pending','running','completed','failed','paused','deleted']),
            ('Task','run_type',['historical','continuous']),('Task','row_type',['spans','traces','sessions','voiceCalls']),
            ('Project','source',['prototype']),('Project','trace_type',['observe']),('Project','model_type',['GenerativeLLM']),
            ('Template','owner',['user']),('Template','output_type_normalized',['pass_fail']),
            ('Template','eval_type',['llm']),('Template','aggregation_function',['weighted_avg'])]:
        env[alias]._meta.get_field(name).flatchoices=[(v,v) for v in values]
    env['Template']._meta.get_field('eval_tags').base_field=SimpleNamespace(get_internal_type=lambda:'CharField',max_length=100)
    env['Log']=SimpleNamespace(_meta=SimpleNamespace(get_field=lambda name:SimpleNamespace(get_internal_type=lambda:'CharField')))
    env['Run']=SimpleNamespace(_meta=SimpleNamespace(get_field=lambda name:SimpleNamespace(
        get_internal_type=lambda:'ForeignKey',remote_field=SimpleNamespace(model=env['Task']))))
    env['connection']=SimpleNamespace(vendor='postgresql',cursor=mock.MagicMock(),introspection=SimpleNamespace(
        get_table_description=lambda cursor,table:columns[table],get_constraints=lambda cursor,table:constraints[table]))
    return env,columns,constraints


class EvalTaskReferenceTests(unittest.TestCase):
    def test_exact_dispatch_and_inheritance(self):
        self.assertEqual(t.EVAL_TASK_REFERENCE_MODELS,{('tracer','EvalTask'):TABLE})
        for cls in (s.ShippingRehearsal,q.QualificationRehearsal):
            driver=driver_for(cls=cls)
            driver.seed_eval_task_reference=mock.Mock()
            driver.app_python=mock.Mock()
            driver.seed_reference_table(ENTRY,100000)
            driver.seed_eval_task_reference.assert_called_once_with(100000)
            for key,value in [('database','clickhouse'),('database','sqlite'),('app_label','other'),
                    ('model','Other'),('table','public.'+TABLE),('table','tracer_eval_logger'),('table','usage_api_call_log')]:
                with self.subTest(key=key,value=value),self.assertRaises(p.ManualRelease):
                    driver.seed_reference_table(dict(ENTRY,**{key:value}),100000)
            with self.assertRaises(p.ManualRelease):
                driver.seed_reference_table(dict(ENTRY,app_label='usage',model='APICallLog'),100000)
            driver.app_python.assert_not_called()

    def test_full_plan_geometry_relations_and_nonrunnable_payloads(self):
        env=payload_env()
        projects,templates,configs=env['support_payloads'](CONTEXT)
        self.assertEqual(tuple(map(len,(projects,templates,configs))),(3,2,6))
        config_by_id={row['id']:row for row in configs}
        counts=Counter()
        membership_count=0
        seen=set()
        for start in range(0,100000,2500):
            tasks,members=env['payloads'](CONTEXT,start,start+2500)
            task_by_id={row['id']:row for row in tasks}
            self.assertEqual(len(task_by_id),2500)
            self.assertFalse(seen & set(task_by_id))
            seen.update(task_by_id)
            counts.update(row['status'] for row in tasks)
            membership_count+=len(members)
            for row in tasks:
                self.assertNotIn(row['status'],('pending','running'))
                self.assertEqual(row['deleted'],row['status']=='deleted')
                self.assertEqual(row['continuous_cursor'] is not None,row['status']=='paused')
                self.assertEqual(row['run_type'],'continuous' if row['status']=='paused' else 'historical')
                self.assertLess(row['start_time'],row['created_at'])
                self.assertLessEqual(row['created_at'],row['updated_at'])
                self.assertTrue(0<row['sampling_rate']<=100)
                self.assertEqual(set(row),{key+('_id' if spec[3] else '') for key,spec in t.SOURCE_FIELDS['EvalTask'].items()})
            for pair in members:
                self.assertEqual(task_by_id[pair['evaltask_id']]['project_id'],
                    config_by_id[pair['customevalconfig_id']]['project_id'])
        self.assertEqual(dict(counts),t.COHORTS)
        self.assertEqual(membership_count,150000)
        for scale in (1,2,1000):
            self.assertEqual(t.eval_task_reference_plan(scale),dict(counts={TABLE:100000*scale},
                cohorts={k:v*scale for k,v in t.COHORTS.items()},memberships=150000*scale))
        self.assertEqual(env['payloads'](CONTEXT,0,10),env['payloads'](CONTEXT,0,10))
        other=payload_env(); other['a']['namespace']='other'; exec(t.ROWS,other)
        self.assertNotEqual(env['identity']('task',0),other['identity']('task',0))
        for model,rows in zip(('Project','EvalTemplate','CustomEvalConfig'),(projects,templates,configs)):
            self.assertTrue(all(set(row)=={key+('_id' if spec[3] else '')
                for key,spec in t.SOURCE_FIELDS[model].items()} for row in rows))
        self.assertNotIn('analysis_source_generation',t.SOURCE_FIELDS['Project'])

    def test_both_pinned_source_objects_match_inventory_and_nonrunnable_contract(self):
        # Unit CI fetches full tag history. Read source as AST only; never import
        # historical application code or infer its inventory from the target.
        root=Path(__file__).resolve().parents[2]
        def tree(ref,path):
            return ast.parse(subprocess.check_output(['git','-C',str(root),'show',ref+':futureagi/'+path],text=True))
        def cls(tree,name):
            return next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
        labels={'Project':'tracer.Project','CustomEvalConfig':'tracer.CustomEvalConfig',
            'EvalTemplate':'model_hub.EvalTemplate','Organization':'accounts.Organization',
            'Workspace':'accounts.Workspace','User':'accounts.User',
            'KnowledgeBaseFile':'model_hub.KnowledgeBaseFile','EvalGroup':'model_hub.EvalGroup'}
        revisions=('6840e44914cf7c85cd0ae65d98761f7728b8afcc','28e729aab169d75a2ca856fb4ed67fd1dda5901a')
        paths={'EvalTask':'tracer/models/eval_task.py','Project':'tracer/models/project.py',
            'CustomEvalConfig':'tracer/models/custom_eval_config.py','EvalTemplate':'model_hub/models/evals_metric.py'}
        selection=[]
        for revision in revisions:
            base=cls(tree(revision,'tfc/utils/base_model.py'),'BaseModel')
            for name,path in paths.items():
                declarations=base.body+cls(tree(revision,path),name).body
                actual={}
                for node in declarations:
                    if not isinstance(node,ast.Assign) or not isinstance(node.value,ast.Call): continue
                    call=node.value
                    if isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id=='models': kind=call.func.attr
                    elif isinstance(call.func,ast.Name) and call.func.id=='ArrayField': kind='ArrayField'
                    else: continue
                    if kind in ('ManyToManyField','Manager'): continue
                    options={kw.arg:ast.literal_eval(kw.value) for kw in call.keywords if isinstance(kw.value,ast.Constant)}
                    remote=None
                    if kind in ('ForeignKey','OneToOneField'):
                        remote=labels[call.args[0].id] if isinstance(call.args[0],ast.Name) else ast.literal_eval(call.args[0])
                    actual[node.targets[0].id]=(kind,options.get('null',False),
                        32 if kind=='UUIDField' else options.get('max_length'),remote)
                self.assertEqual(actual,t.SOURCE_FIELDS[name],(revision,name))
            stranded=tree(revision,'tracer/selectors/eval_tasks/stranded.py')
            sweepable=next(n.value for n in stranded.body if isinstance(n,ast.Assign)
                and any(isinstance(v,ast.Name) and v.id=='_SWEEPABLE' for v in n.targets))
            self.assertEqual([n.attr for n in sweepable.elts],['RUNNING','PENDING'])
            select=next(n for n in stranded.body if isinstance(n,ast.FunctionDef) and n.name=='find_stranded_tasks')
            self.assertIn('eval_task_id__in',ast.unparse(select))
            self.assertIn('status__in=_UNDRAINED',ast.unparse(select))
            activities=tree(revision,'tfc/temporal/eval_tasks/activities.py')
            state=next(n for n in activities.body if isinstance(n,ast.FunctionDef) and n.name=='_get_task_state_sync')
            inactive=next(n.value for n in ast.walk(state) if isinstance(n,ast.Assign)
                and any(isinstance(v,ast.Name) and v.id=='inactive' for v in n.targets))
            self.assertEqual({n.attr for n in inactive.elts},{'PAUSED','DELETED','FAILED'})
            selection.append((ast.dump(select),ast.dump(state)))
        self.assertEqual(selection[0],selection[1])

    def test_schema_fail_closed_before_prepare(self):
        env,columns,constraints=schema_env()
        exec(t.SCHEMA_CHECK,env)
        cases=[('engine',lambda e,c,k:setattr(e['connection'],'vendor','sqlite')),
            ('column',lambda e,c,k:c[TABLE].pop()),
            ('nullability',lambda e,c,k:setattr(c[TABLE][0],'null_ok',True)),
            ('pk',lambda e,c,k:k[TABLE].pop('pk')),
            ('fk',lambda e,c,k:k[TABLE].pop('fk_project_id')),
            ('pair uniqueness',lambda e,c,k:k['tracer_eval_task_evals'].pop('pair_unique')),
            ('extra check',lambda e,c,k:k[TABLE].update(extra={'check':True})),
            ('wrong relation',lambda e,c,k:setattr(e['Task']._meta.get_field('project'),'db_constraint',False)),
            ('wrong type',lambda e,c,k:setattr(e['Task']._meta.get_field('sampling_rate'),'get_internal_type',lambda:'IntegerField')),
            ('wrong choices',lambda e,c,k:setattr(e['Task']._meta.get_field('status'),'flatchoices',[('pending','Pending')])),
            ('custom through',lambda e,c,k:setattr(e['Membership']._meta,'auto_created',False))]
        for name,change in cases:
            env,columns,constraints=schema_env(); change(env,columns,constraints)
            with self.subTest(name=name),self.assertRaises(AssertionError): exec(t.SCHEMA_CHECK,env)
        self.assertLess(t.PREPARE.index('PostgreSQL required'),t.PREPARE.index('with transaction.atomic():'))

    def test_actual_chunks_replay_and_refuse_partial_payload_membership_or_work_conflicts(self):
        for start in (0,50000,70000,90000):
            env=orm_env(); env['a'].update(start=start,stop=start+2500)
            self.assertEqual(execute(env,t.CHUNK_BODY),{'skipped':False})
            before=copy.deepcopy(env['Task'].records)
            self.assertEqual(execute(env,t.CHUNK_BODY),{'skipped':True})
            self.assertEqual(env['Task'].records,before)
            for change in ('payload','missing task','missing membership','wrong membership','entry','run'):
                task_before=copy.deepcopy(env['Task'].records)
                member_before=copy.deepcopy(env['Membership'].records)
                task_id=next(iter(env['Task'].records))
                if change=='payload': env['Task'].records[task_id]['sampling_rate']=99.5
                if change=='missing task': del env['Task'].records[task_id]
                if change=='missing membership': env['Membership'].records.pop(next(iter(env['Membership'].records)))
                if change=='wrong membership': next(iter(env['Membership'].records.values()))['customevalconfig_id']=uuid.UUID(int=99)
                if change=='entry': env['Log'].records[1]={'id':1,'eval_task_id':str(task_id)}
                if change=='run': env['Run'].records[1]={'id':1,'eval_task_id':task_id}
                with self.subTest(start=start,change=change),self.assertRaises(AssertionError): execute(env,t.CHUNK_BODY)
                env['Task'].records,env['Membership'].records=task_before,member_before
                env['Log'].records.clear(); env['Run'].records.clear()

    def test_actual_prepare_replays_exact_context_and_refuses_partial_or_changed_context(self):
        env=orm_env()
        for name in ('Project','Template','Config'): env[name].records.clear()
        user=SimpleNamespace(id=uuid.UUID(CONTEXT['user_id']))
        workspace=SimpleNamespace(created_by_id=user.id,
            created_at=datetime.fromisoformat(CONTEXT['historical_at'])+timedelta(days=400))
        env['User']=SimpleNamespace(objects=mock.Mock())
        env['User'].objects.get.return_value=user
        env['Workspace']=SimpleNamespace(all_objects=mock.Mock())
        env['Workspace'].all_objects.get.return_value=workspace
        env['observation']=lambda:dict(counts={TABLE:11},seeded_counts={TABLE:0})
        result=execute(env,t.PREPARE_BODY)
        self.assertEqual(result['context'],CONTEXT)
        self.assertEqual([len(env[name].records) for name in ('Project','Template','Config')],[3,2,6])
        self.assertEqual(execute(env,t.PREPARE_BODY),result)
        env['a']['rows']=100001
        with self.assertRaisesRegex(AssertionError,'incompatible existing Project'): execute(env,t.PREPARE_BODY)
        env['a']['rows']=100000
        env['Config'].records.pop(next(iter(env['Config'].records)))
        with self.assertRaisesRegex(AssertionError,'partial context'): execute(env,t.PREPARE_BODY)
        self.assertEqual(len(env['Config'].records),5)
        workspace.created_by_id=uuid.UUID(int=99)
        with self.assertRaisesRegex(AssertionError,'account/workspace mismatch'): execute(env,t.PREPARE_BODY)

    def test_atomic_chunk_failure_does_not_leave_tasks_without_memberships(self):
        env=orm_env(); env['Membership'].fail_insert=True
        with self.assertRaises(RuntimeError): execute(env,t.CHUNK_BODY)
        self.assertFalse(env['Task'].records)
        self.assertFalse(env['Membership'].records)
        env['Membership'].fail_insert=False
        self.assertEqual(execute(env,t.CHUNK_BODY),{'skipped':False})
        for start,stop in ((1,2501),(0,5000),(0,1000),(100000,102500)):
            env['a'].update(start=start,stop=stop)
            with self.assertRaises(AssertionError): execute(env,t.CHUNK_BODY)

    def test_full_seed_precedes_authoritative_count_and_bytes_measurement(self):
        fake=FakeApp(); driver=driver_for(fake,cls=q.QualificationRehearsal)
        def measure(entry):
            self.assertEqual(fake.calls[-1][0],s.DJANGO_PREFIX+t.VERIFY)
            self.assertEqual(len(fake.committed),40)
            self.assertTrue(hasattr(driver,'_eval_task_reference'))
            return dict(engine='postgres',table='public.'+TABLE,rows=123456,bytes=7654321)
        result=p.seed_reference_tables([ENTRY],driver.seed_reference_table,measure)
        self.assertEqual((result[0]['rows'],result[0]['bytes']),(123456,7654321))
        self.assertEqual([program for program,args in fake.calls],
            [s.DJANGO_PREFIX+t.PREPARE]+[s.DJANGO_PREFIX+t.CHUNK]*40+[s.DJANGO_PREFIX+t.VERIFY])
        self.assertEqual(fake.calls[0][1],ARGS)
        for secret in ('Authorization','SECRET','password','DO-NOT-FORWARD'):
            self.assertNotIn(secret,json.dumps([args for program,args in fake.calls]))
        for fragment in ('pg_total_relation_size','SELECT count(*)'):
            self.assertIn(fragment,Path(s.__file__).read_text())

    def test_interruption_resumes_same_and_fresh_driver_without_baseline_inflation(self):
        for fresh in (False,True):
            fake=FakeApp(); fake.fail_at=5000; driver=driver_for(fake)
            with self.assertRaises(RuntimeError): driver.seed_eval_task_reference(100000)
            self.assertEqual(fake.committed,{0,2500})
            if fresh: driver=driver_for(fake)
            driver.seed_eval_task_reference(100000)
            self.assertEqual(driver._eval_task_reference['baseline'],11)
            self.assertEqual(fake.skipped,[0,2500])
            self.assertEqual(len(driver.corpus['reference_seed_operations']),1)
            before=len(fake.calls); driver.seed_eval_task_reference(100000)
            self.assertEqual(len(fake.calls),before+1)
            with self.assertRaises(p.ManualRelease): driver.seed_eval_task_reference(100001)

    def test_invalid_plan_counts_and_missing_context_refuse_without_measurement(self):
        for rows in (True,99999,0,100000001,100000.0):
            driver=driver_for()
            with self.assertRaises(p.ManualRelease): driver.seed_eval_task_reference(rows)
            self.assertFalse(driver.app_python.calls)
        for edit in (lambda r:dict(r,seeded_counts={TABLE:99999}),lambda r:dict(r,counts={TABLE:100000}),
                lambda r:dict(r,memberships=149999),lambda r:dict(r,cohorts=dict(t.COHORTS,failed=True))):
            fake=FakeApp(); fake.alter_verified=edit; driver=driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_eval_task_reference(100000)
            self.assertFalse(hasattr(driver,'_eval_task_reference'))
            self.assertFalse(driver.corpus)
        driver=driver_for(); del driver.witnesses.namespaces['source']['account']
        with self.assertRaises(p.ManualRelease): driver.seed_eval_task_reference(100000)
        self.assertFalse(driver.app_python.calls)
        fake=FakeApp(scale=2); driver=driver_for(fake); driver.seed_eval_task_reference(100001)
        self.assertEqual(len(fake.committed),80)

    def test_retained_verification_is_read_only_and_not_a_product_api_witness(self):
        fake=FakeApp(); driver=driver_for(fake)
        t.verify_eval_task_reference(driver,prefix=s.DJANGO_PREFIX,producer=p)
        self.assertFalse(fake.calls)
        driver.seed_eval_task_reference(100000)
        before=len(fake.calls)
        for phase in ('before backup','after bootstrap','after restore'):
            t.verify_eval_task_reference(driver,prefix=s.DJANGO_PREFIX,producer=p)
        self.assertEqual([program for program,args in fake.calls[before:]],[s.DJANGO_PREFIX+t.VERIFY]*3)
        self.assertEqual(driver.corpus['eval_task_reference_preservation_reads'],3)
        fake.alter_verified=lambda r:dict(r,memberships=150001)
        with self.assertRaises(p.ManualRelease): t.verify_eval_task_reference(driver,prefix=s.DJANGO_PREFIX,producer=p)
        self.assertEqual(driver.corpus['eval_task_reference_preservation_reads'],3)
        source=Path(s.__file__).read_text()
        self.assertIn("if label == 'source':",source)
        self.assertIn('verify_eval_task_reference(self.d, prefix=DJANGO_PREFIX, producer=p)',source)
        driver._eval_task_reference_pending={}
        with self.assertRaises(p.ManualRelease): t.verify_eval_task_reference(driver,prefix=s.DJANGO_PREFIX,producer=p)

    def test_source_witness_read_enforces_retained_check_before_api_read(self):
        witness=object.__new__(s.ProductWitnesses)
        witness.d=driver_for()
        namespace=dict(headers={},account={'email':ARGS['email']})
        witness.namespaces={'source':namespace,'target':namespace}
        witness._http=mock.Mock(side_effect=RuntimeError('stop at API boundary'))
        refusal=p.ManualRelease('high_volume_reference_unavailable')
        with mock.patch.object(s,'verify_eval_task_reference',side_effect=refusal) as verify:
            with self.assertRaises(p.ManualRelease) as raised: witness.read('source')
            self.assertIs(raised.exception,refusal)
            verify.assert_called_once_with(witness.d,prefix=s.DJANGO_PREFIX,producer=p)
            witness._http.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'API boundary'): witness.read('target')
            self.assertEqual(verify.call_count,1)
        with mock.patch.object(s,'verify_eval_task_reference') as verify:
            with self.assertRaisesRegex(RuntimeError,'API boundary'): witness.read('source')
            verify.assert_called_once_with(witness.d,prefix=s.DJANGO_PREFIX,producer=p)

    def test_verifier_visits_every_bounded_chunk_and_detects_payload_loss(self):
        env=orm_env()
        env['check_context']=mock.Mock()
        env['matching']=mock.Mock()
        env['check_memberships']=mock.Mock()
        env['check_no_work']=mock.Mock()
        env['payloads']=mock.Mock(side_effect=lambda context,start,stop:([dict(id=start)],[dict(id=stop)]))
        env['observation']=lambda:{'observed':True}
        self.assertEqual(execute(env,t.VERIFY_BODY),{'observed':True})
        self.assertEqual([call.args[1:] for call in env['payloads'].call_args_list],
            [(start,start+2500) for start in range(0,100000,2500)])
        self.assertEqual(env['matching'].call_count,40)
        self.assertEqual(env['check_memberships'].call_count,40)
        self.assertEqual(env['check_no_work'].call_count,40)
        env=orm_env(); execute(env,t.CHUNK_BODY)
        tasks,members=env['payloads'](CONTEXT,0,2500)
        env['matching'](env['Task'],tasks)
        next(iter(env['Task'].records.values()))['filters']={}
        with self.assertRaisesRegex(AssertionError,'incompatible existing'): env['matching'](env['Task'],tasks)

    def test_programs_are_source_only_and_no_dispatch_or_sql_mutation(self):
        for program in (t.PREPARE,t.CHUNK,t.VERIFY):
            compile(s.DJANGO_PREFIX+program,'<task-reference>','exec')
            tree=ast.parse(program)
            self.assertFalse(any(isinstance(n,ast.keyword) and n.arg=='ignore_conflicts' for n in ast.walk(tree)))
            self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in
                ('delete','schema_editor','migrate','delay','apply_async') for n in ast.walk(tree)))
            for forbidden in ('ANALYZE','pg_stat','start_eval_task_workflow','InstantEval','analysis_source_generation'):
                self.assertNotIn(forbidden,program)
        tree=ast.parse(t.VERIFY)
        self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in ('bulk_create','bulk_update','update','save') for n in ast.walk(tree)))


if __name__=='__main__': unittest.main()
