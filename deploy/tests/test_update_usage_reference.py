"""Daemon-free contract/fake regressions. Never native or financial evidence."""
import ast
from collections import Counter
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tokenize
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_shipping as s
from scripts import update_rehearsal_qualification as q
from scripts import update_rehearsal_usage_reference as u
from deploy.tests.test_update_eval_task_reference import Query as TaskQuery, execute

ROOT = Path(__file__).resolve().parents[2]
REVISIONS = ('6840e44914cf7c85cd0ae65d98761f7728b8afcc', '28e729aab169d75a2ca856fb4ed67fd1dda5901a')
TABLE = 'usage_apicalllog'
ENTRY = next(row for row in p.load_high_volume_registry() if row['database']=='postgres' and row['table']==TABLE)
ARGS = dict(namespace='usage-reference-source', email='usage-reference@example.test', rows=100000,
    organization_id=str(uuid.UUID(int=1)), workspace_id=str(uuid.UUID(int=2)))
CONTEXT = dict(user_id=str(uuid.UUID(int=3)), historical_at='2025-01-01T00:00:00+00:00',
    call_types={name:10+i for i,name in enumerate(u.CALL_TYPES)})


def source(revision, path):
    return subprocess.check_output(['git','-C',str(ROOT),'show',revision+':futureagi/'+path],text=True)


def usage_source_nodes(raw):
    """Parse only the four reviewed top-level billing definitions on Python 3.9.

    Historical usage_entries also contains unrelated match/case code. Tokenize
    understands its boundaries without requiring the newer Python grammar; real
    logical statements/indentation, not text matches, delimit the selected code.
    """
    wanted = {'api_call_type_entries': ast.Assign, 'create_usage_entries': ast.FunctionDef,
        'check_if_api_call_is_rate_limited': ast.FunctionDef, 'refund_cost_for_api_call': ast.FunctionDef}
    tokens = list(tokenize.generate_tokens(io.StringIO(raw).readline))
    starts = []; depth = 0; statement_start = True
    for index, token in enumerate(tokens):
        if token.type == tokenize.INDENT:
            depth += 1
        elif token.type == tokenize.DEDENT:
            depth -= 1
        elif token.type == tokenize.NEWLINE:
            statement_start = True
        elif token.type not in (tokenize.NL, tokenize.COMMENT, tokenize.ENDMARKER):
            if depth == 0 and statement_start:
                starts.append(index)
            statement_start = False
    lines = raw.splitlines(keepends=True); nodes = {}
    for start, end in zip(starts, starts[1:] + [len(tokens) - 1]):
        first = tokens[start]
        if first.type != tokenize.NAME:
            continue
        name = tokens[start + 1].string if first.string == 'def' else first.string
        if name not in wanted:
            continue
        tree = ast.parse(''.join(lines[first.start[0] - 1:tokens[end].start[0] - 1]))
        assert len(tree.body) == 1 and isinstance(tree.body[0], wanted[name]), name
        node = tree.body[0]
        if isinstance(node, ast.Assign):
            assert len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name, name
        else:
            assert node.name == name, name
        assert name not in nodes, 'duplicate billing definition: ' + name
        nodes[name] = node
    assert set(nodes) == set(wanted), 'missing billing definitions: ' + str(set(wanted) - set(nodes))
    return nodes


def payload_env():
    env={'a':copy.deepcopy(ARGS)}
    exec(u.ROWS,env)
    return env


class FakeApp:
    def __init__(self, scale=1):
        self.scale,self.baseline=scale,11
        self.calls,self.committed,self.skipped=[],set(),[]
        self.fail_at=None
        self.alter_verified=self.alter_prepared=lambda r:r

    def __call__(self, program, args):
        self.calls.append((program,copy.deepcopy(args)))
        if program==s.DJANGO_PREFIX+u.PREPARE:
            seeded=2500*len(self.committed)
            return self.alter_prepared(dict(context=copy.deepcopy(CONTEXT),
                counts={TABLE:self.baseline+seeded},seeded_counts={TABLE:seeded}))
        if program==s.DJANGO_PREFIX+u.CHUNK:
            assert args['stop']==args['start']+2500 and args['scale']==self.scale
            if args['start']==self.fail_at:
                self.fail_at=None
                raise RuntimeError('synthetic interruption')
            skipped=args['start'] in self.committed
            if skipped: self.skipped.append(args['start'])
            self.committed.add(args['start'])
            return dict(skipped=skipped)
        if program==s.DJANGO_PREFIX+u.VERIFY:
            assert self.committed==set(range(0,100000*self.scale,2500)), 'premature verify'
            plan=u.usage_reference_plan(self.scale)
            return self.alter_verified(dict(counts={TABLE:self.baseline+100000*self.scale},
                seeded_counts=plan['counts'],cohorts=plan['cohorts'],deleted=plan['deleted'],identity_sha256='a'*64))
        raise AssertionError('unexpected app operation')


def driver_for(fake=None, cls=s.ShippingRehearsal):
    driver=object.__new__(cls)
    driver.witnesses=SimpleNamespace(namespaces={'source':dict(namespace=ARGS['namespace'],
        account={'email':ARGS['email'],'password':'DO-NOT-FORWARD'},headers={
            'X-Organization-Id':ARGS['organization_id'],'X-Workspace-Id':ARGS['workspace_id'],
            'Authorization':'Bearer SECRET','X-Api-Key':'SECRET'})})
    driver.app_python=fake if fake is not None else FakeApp()
    driver.corpus={}
    return driver


class Query(TaskQuery):
    def filter(self, **kwargs):
        # The real database has a unique log_id index; avoid quadratic scans in
        # this small in-memory double when verifying every source field.
        if 'log_id__in' in kwargs: kwargs['log_id__in']=set(kwargs['log_id__in'])
        return Query(self.model,self.conditions+(kwargs,))
    def first(self):
        row=next(iter(self.rows()),None)
        return None if row is None else self.model(**row)
    def get(self, **kwargs):
        rows=list(self.filter(**kwargs).rows())
        assert len(rows)==1
        return self.model(**rows[0])
    def create(self, **kwargs):
        obj=self.model(id=max(self.model.records,default=9)+1,created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),deleted=False,deleted_at=None,**kwargs)
        self.model.records[obj.id]=copy.deepcopy(vars(obj))
        return obj
    def bulk_update(self, objects, fields, batch_size):
        super().bulk_update(objects,fields,batch_size)
        if self.model.fail_update: raise RuntimeError('synthetic update failure')


def orm_env():
    env=payload_env()
    for alias,name in [('Log','APICallLog'),('CallType','APICallType')]:
        def init(self, **kwargs): self.__dict__.update(copy.deepcopy(kwargs))
        model=type(name,(),{'records':{},'fail_insert':False,'fail_update':False,'__init__':init,
            'pk':property(lambda obj:obj.id)})
        fields={key:SimpleNamespace(attname=key+('_id' if spec[3] else ''))
                for key,spec in u.SOURCE_FIELDS[name].items()}
        model._meta=SimpleNamespace(object_name=name,get_field=fields.__getitem__)
        model.all_objects=model.objects=Query(model)
        env[alias]=model
    functions=ast.Module(body=[node for node in ast.parse(u.COMMON).body if isinstance(node,ast.FunctionDef)],type_ignores=[])
    exec(compile(functions,'<usage-common>','exec'),env)
    for name,pk in CONTEXT['call_types'].items():
        env['CallType'].records[pk]=dict(id=pk,name=name,description=u.CALL_TYPES[name],deleted=False,deleted_at=None)
    @contextmanager
    def atomic():
        models=[env['Log'],env['CallType']]
        before=[copy.deepcopy(m.records) for m in models]
        try: yield
        except BaseException:
            for model,records in zip(models,before): model.records=records
            raise
    env.update(json=json,hashlib=hashlib,transaction=SimpleNamespace(atomic=atomic))
    env['owner_context']=lambda:{k:CONTEXT[k] for k in ('user_id','historical_at')}
    env['financial_snapshot']=mock.Mock(return_value={'owned_financial_hash':'unchanged'})
    env['check_no_events']=mock.Mock()
    env['observation']=lambda:{}
    env['a'].update(context=copy.deepcopy(CONTEXT),start=0,stop=2500,scale=1)
    return env


def schema_env():
    """Literal reviewed model contract + catalog double; not a PostgreSQL probe."""
    cascade=object()
    env={'SOURCE_FIELDS':copy.deepcopy(u.SOURCE_FIELDS),'CALL_TYPES':u.CALL_TYPES,'models':SimpleNamespace(CASCADE=cascade)}
    targets={}
    def target(label):
        if label not in targets:
            name=label.split('.')[-1]
            targets[label]=type(name,(),{'_meta':SimpleNamespace(label=label,db_table=label.lower().replace('.','_'),
                pk=SimpleNamespace(name='id',column='id',get_internal_type=lambda:'UUIDField'))})
        return targets[label]
    env['Log']=target('usage.APICallLog'); env['CallType']=target('usage.APICallType')
    columns,constraints={},{}
    for alias,name,table,unique in [('Log','APICallLog',TABLE,'log_id'),('CallType','APICallType','usage_apicalltype','name')]:
        model=env[alias]; fields={}
        for key,(kind,nullable,length,remote) in u.SOURCE_FIELDS[name].items():
            field=SimpleNamespace(name=key,column=key+('_id' if remote else ''),null=nullable,max_length=length,
                get_internal_type=lambda kind=kind:kind,unique=key in ('id',unique),primary_key=key=='id',
                db_constraint=True,flatchoices=[],max_digits=16,decimal_places=8)
            if remote: field.remote_field=SimpleNamespace(model=target(remote),on_delete=cascade)
            fields[key]=field
        model._meta=SimpleNamespace(object_name=name,label='usage.'+name,db_table=table,managed=True,
            concrete_fields=list(fields.values()),get_field=fields.__getitem__,pk=fields['id'],constraints=[],unique_together=[],many_to_many=[])
        columns[table]=[SimpleNamespace(name=f.column,null_ok=f.null) for f in fields.values()]
    for alias in ('Log','CallType'):
        model=env[alias]; actual={'pk':dict(primary_key=True,unique=True,columns=['id'])}
        for field in model._meta.concrete_fields:
            if hasattr(field,'remote_field'):
                field.target_field=field.remote_field.model._meta.pk
                actual['fk_'+field.column]=dict(columns=[field.column],foreign_key=(
                    field.remote_field.model._meta.db_table,field.target_field.column))
            if field.unique and not field.primary_key: actual['unique_'+field.column]=dict(unique=True,columns=[field.column])
        if alias=='Log': actual['positive_token']=dict(check=True,columns=['input_token_count'])
        constraints[model._meta.db_table]=actual
    env['Log']._meta.get_field('status').flatchoices=[(v,v) for v in (*u.COHORTS,'success','processing','not_started')]
    env['CallType']._meta.get_field('name').flatchoices=[(v,v) for v in u.CALL_TYPES]
    env['connection']=SimpleNamespace(vendor='postgresql',cursor=mock.MagicMock(),introspection=SimpleNamespace(
        get_table_description=lambda cursor,table:columns[table],get_constraints=lambda cursor,table:constraints[table]))
    return env,columns,constraints


class UsageReferenceTests(unittest.TestCase):
    def test_full_100k_plan_unique_owned_relations_and_terminal_zero_cost_payloads(self):
        env=payload_env(); seen=set(); cohorts=Counter(); nullable=Counter(); deleted=0
        for start in range(0,100000,2500):
            rows=env['payloads'](CONTEXT,start,start+2500)
            for row in rows:
                self.assertNotIn('id',row)  # DB assigns the BigAutoField.
                self.assertNotIn(row['log_id'],seen); seen.add(row['log_id'])
                cohorts[row['status']]+=1; deleted+=row['deleted']
                self.assertEqual(row['organization_id'],uuid.UUID(ARGS['organization_id']))
                for field,owned in [('workspace_id',ARGS['workspace_id']),('user_id',CONTEXT['user_id'])]:
                    self.assertIn(row[field],(None,uuid.UUID(owned)))
                    nullable[(field,row[field] is None)]+=1
                self.assertIn(row['api_call_type_id'],CONTEXT['call_types'].values())
                self.assertEqual((row['cost'],row['deducted_cost']),(Decimal(0),Decimal(0)))
                self.assertIsNone(row['refund_parent_id'])
                self.assertLess(row['updated_at'],datetime(2026,1,1,tzinfo=timezone.utc))
                self.assertEqual(row['deleted_at'],row['updated_at'] if row['deleted'] else None)
                self.assertEqual(set(row),{key+('_id' if spec[3] else '') for key,spec in u.SOURCE_FIELDS['APICallLog'].items() if key!='id'})
            self.assertEqual(rows,env['payloads'](CONTEXT,start,start+2500))
        self.assertEqual(cohorts,u.COHORTS); self.assertEqual(deleted,10000); self.assertEqual(len(seen),100000)
        self.assertEqual(len(nullable),4)
        self.assertNotEqual(env['identity']('log',0),uuid.uuid5(uuid.NAMESPACE_URL,'different'))
        other=payload_env(); other['a']['namespace']='other'; exec(u.ROWS,other)
        self.assertNotEqual(env['identity']('log',0),other['identity']('log',0))
        for scale in (1,2,17):
            self.assertEqual(u.usage_reference_plan(scale),dict(counts={TABLE:100000*scale},
                cohorts={k:v*scale for k,v in u.COHORTS.items()},deleted=10000*scale))

    def test_pinned_source_schema_choices_bootstrap_and_billing_selectors(self):
        def cls(tree,name): return next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
        labels={name:'accounts.'+name for name in ('Organization','Workspace','User')}; labels['APICallType']='usage.APICallType'
        for revision in REVISIONS:
            raw=source(revision,'ee/usage/models/usage.py')
            self.assertEqual(raw,(ROOT/'futureagi/ee/usage/models/usage.py').read_text())
            base=cls(ast.parse(source(revision,'tfc/utils/base_model.py')),'BaseModel')
            tree=ast.parse(raw)
            for name in ('APICallLog','APICallType'):
                actual={'id':('BigAutoField',False,None,None)}; unique=set()
                for n in base.body+cls(tree,name).body:
                    if not isinstance(n,ast.Assign) or not isinstance(n.value,ast.Call): continue
                    call=n.value
                    if not (isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id=='models'): continue
                    kind=call.func.attr
                    if kind=='Manager': continue
                    options={kw.arg:ast.literal_eval(kw.value) for kw in call.keywords if isinstance(kw.value,ast.Constant)}
                    remote=labels[call.args[0].id] if kind=='ForeignKey' else None
                    actual[n.targets[0].id]=(kind,options.get('null',False),32 if kind=='UUIDField' else options.get('max_length'),remote)
                    if options.get('unique'): unique.add(n.targets[0].id)
                    if kind=='DecimalField': self.assertEqual((options['max_digits'],options['decimal_places']),(16,8))
                self.assertEqual(actual,u.SOURCE_FIELDS[name])
                self.assertEqual(unique,{'log_id'} if name=='APICallLog' else {'name'})
            constants=ast.parse(source(revision,'tfc/constants/api_calls.py'))
            choices={n.value.elts[0].value for n in cls(constants,'APICallTypeChoices').body if isinstance(n,ast.Assign)}
            self.assertTrue(set(u.CALL_TYPES)<=choices); self.assertNotIn('jev_evaluator',choices)
            names={n.targets[0].id:n.value.elts[0].value for n in cls(constants,'APICallTypeChoices').body if isinstance(n,ast.Assign)}
            self.assert_usage_entries_contract(source(revision,'ee/usage/utils/usage_entries.py'),names)
            bootstrap=source(revision,'ee/usage/apps.py')
            self.assertIn('default_auto_field = "django.db.models.BigAutoField"',bootstrap)
            self.assertIn('post_migrate.connect',bootstrap); self.assertIn('create_usage_entries()',bootstrap)
            sql=source(revision,'ee/usage/utils/usage_sqls.py')
            self.assertIn("acl.status IN ('success', 'processing')",sql); self.assertIn('acl.deducted_cost > 0',sql)

    def assert_usage_entries_contract(self, raw, names):
        usage=usage_source_nodes(raw)
        entries=usage['api_call_type_entries'].value
        self.assertIsInstance(entries,ast.List)
        canonical={}
        for entry in entries.elts:
            fields={k.value:v for k,v in zip(entry.keys,entry.values)}
            canonical[names[fields['name'].value.attr]]=ast.literal_eval(fields['description'])
        self.assertTrue(set(u.CALL_TYPES)<=set(canonical))
        self.assertEqual({name:canonical[name] for name in u.CALL_TYPES},u.CALL_TYPES)
        # Require actual calls/guards, not matching text in a comment or string.
        create=usage['create_usage_entries']
        call=ast.parse('insert_api_call_type_entries(api_call_type_entries)').body[0].value
        self.assertIn(ast.dump(call),[ast.dump(n) for n in ast.walk(create) if isinstance(n,ast.Call)])
        # All old rate-limit status lists select success/processing, not our cohorts.
        selector=usage['check_if_api_call_is_rate_limited']
        statuses=[kw.value for n in ast.walk(selector) if isinstance(n,ast.Call) for kw in n.keywords if kw.arg=='status__in']
        self.assertTrue(statuses)
        for values in statuses: self.assertEqual({v.value.attr for v in values.elts},{'SUCCESS','PROCESSING'})
        guard=ast.parse('if not api_call_log_row or api_call_log_row.deducted_cost <= 0:\n    return None').body[0]
        self.assertTrue(any(ast.dump(n.test)==ast.dump(guard.test) and
            [ast.dump(stmt) for stmt in n.body]==[ast.dump(stmt) for stmt in guard.body]
            for n in ast.walk(usage['refund_cost_for_api_call']) if isinstance(n,ast.If)))

    def usage_inspection_fixture(self):
        # Keep real reviewed definitions, with unsupported syntax between each.
        raw=source(REVISIONS[0],'ee/usage/utils/usage_entries.py')
        definitions={name:ast.unparse(node) for name,node in usage_source_nodes(raw).items()}
        constants=ast.parse(source(REVISIONS[0],'tfc/constants/api_calls.py'))
        choices=next(n for n in constants.body if isinstance(n,ast.ClassDef) and n.name=='APICallTypeChoices')
        names={n.targets[0].id:n.value.elts[0].value for n in choices.body if isinstance(n,ast.Assign)}
        contract='\n\n'.join(definitions.values())
        # These exact, valid-looking definitions must never satisfy inspection.
        decoys='"""\n'+contract+'\n"""\n'+'\n'.join('# '+line for line in contract.splitlines())+'\n'
        decoys+='def nested_decoys():\n'+'\n'.join('    '+line for line in contract.splitlines())+'\n'
        newer='\ndef unrelated(value):\n    match value:\n        case _:\n            return value\n'
        def assemble(parts): return decoys+newer+newer.join(parts)+'\n'+newer
        return definitions,names,assemble

    def test_usage_source_inspection_tolerates_unrelated_newer_syntax(self):
        definitions,names,assemble=self.usage_inspection_fixture()
        raw=assemble(definitions.values())
        self.assertEqual(set(usage_source_nodes(raw)),set(definitions))
        self.assert_usage_entries_contract(raw,names)
        # A relevant definition's syntax must still be parsed, never swallowed.
        broken=dict(definitions); broken['create_usage_entries']='def create_usage_entries(:\n    pass'
        with self.assertRaises((SyntaxError,tokenize.TokenError)):
            self.assert_usage_entries_contract(assemble(broken.values()),names)

    def test_usage_source_inspection_rejects_missing_and_duplicate_definitions(self):
        definitions,names,assemble=self.usage_inspection_fixture()
        for name,definition in definitions.items():
            with self.subTest(missing=name),self.assertRaisesRegex(AssertionError,'missing billing definitions'):
                self.assert_usage_entries_contract(assemble([value for key,value in definitions.items() if key!=name]),names)
            with self.subTest(duplicate=name),self.assertRaisesRegex(AssertionError,'duplicate billing definition'):
                self.assert_usage_entries_contract(assemble(list(definitions.values())+[definition]),names)

    def test_usage_source_inspection_rejects_assignment_selector_and_refund_drift(self):
        definitions,names,assemble=self.usage_inspection_fixture()
        edits=[('api_call_type_entries',definitions['api_call_type_entries'],'api_call_type_entries = []'),
            ('api_call_type_entries','This is the API for PROMPT BENCH','changed description'),
            ('create_usage_entries','insert_api_call_type_entries(api_call_type_entries)',
                repr('insert_api_call_type_entries(api_call_type_entries)')),
            ('check_if_api_call_is_rate_limited','status__in=','other_status__in='),
            ('check_if_api_call_is_rate_limited','APICallStatusChoices.SUCCESS.value','APICallStatusChoices.ERROR.value'),
            ('refund_cost_for_api_call','api_call_log_row.deducted_cost <= 0','api_call_log_row.deducted_cost > 0'),
            ('refund_cost_for_api_call','not api_call_log_row or api_call_log_row.deducted_cost <= 0',
                repr('api_call_log_row.deducted_cost <= 0')),
            ('refund_cost_for_api_call','return None','pass')]
        for name,old,new in edits:
            self.assertIn(old,definitions[name])
            changed=dict(definitions); changed[name]=changed[name].replace(old,new)
            with self.subTest(definition=name,change=new),self.assertRaises(AssertionError):
                self.assert_usage_entries_contract(assemble(changed.values()),names)

    def test_schema_physical_fk_unique_and_source_drift_refuse(self):
        env,columns,constraints=schema_env(); exec(u.SCHEMA_CHECK,env)
        edits=[lambda e,c,k:setattr(e['connection'],'vendor','sqlite'),
            lambda e,c,k:c[TABLE].pop(), lambda e,c,k:setattr(c[TABLE][0],'null_ok',True),
            lambda e,c,k:k[TABLE].pop('unique_log_id'),lambda e,c,k:k[TABLE].pop('fk_api_call_type_id'),
            lambda e,c,k:setattr(e['Log']._meta.get_field('api_call_type'),'db_constraint',False),
            lambda e,c,k:setattr(e['Log']._meta.get_field('cost'),'decimal_places',2),
            lambda e,c,k:setattr(e['Log']._meta.get_field('log_id'),'unique',False),
            lambda e,c,k:k[TABLE].pop('positive_token'),
            lambda e,c,k:setattr(e['CallType']._meta.get_field('name'),'flatchoices',[('jev_evaluator','Jev')])]
        for edit in edits:
            env,columns,constraints=schema_env(); edit(env,columns,constraints)
            with self.assertRaises(AssertionError): exec(u.SCHEMA_CHECK,env)

    def test_empty_log_table_and_replay_do_not_need_a_sample_or_change_unrelated_rows(self):
        env=orm_env()
        self.assertEqual(execute(env,u.CHUNK_BODY),{'skipped':False})
        self.assertEqual(len(env['Log'].records),2500)
        snapshot=copy.deepcopy(env['Log'].records)
        env['Log'].records[1000000]=dict(snapshot[1],id=1000000,log_id=uuid.uuid4(),source_id='unrelated')
        unrelated=copy.deepcopy(env['Log'].records[1000000])
        self.assertEqual(execute(env,u.CHUNK_BODY),{'skipped':True})
        self.assertEqual(env['Log'].records[1000000],unrelated)
        for pk,row in snapshot.items(): self.assertEqual(env['Log'].records[pk],row)
        self.assertEqual(env['financial_snapshot'].call_count,4)

    def test_atomic_rollback_partial_and_every_source_field_conflict(self):
        for field in ('fail_insert','fail_update'):
            env=orm_env(); setattr(env['Log'],field,True)
            with self.assertRaises(RuntimeError): execute(env,u.CHUNK_BODY)
            self.assertFalse(env['Log'].records)
        env=orm_env(); env['financial_snapshot'].side_effect=[{'balance':'before'},{'balance':'after'}]
        with self.assertRaisesRegex(AssertionError,'financial baseline'): execute(env,u.CHUNK_BODY)
        self.assertFalse(env['Log'].records)
        env=orm_env(); env['check_no_events'].side_effect=AssertionError('unexpected usage event')
        with self.assertRaisesRegex(AssertionError,'unexpected usage event'): execute(env,u.CHUNK_BODY)
        self.assertFalse(env['Log'].records)
        env=orm_env(); execute(env,u.CHUNK_BODY); del env['Log'].records[1]
        with self.assertRaisesRegex(AssertionError,'partial atomic chunk'): execute(env,u.CHUNK_BODY)
        env=orm_env(); execute(env,u.CHUNK_BODY)
        fields=list(env['Log'].records[1])
        for field in fields:
            if field=='id': continue  # checked by the retained PK digest, below
            original=env['Log'].records[1][field]
            env['Log'].records[1][field]='conflict'
            with self.subTest(field=field),self.assertRaises(AssertionError): execute(env,u.CHUNK_BODY)
            env['Log'].records[1][field]=original
        for start,stop in ((1,2501),(0,5000),(0,1000),(100000,102500)):
            env['a'].update(start=start,stop=stop)
            with self.assertRaises(AssertionError): execute(env,u.CHUNK_BODY)

    def test_lookup_existing_missing_conflicting_and_atomic_preparation(self):
        env=orm_env(); existing=copy.deepcopy(env['CallType'].records)
        execute(env,u.PREPARE_BODY); self.assertEqual(env['CallType'].records,existing)
        env=orm_env(); env['CallType'].records={}
        result=execute(env,u.PREPARE_BODY)
        self.assertEqual(set(result['context']['call_types']),set(u.CALL_TYPES))
        self.assertEqual(len(env['CallType'].records),4)
        snapshot=copy.deepcopy(env['CallType'].records)
        execute(env,u.PREPARE_BODY); self.assertEqual(env['CallType'].records,snapshot)
        for field,value in [('description','foreign payload'),('deleted',True),('deleted_at',datetime.now(timezone.utc))]:
            env=orm_env(); env['CallType'].records[10][field]=value
            before=copy.deepcopy(env['CallType'].records)
            with self.assertRaisesRegex(AssertionError,'lookup conflict'): execute(env,u.PREPARE_BODY)
            self.assertEqual(env['CallType'].records,before)
        env=orm_env(); env['CallType'].records={}
        env['financial_snapshot'].side_effect=[{}, {'unexpected':'effect'}]
        with self.assertRaisesRegex(AssertionError,'financial baseline'): execute(env,u.PREPARE_BODY)
        self.assertFalse(env['CallType'].records)
        env=orm_env(); env['a']['context']['user_id']=str(uuid.UUID(int=999))
        with self.assertRaisesRegex(AssertionError,'owner drift'): execute(env,u.CHUNK_BODY)
        env=orm_env(); del env['CallType'].records[10]; env['CallType'].records[13]['deleted']=True
        before=copy.deepcopy(env['CallType'].records)
        with self.assertRaisesRegex(AssertionError,'lookup conflict'): execute(env,u.PREPARE_BODY)
        self.assertEqual(env['CallType'].records,before)

    def test_owner_lookup_uses_real_org_and_workspace_creator_not_unowned_fks(self):
        env=orm_env()
        node=next(n for n in ast.parse(u.COMMON).body if isinstance(n,ast.FunctionDef) and n.name=='owner_context')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<owner-context>','exec'),env)
        user=SimpleNamespace(id=uuid.UUID(CONTEXT['user_id']))
        workspace=SimpleNamespace(created_by_id=user.id,created_at=datetime(2026,2,5,tzinfo=timezone.utc))
        env['User']=SimpleNamespace(objects=mock.Mock()); env['User'].objects.get.return_value=user
        env['Workspace']=SimpleNamespace(all_objects=mock.Mock()); env['Workspace'].all_objects.get.return_value=workspace
        self.assertEqual(env['owner_context']()['user_id'],CONTEXT['user_id'])
        env['User'].objects.get.assert_called_once_with(email=ARGS['email'],organization_id=ARGS['organization_id'],is_active=True)
        env['Workspace'].all_objects.get.assert_called_once_with(id=ARGS['workspace_id'],organization_id=ARGS['organization_id'],deleted=False,is_active=True)
        workspace.created_by_id=uuid.UUID(int=999)
        with self.assertRaisesRegex(AssertionError,'account/workspace mismatch'): env['owner_context']()
        self.assertIn('get_current_workspace() is None and get_current_organization() is None',u.COMMON)

    def test_reference_completed_before_independent_physical_measurement(self):
        fake=FakeApp(); driver=driver_for(fake,q.QualificationRehearsal)
        def measure(entry):
            self.assertEqual(fake.calls[-1][0],s.DJANGO_PREFIX+u.VERIFY)
            self.assertEqual(len(fake.committed),40); self.assertTrue(hasattr(driver,'_usage_reference'))
            return dict(engine='postgres',table='public.'+TABLE,rows=123456,bytes=7654321)
        result=p.seed_reference_tables([ENTRY],driver.seed_reference_table,measure)
        self.assertEqual((result[0]['rows'],result[0]['bytes']),(123456,7654321))
        self.assertEqual([program for program,args in fake.calls],
            [s.DJANGO_PREFIX+u.PREPARE]+[s.DJANGO_PREFIX+u.CHUNK]*40+[s.DJANGO_PREFIX+u.VERIFY])
        self.assertEqual(fake.calls[0][1],ARGS)
        for secret in ('Authorization','SECRET','password','DO-NOT-FORWARD'):
            self.assertNotIn(secret,json.dumps([args for program,args in fake.calls]))
        self.assertIn('SELECT count(*)',u.COMMON)
        self.assertIn('pg_total_relation_size',Path(s.__file__).read_text())

    def test_interruption_resume_and_pending_preservation_refusal(self):
        for fresh in (False,True):
            fake=FakeApp(); fake.fail_at=5000; driver=driver_for(fake)
            with self.assertRaises(RuntimeError): driver.seed_usage_reference(100000)
            with self.assertRaises(p.ManualRelease): u.verify_usage_reference(driver,prefix=s.DJANGO_PREFIX,producer=p)
            if fresh: driver=driver_for(fake)
            driver.seed_usage_reference(100000)
            self.assertEqual(driver._usage_reference['baseline'],11); self.assertEqual(fake.skipped,[0,2500])
            before=len(fake.calls); driver.seed_usage_reference(100000)
            self.assertEqual(len(fake.calls),before+1)
            with self.assertRaises(p.ManualRelease): driver.seed_usage_reference(100001)
        for rows in (True,99999,0,100000001,100000.0):
            driver=driver_for()
            with self.assertRaises(p.ManualRelease): driver.seed_usage_reference(rows)
            self.assertFalse(driver.app_python.calls)
        fake=FakeApp(scale=2); driver=driver_for(fake); driver.seed_usage_reference(100001)
        self.assertEqual(len(fake.committed),80)

    def test_failed_completion_never_reaches_measurement_or_finishes_state(self):
        for alter in (lambda r:dict(r,seeded_counts={TABLE:99999}),lambda r:dict(r,cohorts={}),
                lambda r:dict(r,deleted=True),lambda r:dict(r,identity_sha256='not-a-digest')):
            fake=FakeApp(); fake.alter_verified=alter; driver=driver_for(fake)
            measure=mock.Mock()
            with self.assertRaises(p.ManualRelease): p.seed_reference_tables([ENTRY],driver.seed_reference_table,measure)
            measure.assert_not_called(); self.assertFalse(hasattr(driver,'_usage_reference'))
            self.assertFalse(driver.corpus)
        driver=driver_for(); del driver.witnesses.namespaces['source']['account']
        with self.assertRaises(p.ManualRelease): driver.seed_usage_reference(100000)
        self.assertFalse(driver.app_python.calls)

    def test_full_read_only_preservation_and_pk_digest_refuse_loss(self):
        fake=FakeApp(); driver=driver_for(fake)
        u.verify_usage_reference(driver,prefix=s.DJANGO_PREFIX,producer=p); self.assertFalse(fake.calls)
        driver.seed_usage_reference(100000); before=len(fake.calls)
        for phase in ('before backup','on B','after restore'):
            u.verify_usage_reference(driver,prefix=s.DJANGO_PREFIX,producer=p)
        self.assertEqual([program for program,args in fake.calls[before:]],[s.DJANGO_PREFIX+u.VERIFY]*3)
        self.assertEqual(driver.corpus['usage_reference_preservation_reads'],3)
        for alter in (lambda r:dict(r,identity_sha256='b'*64),lambda r:dict(r,deleted=9999),
                lambda r:dict(r,counts={TABLE:100000}),lambda r:dict(r,seeded_counts={TABLE:99999}),
                lambda r:dict(r,cohorts=dict(u.COHORTS,error=True))):
            fake.alter_verified=alter
            with self.assertRaises(p.ManualRelease): u.verify_usage_reference(driver,prefix=s.DJANGO_PREFIX,producer=p)
        env=orm_env(); execute(env,u.CHUNK_BODY); rows=env['payloads'](CONTEXT,0,2500)
        before=hashlib.sha256(); env['identity_digest'](rows,before)
        env['Log'].records[1]['id']=999999
        after=hashlib.sha256(); env['identity_digest'](rows,after)
        self.assertNotEqual(before.hexdigest(),after.hexdigest())
        methods={n.func.attr for n in ast.walk(ast.parse(u.COMMON+u.VERIFY_BODY)) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Attribute) and not (isinstance(n.func.value,ast.Name) and n.func.value.id=='digest')}
        self.assertFalse(methods & {'create','bulk_create','bulk_update','update','delete','save'})

    def test_verifier_streams_all_bounded_chunks_and_source_hook_is_before_api(self):
        env=orm_env(); env['matching']=mock.Mock(); env['identity_digest']=mock.Mock()
        env['payloads']=mock.Mock(return_value=[])
        execute(env,u.VERIFY_BODY)
        self.assertEqual([call.args[1:] for call in env['payloads'].call_args_list],[(start,start+2500) for start in range(0,100000,2500)])
        self.assertEqual(env['matching'].call_count,40)
        witness=object.__new__(s.ProductWitnesses); witness.d=driver_for()
        witness.namespaces={'source':dict(headers={},account={'email':ARGS['email']}),'target':dict(headers={})}
        witness._http=mock.Mock(side_effect=RuntimeError('API boundary'))
        refusal=p.ManualRelease('high_volume_reference_unavailable')
        with mock.patch.object(s,'verify_usage_reference',side_effect=refusal) as verify:
            with self.assertRaises(p.ManualRelease): witness.read('source')
            witness._http.assert_not_called(); verify.assert_called_once_with(witness.d,prefix=s.DJANGO_PREFIX,producer=p)
            with self.assertRaisesRegex(RuntimeError,'API boundary'): witness.read('target')
            self.assertEqual(verify.call_count,1)

    def test_exact_dispatch_closed_diagnostics_and_generic_guards_remain(self):
        for cls in (s.ShippingRehearsal,q.QualificationRehearsal):
            driver=driver_for(cls=cls); driver.seed_usage_reference=mock.Mock(side_effect=p.ManualRelease('high_volume_reference_unavailable'))
            driver.app_python=mock.Mock(); driver._diagnostic_data={'qualification_evidence':False}
            with self.assertRaises(p.ManualRelease):
                driver.seed_reference_table(dict(ENTRY,password='SECRET',organization_id='PRIVATE',config={'payload':'PRIVATE'}),100000)
            self.assertEqual(driver._diagnostic_data['last_reference'],dict(database='postgres',app_label='usage',model='APICallLog',table=TABLE,requested_rows=100000))
            self.assertNotIn('PRIVATE',json.dumps(driver._diagnostic_data)); self.assertNotIn('SECRET',json.dumps(driver._diagnostic_data))
            self.assertLess(len(json.dumps(driver._diagnostic_data)),512)
            for key,value in [('database','clickhouse'),('app_label','other'),('model','Other'),('table','usage_api_call_log'),('table','public.'+TABLE),('table','SECRET\n'+'x'*10000)]:
                with self.assertRaises(p.ManualRelease): driver.seed_reference_table(dict(ENTRY,**{key:value}),100000)
                self.assertIsNone(driver._diagnostic_data['last_reference'])
            with self.assertRaises(p.ManualRelease): driver.seed_reference_table(ENTRY,10**100)
            self.assertIsNone(driver._diagnostic_data['last_reference']['requested_rows'])
            driver.app_python.assert_not_called()
        self.assertNotIn(('usage','APICallLog'),s.APPEND_ONLY_MODELS)
        self.assertIn('assert not field.unique or field.primary_key',s.REFERENCE_SEED)
        self.assertIn('assert not m._meta.unique_together',s.REFERENCE_SEED)
        for forbidden in ('log_api_call','refund_api_call','create_usage_entries','insert_pricing_entries','disable_trigger','session_replication_role'):
            self.assertNotIn(forbidden,u.PREPARE+u.CHUNK+u.VERIFY)

    def test_real_financial_snapshot_queries_are_bounded_owner_scoped_and_no_events_check_is_attributable(self):
        env=orm_env()
        functions=ast.Module(body=[n for n in ast.parse(u.COMMON).body if isinstance(n,ast.FunctionDef) and n.name in ('financial_snapshot','check_no_events')],type_ignores=[])
        exec(compile(functions,'<financial-functions>','exec'),env)
        class Q:
            def __init__(self, **kw): self.parts=[kw]
            def __or__(self, other):
                result=Q(); result.parts=self.parts+other.parts; return result
        managers={}
        def get_model(app,name):
            if name not in managers:
                query=mock.MagicMock(); query.filter.return_value=query; query.order_by.return_value=query
                query.values.return_value=[{'id':1,'balance':'unchanged'}]; query.exists.return_value=False
                managers[name]=query
            return SimpleNamespace(all_objects=managers[name],objects=managers[name],_default_manager=managers[name])
        env.update(Q=Q,apps=SimpleNamespace(get_model=get_model),DjangoJSONEncoder=json.JSONEncoder)
        before=env['financial_snapshot']()
        self.assertEqual(len(before),13)
        for name,manager in managers.items():
            call=manager.filter.call_args
            if name=='InvoiceLineItem': self.assertEqual(call.kwargs,{'invoice__organization_id':ARGS['organization_id']})
            else: self.assertEqual(call.args[0].parts[0],{'organization_id':ARGS['organization_id']})
        managers['CreditBalance'].values.return_value=[{'id':1,'balance':'changed'}]
        self.assertNotEqual(env['financial_snapshot'](),before)
        managers['UsageSummary'].values.return_value=[{}]*4097
        with self.assertRaisesRegex(AssertionError,'unbounded'): env['financial_snapshot']()
        env['check_no_events']()
        event=managers['UsageEventLog']
        self.assertEqual(event.filter.call_args.kwargs,{'properties__source_id':env['SOURCE_ID']})
        event.exists.return_value=True
        with self.assertRaisesRegex(AssertionError,'unexpected usage event'): env['check_no_events']()
