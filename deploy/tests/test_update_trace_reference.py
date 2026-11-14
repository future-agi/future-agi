"""Stdlib orchestration and executable fixture-logic tests, NOT qualification.

FakeApp models committed boundaries/count observations. The small ORM double
executes the actual fixed payload/chunk/predicate programs (no Django or SQL on
the host); native CI must establish schema, SQL, timing and 0030's actual effect.
"""
import ast
from collections import Counter
from contextlib import contextmanager, redirect_stdout
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_shipping as s
from scripts import update_rehearsal_qualification as q
from scripts import update_rehearsal_trace_reference as t


TABLES = tuple(t.TRACE_REFERENCE_MODELS.values())
ENTRIES = tuple(row for row in p.load_high_volume_registry()
                if row['database'] == 'postgres' and row['table'] in TABLES)
ARGS = dict(namespace='reference-source', email='reference-source@example.test', rows=100000,
    organization_id=str(uuid.UUID(int=1)), workspace_id=str(uuid.UUID(int=2)))
CONTEXT = dict(user_id=str(uuid.UUID(int=3)), historical_at='2026-01-01T00:00:00+00:00')


def driver_for(fake=None, cls=s.ShippingRehearsal):
    driver = object.__new__(cls)
    driver.witnesses = SimpleNamespace(namespaces={'source':dict(namespace=ARGS['namespace'],
        account={'email':ARGS['email'], 'password':'DO-NOT-FORWARD'}, headers={
            'X-Organization-Id':ARGS['organization_id'], 'X-Workspace-Id':ARGS['workspace_id'],
            'Authorization':'Bearer SECRET', 'X-Api-Key':'SECRET'})})
    driver.app_python = fake if fake is not None else FakeApp()
    driver.corpus = {}
    return driver


class FakeApp:
    def __init__(self, scale=1):
        self.scale = scale
        self.plan = t.trace_reference_plan(scale)
        self.baseline = dict(zip(TABLES, (7, 13)))
        self.committed = set()
        self.calls, self.skipped = [], []
        self.fail_at = None
        self.alter_verified = self.alter_prepared = lambda value: value

    def partial_counts(self):
        # Abstract committed-boundary accounting, NOT simulated physical data.
        traces = len(self.committed) * t.CHUNK_TRACES
        spans = sum(0 if start % 100000 >= 95000 else 2*t.CHUNK_TRACES for start in self.committed)
        return dict(zip(TABLES, (traces, spans)))

    def __call__(self, program, args):
        self.calls.append((program, copy.deepcopy(args)))
        if program == s.DJANGO_PREFIX+t.PREPARE:
            seeded = self.partial_counts()
            return self.alter_prepared(dict(context=copy.deepcopy(CONTEXT), seeded_counts=seeded,
                counts={table:self.baseline[table]+seeded[table] for table in TABLES}))
        if program == s.DJANGO_PREFIX+t.CHUNK:
            assert args['scale'] == self.scale
            assert args['stop'] == args['start']+t.CHUNK_TRACES
            assert args['context'] == CONTEXT
            if args['start'] == self.fail_at:
                self.fail_at = None
                raise RuntimeError('synthetic chunk failure')
            skipped = args['start'] in self.committed
            if skipped: self.skipped.append(args['start'])
            self.committed.add(args['start'])
            return {'skipped':skipped}
        if program == s.DJANGO_PREFIX+t.VERIFY:
            assert self.committed == set(range(0, 100000*self.scale, t.CHUNK_TRACES)), 'premature verify'
            return self.alter_verified(dict(counts={table:self.baseline[table]+count
                for table,count in self.plan['counts'].items()}, seeded_counts=copy.deepcopy(self.plan['counts']),
                cohorts=copy.deepcopy(self.plan['cohorts'])))
        raise AssertionError('unexpected source operation')


def payload_env():
    env = {'a':copy.deepcopy(ARGS)}
    exec(t.ROWS, env)
    return env


class OuterRef:
    def __init__(self, name): self.name = name


class Exists:
    def __init__(self, query): self.query = query


class Query:
    """Only the operations used by these fixed programs; not a SQL emulator."""
    def __init__(self, model, conditions=(), exists=()):
        self.model, self.conditions, self.exists = model, conditions, exists

    def filter(self, *args, **kwargs):
        return Query(self.model, self.conditions+(kwargs,), self.exists+args)

    def rows(self, outer=None):
        # PK lookup keeps replay checks bounded, as a real PK index would.
        pks = next((c['pk__in'] for c in self.conditions if 'pk__in' in c), None)
        source = self.model.records.values() if pks is None else (
            self.model.records[pk] for pk in pks if pk in self.model.records)
        for row in source:
            if all(self.matches(row, condition, outer) for condition in self.conditions) and all(
                    any(expr.query.rows(outer=row)) for expr in self.exists):
                yield row

    def matches(self, row, condition, outer):
        for key, expected in condition.items():
            if isinstance(expected, OuterRef): expected = outer['id']
            parts = key.split('__')
            op = parts.pop() if parts[-1] in ('in','isnull') else 'eq'
            field = parts[0]
            if field == 'pk': field = 'id'
            if len(parts) == 2:
                assert field == 'project'
                value = self.model.project_model.records[row['project_id']][parts[1]]
            else:
                value = row[field]
            if isinstance(value,uuid.UUID) and isinstance(expected,str): expected=uuid.UUID(expected)
            if op == 'isnull':
                if (value is None) != expected: return False
            elif op == 'in':
                if value not in expected: return False
            elif value != expected:
                return False
        return True

    def count(self): return sum(1 for _ in self.rows())
    def get(self, **kwargs):
        rows=list(self.filter(**kwargs).rows())
        assert len(rows)==1
        return SimpleNamespace(**rows[0])
    def values(self, field): return [row['id' if field=='pk' else field] for row in self.rows()]
    def values_list(self, *fields): return [tuple(row[field] for field in fields) for row in self.rows()]
    def update(self, **kwargs):
        for row in list(self.rows()): self.model.records[row['id']] = dict(row, **kwargs)

    def bulk_create(self, objects, batch_size):
        assert batch_size in (100, 500)
        if self.model.fail_insert: raise RuntimeError('synthetic insert failure')
        for obj in objects:
            if obj.pk in self.model.records: raise AssertionError('duplicate PK')
            # Django mutates these fields even if supplied to the constructor.
            obj.created_at = obj.updated_at = datetime.now(timezone.utc)
            self.model.records[obj.pk] = copy.deepcopy(vars(obj))


def model_double(name, sample):
    class Model:
        records = {}
        fail_insert = False
        def __init__(self, **kwargs): self.__dict__.update(copy.deepcopy(kwargs))
        @property
        def pk(self): return self.id
    Model.__name__ = name
    Model._meta = SimpleNamespace(concrete_fields=[SimpleNamespace(attname=k) for k in sample],
        pk=SimpleNamespace(attname='id'), db_table={'Session':'trace_session','Project':'tracer_project',
            'Trace':'tracer_trace','Span':'tracer_observation_span'}[name])
    Model.all_objects = Query(Model)
    return Model


def orm_env():
    env = payload_env()
    rows = env['payloads'](CONTEXT, 0, 1)
    for name, sample in zip(('Session','Trace','Span'), rows):
        env[name] = model_double(name, sample[0])
    env['Project'] = model_double('Project', env['project_payloads'](CONTEXT)[0])
    for name in ('Trace','Span','Session'): env[name].project_model = env['Project']
    for row in env['project_payloads'](CONTEXT): env['Project'].records[row['id']] = row
    @contextmanager
    def atomic():
        models = [env[name] for name in ('Project','Session','Trace','Span')]
        before = [dict(model.records) for model in models]
        try: yield
        except BaseException:
            for model, records in zip(models, before): model.records = records
            raise
    # Execute the actual common functions; imports/model resolution are tested
    # separately. No duplicate test implementation of matching or payload logic.
    functions = ast.Module(body=[node for node in ast.parse(t.COMMON).body
        if isinstance(node, ast.FunctionDef)], type_ignores=[])
    exec(compile(functions, '<fixed-common-functions>', 'exec'), env)
    env.update(json=json, transaction=SimpleNamespace(atomic=atomic), Exists=Exists, OuterRef=OuterRef)
    return env


def execute(env, source):
    with redirect_stdout(io.StringIO()) as output:
        exec(source, env)
    return json.loads(output.getvalue()) if output.getvalue() else None


def schema_env():
    """Metadata double from real checked-out model declarations, without Django.

    Physical columns/constraints remain a double, not a claim about PostgreSQL.
    Tests mutate them to execute the production fail-closed schema checks.
    """
    class Unique:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
        def __eq__(self, other): return type(other) is Unique and vars(self)==vars(other)
    env={'models':SimpleNamespace(UniqueConstraint=Unique),'Q':lambda **kwargs:kwargs}
    root=Path(__file__).resolve().parents[2]/'futureagi/tracer/models'
    definitions={}
    for name,filename,cls in [('Project','project.py','Project'),('Session','trace_session.py','TraceSession'),
            ('Trace','trace.py','Trace'),('Span','observation_span.py','ObservationSpan')]:
        node=next(node for node in ast.parse((root/filename).read_text()).body
                  if isinstance(node,ast.ClassDef) and node.name==cls)
        definitions[name]=[]
        for declaration in node.body:
            if not isinstance(declaration,ast.Assign) or not isinstance(declaration.value,ast.Call): continue
            # This double represents v1.56.1/v1.58.0, not the target model.
            # 0127 and 0117_cohort_sampling add these only on the target.
            # The exact source/target comparison below guards this projection.
            if name=='Project' and declaration.targets[0].id in (
                    'analysis_source_generation', 'sampling_contact_attribute'): continue
            function=declaration.value.func
            if (isinstance(function,ast.Attribute) and isinstance(function.value,ast.Name)
                    and function.value.id=='models' and (function.attr.endswith('Field') or function.attr=='ForeignKey')):
                definitions[name].append(declaration)
        env[name]=type(cls,(),{})
    env.update(TraceSession=env['Session'],ObservationSpan=env['Span'])
    def target(name):
        if name not in env:
            env[name]=type(name,(),{'_meta':SimpleNamespace(db_table='context_'+name.lower(),
                pk=SimpleNamespace(column='id'))})
        return env[name]
    target('User'); target('Workspace')
    constraints,columns={},{}
    for name in definitions:
        model=env[name]
        fields=[]
        declarations=ast.parse("created_at=models.DateTimeField()\nupdated_at=models.DateTimeField()\ndeleted=models.BooleanField()\ndeleted_at=models.DateTimeField(null=True)").body+definitions[name]
        for node in declarations:
            field_name=node.targets[0].id
            call=node.value
            kind=call.func.attr
            options={kw.arg:ast.literal_eval(kw.value) for kw in call.keywords if isinstance(kw.value,ast.Constant)}
            fk=kind in ('ForeignKey','OneToOneField')
            field=SimpleNamespace(name=field_name,attname=field_name+('_id' if fk else ''),
                column=field_name+('_id' if fk else ''),primary_key=options.get('primary_key',False),
                unique=options.get('primary_key',False) or kind=='OneToOneField',
                null=options.get('null',False),db_constraint=options.get('db_constraint',True),
                many_to_one=kind=='ForeignKey',one_to_one=kind=='OneToOneField',
                max_length=options.get('max_length'),get_internal_type=lambda kind=kind:kind)
            if fk:
                remote=call.args[0]
                field.remote_field=SimpleNamespace(model=target(remote.id if isinstance(remote,ast.Name)
                    else ast.literal_eval(remote).split('.')[-1]))
            field.flatchoices=[]
            fields.append(field)
        table={'Project':'tracer_project','Session':'trace_session','Trace':'tracer_trace','Span':'tracer_observation_span'}[name]
        lookup={f.name:f for f in fields}
        unique=Unique(fields=['name','trace_type','organization','workspace'],condition={'deleted':False},name='unique_project_per_org_type')
        model._meta=SimpleNamespace(managed=True,db_table=table,concrete_fields=fields,pk=lookup['id'],
            get_field=lookup.__getitem__,unique_together=(),constraints=[unique] if name=='Project' else [])
        columns[table]=[SimpleNamespace(name=f.column) for f in fields]
    for name in definitions:
        model=env[name]
        actual={'pk':dict(primary_key=True,unique=True,columns=['id'])}
        for field in model._meta.concrete_fields:
            if field.many_to_one or field.one_to_one:
                field.target_field=field.remote_field.model._meta.pk
                if field.db_constraint:
                    actual['fk_'+field.column]=dict(columns=[field.column],foreign_key=(
                        field.remote_field.model._meta.db_table,field.target_field.column))
            if field.unique and not field.primary_key:
                actual['unique_'+field.column]=dict(unique=True,columns=[field.column])
        if name=='Project':
            actual['unique_project_per_org_type']=dict(unique=True,columns=['name','trace_type','organization_id','workspace_id'])
        constraints[model._meta.db_table]=actual
    for name,field,values in [('Project','source',['demo','prototype']),('Project','trace_type',['observe']),
            ('Project','model_type',['GenerativeLLM']),('Trace','error_analysis_status',['skipped']),
            ('Span','observation_type',['agent','retriever']),('Span','status',['OK','ERROR'])]:
        env[name]._meta.get_field(field).flatchoices=[(value,value) for value in values]
    env['connection']=SimpleNamespace(vendor='postgresql',cursor=mock.MagicMock(),introspection=SimpleNamespace(
        get_table_description=lambda cursor,table:columns[table],get_constraints=lambda cursor,table:constraints[table]))
    return env,columns,constraints


class TraceReferenceTests(unittest.TestCase):
    def test_dispatch_exact_table_model_engine_and_inheritance(self):
        self.assertEqual(len(ENTRIES), 2)
        for cls in (s.ShippingRehearsal, q.QualificationRehearsal):
            for entry in ENTRIES:
                driver = driver_for(cls=cls)
                driver.seed_trace_reference_family = mock.Mock()
                driver.app_python = mock.Mock()
                driver.seed_reference_table(entry,100000)
                driver.seed_trace_reference_family.assert_called_once_with(100000)
                driver.app_python.assert_not_called()
                for key, value in [('database','clickhouse'),('database','sqlite'),('app_label','unknown'),
                        ('model','Unknown'),('table','public.'+entry['table']),('table','tracer_eval_logger')]:
                    with self.subTest(entry=entry,key=key), self.assertRaises(p.ManualRelease):
                        driver.seed_reference_table(dict(entry,**{key:value}),100000)
                for model in ('AgentccShadowResult','EvalLogger'):
                    with self.assertRaises(p.ManualRelease):
                        driver.seed_reference_table(dict(entry, app_label='prism',model=model),100000)
                driver.app_python.assert_not_called()

    def test_programs_compile_and_only_source_models_are_resolved(self):
        for program in (t.PREPARE,t.CHUNK,t.VERIFY):
            compile(s.DJANGO_PREFIX+program,'<fixed-source-operation>','exec')
            tree = ast.parse(program)
            calls = [node for node in ast.walk(tree) if isinstance(node,ast.Call)]
            names = [(ast.literal_eval(node.args[0]),ast.literal_eval(node.args[1])) for node in calls
                if isinstance(node.func,ast.Attribute) and node.func.attr=='get_model']
            self.assertEqual(set(names), {('tracer',name) for name in ('Project','TraceSession','Trace','ObservationSpan')} |
                {('accounts','User'),('accounts','Workspace')})
            self.assertFalse(any(isinstance(node, ast.keyword) and node.arg=='ignore_conflicts' for node in ast.walk(tree)))
            self.assertFalse(any(isinstance(node,ast.Attribute) and node.attr in ('delete','schema_editor','migrate') for node in ast.walk(tree)))
            self.assertNotIn('ANALYZE',program)
            self.assertNotIn('pg_stat',program)

    def test_missing_source_model_fails_before_any_write(self):
        apps = mock.Mock()
        apps.get_model.side_effect = LookupError('source model missing')
        db = SimpleNamespace(connection=mock.Mock(), transaction=mock.Mock(), models=mock.Mock())
        with mock.patch.dict('sys.modules', {'django.apps':SimpleNamespace(apps=apps),'django.db':db,
                'django.db.models':SimpleNamespace(Q=mock.Mock(),Exists=Exists,OuterRef=OuterRef)}):
            with self.assertRaises(LookupError): exec(t.PREPARE, {'a':ARGS})
        db.transaction.atomic.assert_not_called()
        db.connection.cursor.assert_not_called()

    def test_source_schema_projection_matches_both_releases_and_exact_target_delta(self):
        # Import here: usage's ORM double also reuses this module's Query.
        from deploy.tests.test_update_usage_reference import ROOT, REVISIONS, source

        def fields(raw, cls):
            node=next(node for node in ast.parse(raw).body
                if isinstance(node,ast.ClassDef) and node.name==cls)
            return {node.targets[0].id:ast.dump(node.value,include_attributes=False)
                for node in node.body if isinstance(node,ast.Assign)
                and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute)
                and isinstance(node.value.func.value,ast.Name) and node.value.func.value.id=='models'
                and (node.value.func.attr.endswith('Field') or node.value.func.attr=='ForeignKey')}

        target_only={
            'analysis_source_generation': 'models.PositiveBigIntegerField(default=0, db_default=0, editable=False)',
            'sampling_contact_attribute': 'models.CharField(max_length=200, default="", db_default="", blank=True)',
        }
        env,_,_=schema_env()
        inherited={'created_at','updated_at','deleted','deleted_at'}
        for revision in REVISIONS:
            for name,filename,cls in [('Project','project.py','Project'),('Session','trace_session.py','TraceSession'),
                    ('Trace','trace.py','Trace'),('Span','observation_span.py','ObservationSpan')]:
                with self.subTest(revision=revision,model=name):
                    path='tracer/models/'+filename
                    released=fields(source(revision,path),cls)
                    current=fields((ROOT/'futureagi'/path).read_text(),cls)
                    additions={key:ast.dump(ast.parse(value,mode='eval').body,include_attributes=False)
                        for key,value in target_only.items()} if name=='Project' else {}
                    self.assertTrue(set(released).isdisjoint(additions))
                    self.assertEqual(current,dict(released,**additions))
                    self.assertEqual({field.name for field in env[name]._meta.concrete_fields},
                        set(released)|inherited)

    def test_unexpected_model_field_is_not_dropped_by_source_schema_fixture(self):
        path=Path(__file__).resolve().parents[2]/'futureagi/tracer/models/project.py'
        original=Path.read_text
        def read_text(candidate,*args,**kwargs):
            raw=original(candidate,*args,**kwargs)
            if candidate==path:
                raw=raw.replace('class Project(BaseModel):',
                    'class Project(BaseModel):\n    unexpected_field = models.CharField(max_length=20)')
            return raw
        with mock.patch.object(Path,'read_text',read_text):
            env,_,_=schema_env()
        self.assertIn('unexpected_field',{field.name for field in env['Project']._meta.concrete_fields})
        with self.assertRaisesRegex(AssertionError,'field inventory drift'):
            exec(t.SCHEMA_CHECK,env)

    def test_actual_schema_checks_require_reviewed_columns_and_constraints(self):
        env,columns,constraints=schema_env()
        exec(t.SCHEMA_CHECK,env)
        cases=[('column',lambda env,cols,cons:cols['tracer_observation_span'].pop()),
            ('pk',lambda env,cols,cons:cons['tracer_trace'].pop('pk')),
            ('unique',lambda env,cols,cons:cons['tracer_project'].pop('unique_project_per_org_type')),
            ('simulation unique',lambda env,cols,cons:cons['tracer_project'].pop('unique_simulation_agent_id')),
            ('FK',lambda env,cols,cons:cons['trace_session'].pop('fk_project_id')),
            ('unexpected check',lambda env,cols,cons:cons['tracer_trace'].update(extra={'check':True})),
            ('wrong field type',lambda env,cols,cons:setattr(env['Span']._meta.get_field('org_id'),'get_internal_type',lambda:'CharField')),
            ('wrong engine',lambda env,cols,cons:setattr(env['connection'],'vendor','sqlite'))]
        for label,change in cases:
            env,columns,constraints=schema_env()
            change(env,columns,constraints)
            with self.subTest(label=label),self.assertRaises(AssertionError): exec(t.SCHEMA_CHECK,env)

    def test_actual_prepare_uses_existing_user_workspace_and_rejects_changed_plan(self):
        env=orm_env()
        env['Project'].records.clear()
        user=SimpleNamespace(id=uuid.UUID(CONTEXT['user_id']))
        workspace=SimpleNamespace(created_by_id=user.id,created_at=datetime(2026,1,8,tzinfo=timezone.utc))
        env['User']=SimpleNamespace(objects=mock.Mock())
        env['User'].objects.get.return_value=user
        env['Workspace']=SimpleNamespace(all_objects=mock.Mock())
        env['Workspace'].all_objects.get.return_value=workspace
        env['physical_counts']=lambda:dict(tracer_trace=7,tracer_observation_span=13)
        result=execute(env,t.PREPARE_BODY)
        self.assertEqual(result,dict(context=CONTEXT,counts=dict(tracer_trace=7,tracer_observation_span=13),
            seeded_counts=dict(tracer_trace=0,tracer_observation_span=0)))
        env['User'].objects.get.assert_called_with(email=ARGS['email'],organization_id=ARGS['organization_id'],is_active=True)
        self.assertEqual(len(env['Project'].records),6)
        self.assertEqual(execute(env,t.PREPARE_BODY),result)
        projects=copy.deepcopy(env['Project'].records)
        env['a']['rows']=100001
        with self.assertRaisesRegex(AssertionError,'incompatible existing Project'): execute(env,t.PREPARE_BODY)
        self.assertEqual(projects,env['Project'].records)
        env['a']['rows']=100000
        workspace.created_by_id=uuid.UUID(int=999)
        with self.assertRaisesRegex(AssertionError,'account/workspace mismatch'): execute(env,t.PREPARE_BODY)

    def test_physical_counts_return_database_observations_without_zero_fallback(self):
        env=orm_env()
        cursor=mock.MagicMock()
        cursor.fetchone.side_effect=[(17,),(None,)]
        connection=SimpleNamespace(cursor=mock.MagicMock(),ops=SimpleNamespace(quote_name=lambda name:'"'+name+'"'))
        connection.cursor.return_value.__enter__.return_value=cursor
        env['connection']=connection
        self.assertEqual(env['physical_counts'](),dict(tracer_trace=17,tracer_observation_span=None))
        self.assertEqual(cursor.execute.call_args_list,[mock.call('SELECT count(*) FROM "tracer_trace"'),
            mock.call('SELECT count(*) FROM "tracer_observation_span"')])
        fake=FakeApp()
        fake.alter_prepared=lambda value:dict(value,counts=dict(tracer_trace=17,tracer_observation_span=None))
        with self.assertRaises(p.ManualRelease): driver_for(fake).seed_trace_reference_family(100000)

    def test_actual_payload_distribution_and_scoped_sessions(self):
        env = payload_env()
        seen = Counter()
        counts = Counter()
        for start in range(0,100000,t.CHUNK_TRACES):
            sessions,traces,spans = env['payloads'](CONTEXT,start,start+t.CHUNK_TRACES)
            by_session = {row['id']:row for row in sessions}
            by_trace = {row['id']:row for row in traces}
            span_counts = Counter(row['trace_id'] for row in spans)
            seen.update(row['metadata']['cohort'] for row in traces)
            counts.update(tracer_trace=len(traces),tracer_observation_span=len(spans))
            for trace in traces:
                name = trace['metadata']['cohort']
                self.assertEqual(span_counts[trace['id']],0 if name=='no_span' else 2)
                self.assertEqual(trace['session_id'] is None,name=='null_session')
                self.assertEqual(trace['deleted'],name=='deleted_trace')
                self.assertIsInstance(trace['id'],uuid.UUID)
                if trace['session_id'] is not None:
                    self.assertEqual(by_session[trace['session_id']]['project_id'],trace['project_id'])
            for span in spans:
                self.assertEqual(span['project_id'],by_trace[span['trace_id']]['project_id'])
                self.assertEqual(span['org_id'],uuid.UUID(ARGS['organization_id']))
                self.assertEqual(span['org_user_id'],uuid.UUID(CONTEXT['user_id']))
                self.assertGreater(span['end_time'],span['start_time'])
                self.assertEqual(len(span['id']),32)
                if span['metadata']['index']==1:
                    ordinal=span['metadata']['ordinal']
                    self.assertEqual(span['parent_span_id'],env['identity']('span',ordinal,0).hex)
        plan=t.trace_reference_plan(1)
        self.assertEqual(dict(counts),plan['counts'])
        self.assertEqual(dict(seen),{key:value for key,value in plan['cohorts'].items() if key!='eligible_spans'})
        # The second block adds no eligible work; scale never amplifies recovery.
        second=Counter(env['cohort'](ordinal) for ordinal in range(100000,200000))
        self.assertEqual(second['eligible'],0)
        self.assertEqual(second['non_demo'],80000)
        for scale in (1,2,1000): self.assertEqual(t.trace_reference_plan(scale)['cohorts']['eligible'],1250)
        self.assertEqual([min(500,1250-start) for start in range(0,1250,500)],[500,500,250])

    def test_actual_chunk_insert_replay_conflict_and_rollback(self):
        env = orm_env()
        env['a'].update(context=CONTEXT,start=0,stop=2500,scale=1)
        original_projects=copy.deepcopy(env['Project'].records)
        self.assertEqual(execute(env,t.CHUNK_BODY),{'skipped':False})
        self.assertEqual((len(env['Trace'].records),len(env['Span'].records)),(2500,5000))
        original=copy.deepcopy(env['Trace'].records)
        self.assertEqual(execute(env,t.CHUNK_BODY),{'skipped':True})
        self.assertEqual(original,env['Trace'].records)
        self.assertEqual(original_projects,env['Project'].records)
        # Same IDs/counts do not make a changed payload a matching replay.
        pk=next(iter(env['Span'].records))
        env['Span'].records[pk]['output']={'answer':'changed'}
        with self.assertRaisesRegex(AssertionError,'incompatible existing Span'):
            execute(env,t.CHUNK_BODY)
        env=orm_env()
        env['a'].update(context=CONTEXT,start=0,stop=2500,scale=1)
        env['Span'].fail_insert=True
        with self.assertRaisesRegex(RuntimeError,'insert failure'): execute(env,t.CHUNK_BODY)
        self.assertTrue(all(not env[name].records for name in ('Trace','Span','Session')))
        env['Span'].fail_insert=False
        execute(env,t.CHUNK_BODY)
        # Lost row inside a supposedly atomic committed chunk is not repaired.
        env['Span'].records.pop(next(iter(env['Span'].records)))
        with self.assertRaisesRegex(AssertionError,'partial atomic chunk'): execute(env,t.CHUNK_BODY)

    def test_no_span_chunk_and_changed_fixture_context(self):
        env=orm_env()
        env['a'].update(context=CONTEXT,start=95000,stop=97500,scale=1)
        self.assertEqual(execute(env,t.CHUNK_BODY),{'skipped':False})
        self.assertEqual(len(env['Span'].records),0)
        self.assertEqual(execute(env,t.CHUNK_BODY),{'skipped':True})
        env['a']['rows']=100001
        with self.assertRaisesRegex(AssertionError,'incompatible existing Project'): execute(env,t.CHUNK_BODY)

    def test_verify_predicate_matches_migration_without_importing_or_running_it(self):
        migration=Path(__file__).resolve().parents[2]/'futureagi/accounts/migrations/0030_queue_existing_demo_trace_publications.py'
        def predicates(source):
            calls=[node for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Call)]
            scope=next(node for node in calls if any(kw.arg=='project__source' for kw in node.keywords))
            span=next(node for node in calls if any(kw.arg=='trace_id' and isinstance(kw.value,ast.Call)
                and isinstance(kw.value.func,ast.Name) and kw.value.func.id=='OuterRef' for kw in node.keywords))
            return ({kw.arg:ast.literal_eval(kw.value) for kw in scope.keywords},
                [ast.dump(kw,include_attributes=False) for kw in span.keywords])
        self.assertEqual(predicates(t.VERIFY_BODY),predicates(migration.read_text()))

    def test_actual_verify_predicate_excludes_each_negative_and_deduplicates_spans(self):
        env=orm_env()
        # Small actual payload samples spanning every cohort.
        for ordinal in (0,1,1250,80000,85000,90000,95000):
            for model,rows in zip((env['Session'],env['Trace'],env['Span']),env['payloads'](CONTEXT,ordinal,ordinal+1)):
                for row in rows: model.records[row['id']]=row
        # Historical Exists sees even deleted spans; no implicit live-only join.
        for row in env['Span'].records.values():
            if row['metadata']['ordinal']==1: row['deleted']=True
        tree=ast.parse(t.VERIFY_BODY)
        start=next(i for i,node in enumerate(tree.body) if isinstance(node,ast.Assign)
            and isinstance(node.targets[0],ast.Tuple))
        code=compile(ast.Module(body=tree.body[start:],type_ignores=[]),'<actual-source-predicate>','exec')
        env['physical_counts']=lambda: {'tracer_trace':7,'tracer_observation_span':12}
        result=execute(env,code)
        self.assertEqual(result['cohorts'],dict(eligible=2,non_demo=1,deleted_trace=1,deleted_project=1,
            null_session=1,no_span=1,eligible_spans=4))
        self.assertEqual(result['seeded_counts'],dict(tracer_trace=7,tracer_observation_span=12))
        self.assertEqual(result['counts'],dict(tracer_trace=7,tracer_observation_span=12))

    def test_full_family_precedes_first_independent_measurement(self):
        for entries in (ENTRIES,ENTRIES[:1],ENTRIES[1:]):
            fake=FakeApp()
            driver=driver_for(fake,cls=q.QualificationRehearsal)
            measured=[]
            def measure(entry):
                self.assertEqual(len(fake.committed),40)
                self.assertTrue(hasattr(driver,'_trace_reference'))
                self.assertEqual(fake.calls[-1][0],s.DJANGO_PREFIX+t.VERIFY)
                measured.append(entry['table'])
                return dict(engine='postgres',table='public.'+entry['table'],rows=222222,bytes=7654321)
            result=p.seed_reference_tables(entries,driver.seed_reference_table,measure)
            self.assertEqual([row['rows'] for row in result],[222222]*len(entries))
            self.assertEqual(measured,[entry['table'] for entry in entries])
            self.assertEqual(sum(program==s.DJANGO_PREFIX+t.PREPARE for program,args in fake.calls),1)
            self.assertEqual(sum(program==s.DJANGO_PREFIX+t.CHUNK for program,args in fake.calls),40)
            self.assertEqual(len(driver.corpus['reference_seed_operations']),1)
            for secret in ('Authorization','SECRET','password','DO-NOT-FORWARD'):
                self.assertNotIn(secret,json.dumps([args for program,args in fake.calls]))

    def test_independent_measurement_cannot_be_replaced_by_fixture_counts(self):
        for bad in (dict(rows=99999,bytes=1000),dict(rows=100000,bytes=0),dict(rows=None,bytes=None)):
            driver=driver_for()
            with self.assertRaises(p.ManualRelease):
                p.seed_reference_tables(ENTRIES[:1],driver.seed_reference_table,
                    lambda entry:dict(engine='postgres',table='public.'+entry['table'],**bad))

    def test_same_and_new_instance_resume_and_validate_baseline_growth(self):
        for fresh in (False,True):
            fake=FakeApp()
            fake.fail_at=7500
            driver=driver_for(fake)
            with self.assertRaises(RuntimeError): driver.seed_trace_reference_family(100000)
            self.assertFalse(hasattr(driver,'_trace_reference'))
            self.assertNotIn('reference_seed_operations',driver.corpus)
            if fresh: driver=driver_for(fake)
            driver.seed_trace_reference_family(100000)
            self.assertEqual(fake.skipped,[0,2500,5000])
            self.assertEqual(driver._trace_reference['baseline'],fake.baseline)
            self.assertEqual(len(fake.committed),40)
            fake.alter_verified=lambda value:dict(value,counts={k:v+111 for k,v in value['counts'].items()})
            driver.seed_trace_reference_family(100000)
            self.assertEqual(fake.calls[-1][0],s.DJANGO_PREFIX+t.VERIFY)
            self.assertEqual(len(driver.corpus['reference_seed_operations']),1)

    def test_bad_counts_and_unknown_cohorts_never_complete(self):
        mutations=[lambda value:None,lambda value:dict(value,counts={}),lambda value:dict(value,seeded_counts={}),
            lambda value:dict(value,cohorts={}),lambda value:dict(value,cohorts=dict(value['cohorts'],eligible=0)),
            lambda value:dict(value,seeded_counts={k:float(v) for k,v in value['seeded_counts'].items()}),
            lambda value:dict(value,counts=t.trace_reference_plan(1)['counts'])]
        for change in mutations:
            fake=FakeApp()
            fake.alter_verified=change
            driver=driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_trace_reference_family(100000)
            self.assertFalse(hasattr(driver,'_trace_reference'))
            self.assertNotIn('reference_seed_operations',driver.corpus)
            fake.alter_verified=lambda value:value
            driver.seed_trace_reference_family(100000)
            self.assertEqual(fake.skipped,list(range(0,100000,2500)))

    def test_invalid_prepare_rows_and_context_fail_closed(self):
        for change in (lambda value:None,lambda value:dict(value,counts={}),
                lambda value:dict(value,seeded_counts={k:False for k in TABLES}),
                lambda value:dict(value,seeded_counts={k:1000000 for k in TABLES})):
            fake=FakeApp()
            fake.alter_prepared=change
            driver=driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_trace_reference_family(100000)
            self.assertEqual(len(fake.calls),1)
            self.assertFalse(hasattr(driver,'_trace_reference_pending'))
        for rows in (True,99999,100000001,100000.0,'100000'):
            driver=driver_for(mock.Mock())
            with self.assertRaises(p.ManualRelease): driver.seed_trace_reference_family(rows)
            driver.app_python.assert_not_called()
        for source in (None,{},dict(namespace='source',headers={}),dict(namespace='source',headers={},account={})):
            driver=driver_for(mock.Mock())
            driver.witnesses.namespaces={'source':source}
            with self.assertRaises(p.ManualRelease): driver.seed_trace_reference_family(100000)
            driver.app_python.assert_not_called()

    def test_scale_rounding_and_changed_arguments_refuse(self):
        fake=FakeApp(scale=2)
        driver=driver_for(fake)
        driver.seed_trace_reference_family(100001)
        self.assertEqual(len(fake.committed),80)
        self.assertEqual(driver._trace_reference['cohorts']['eligible'],1250)
        calls=len(fake.calls)
        for rows in (100000,200000):
            with self.assertRaises(p.ManualRelease): driver.seed_trace_reference_family(rows)
        self.assertEqual(len(fake.calls),calls)
        driver.witnesses.namespaces['source']['headers']['X-Workspace-Id']='different'
        with self.assertRaises(p.ManualRelease): driver.seed_trace_reference_family(100001)
        self.assertEqual(len(fake.calls),calls)

    def test_error_feed_and_trace_families_keep_independent_state(self):
        from deploy.tests.test_update_error_feed_reference import FakeApp as ErrorApp, ENTRIES as ERROR_ENTRIES
        trace, error=FakeApp(),ErrorApp()
        def app(program,args):
            return trace(program,args) if program in (s.DJANGO_PREFIX+t.PREPARE,s.DJANGO_PREFIX+t.CHUNK,s.DJANGO_PREFIX+t.VERIFY) else error(program,args)
        driver=driver_for(app)
        driver.seed_reference_table(ERROR_ENTRIES[0],100000)
        before=copy.deepcopy(driver._error_feed_reference)
        driver.seed_reference_table(ENTRIES[0],100000)
        self.assertEqual(before,driver._error_feed_reference)
        driver.seed_reference_table(ERROR_ENTRIES[1],100000)
        self.assertEqual(len(error.committed),20)
        self.assertEqual(len(trace.committed),40)
        self.assertEqual(len(driver.corpus['reference_seed_operations']),2)
        self.assertNotEqual(uuid.uuid5(uuid.NAMESPACE_URL,'futureagi-qualification-reference:'+ARGS['namespace']),
                            payload_env()['NAMESPACE'])


if __name__ == '__main__':
    unittest.main()
