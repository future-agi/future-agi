"""Complete 21/26-pending-migration A9 intersection regression, without Django.

Run the shipped touched()/inbound() resolver against AST-derived operation and
ModelState doubles. No migration module, RunPython, SQL or app is executed.
This guards the prior incomplete hand inventory, not a substitute for the
native B MigrationLoader scope probe or PostgreSQL constraint observation.
"""
import ast
import copy
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import unittest

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_qualification as q
from deploy.tests.test_update_usage_reference import ROOT, REVISIONS, source

LATEST = (
    ('accounts','0029_demotracepublication_demoseedrequest'),
    ('accounts','0030_queue_existing_demo_trace_publications'),
    ('accounts','0031_orgapikey_coding_agent'),
    ('usage','0030_add_jev_evaluator_call_type'),
    ('tracer','0116_insight_reports'),
    ('tracer','0117_instant_eval_scale'),
    ('tracer','0118_instant_eval_answers'),
    ('tracer','0119_instant_eval_scope_preset'),
    ('tracer','0120_merge_error_feed_alerts_and_instant_eval'),
    ('tracer','0121_remove_insightconfig_schedule'),
    ('tracer','0122_merge_instant_eval_and_simulation_project'),
    ('tracer','0123_merge_instant_eval_and_shared_link_resource'),
    ('tracer','0124_merge_analysis_and_alert_high_water'),
    ('tracer','0125_insightrun_source_started_at'),
    ('tracer','0126_analysis_source_arrival'),
    ('tracer','0127_project_analysis_source_generation'),
    ('tracer','0128_instantevalrow_source_session_ids'),
    ('tracer','0129_project_analysis_source_generation_db_default'),
    ('tracer','0130_observationspan_internal_type'),
    ('simulate','0102_agent_fix_engine_notes'),
    ('simulate','0103_harness_job_verify_of'),
)
FLOOR_ONLY = (
    ('simulate','0099_harness_versioning'),('simulate','0100_agent_fix'),
    ('simulate','0101_agent_fix_candidate_override'),
    ('tracer','0119_retire_deleted_eval_memberships'),
    ('tracer','0120_alert_issue_state_occurrences_high_water'),
)


def keywords(node): return {k.arg:k.value for k in node.keywords}


class StockCharField(SimpleNamespace):
    """Literal AST shape only; native Django/PG tests prove database behavior."""
    def deconstruct(self):
        return None, 'django.db.models.CharField', copy.deepcopy(self.args), copy.deepcopy(self.kwargs)


def field(node):
    assert isinstance(node,ast.Call)
    options=keywords(node)
    kind=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id
    remote=None
    if kind in ('ForeignKey','OneToOneField','ManyToManyField'):
        target=options.get('to') or (node.args[0] if node.args else None)
        assert target is not None
        if isinstance(target,ast.Attribute):
            assert ast.unparse(target)=='settings.AUTH_USER_MODEL'
            target='accounts.user'
        else: target=ast.literal_eval(target)
        remote=SimpleNamespace(model=target,through=ast.literal_eval(options['through']) if 'through' in options else None)
    attributes=dict(remote_field=remote,many_to_many=kind=='ManyToManyField',
        primary_key=ast.literal_eval(options['primary_key']) if 'primary_key' in options else False,
        unique=ast.literal_eval(options['unique']) if 'unique' in options else kind=='OneToOneField',
        db_table=ast.literal_eval(options['db_table']) if 'db_table' in options else None)
    if kind=='CharField':
        try:
            arguments=[ast.literal_eval(value) for value in node.args]
            parameters={name:ast.literal_eval(value) for name,value in options.items()}
        except (ValueError, TypeError):
            return SimpleNamespace(**attributes)  # unresolved declarations stay conservative
        return StockCharField(args=arguments,kwargs=parameters,**attributes)
    return SimpleNamespace(**attributes)


def model(node):
    options=keywords(node)
    declared={ast.literal_eval(k):v for k,v in zip(options.get('options',ast.Dict(keys=[],values=[])).keys,
        options.get('options',ast.Dict(keys=[],values=[])).values)}
    return SimpleNamespace(options=({'db_table':ast.literal_eval(declared['db_table'])} if 'db_table' in declared else {}),
        fields={ast.literal_eval(pair.elts[0]):field(pair.elts[1]) for pair in options['fields'].elts})


class State:
    def __init__(self): self.models={}
    def clone(self): return copy.deepcopy(self)


class Operation:
    def __init__(self,node):
        self.node=node
        options=keywords(node)
        for key in ('name','model_name','old_name','new_name'):
            if key in options: setattr(self,key,ast.literal_eval(options[key]))
        if 'field' in options: self.field=field(options['field'])
        if type(self).__name__=='AlterField':
            self.preserve_default=ast.literal_eval(options['preserve_default']) if 'preserve_default' in options else True
        if type(self).__name__=='RunPython': self.code=object()  # opaque; never called
        if type(self).__name__=='RunSQL': self.sql='opaque'  # only declaration is read
    def state_forwards(self,app,state):
        kind=type(self).__name__
        if kind=='CreateModel': state.models[(app,self.name.lower())]=model(self.node)
        elif kind in ('AddField','AlterField'): state.models[(app,self.model_name)].fields[self.name]=self.field
        elif kind=='RemoveField': state.models[(app,self.model_name)].fields.pop(self.name,None)
        elif kind=='RenameField':
            fields=state.models[(app,self.model_name)].fields
            fields[self.new_name]=fields.pop(self.old_name)
        else:
            # All remaining operations in these COMPLETE plans affect catalog
            # indexes/constraints or carry declarations; no state table rename.
            assert kind in ('RunPython','RunSQL','AddIndex','RemoveIndex','AddConstraint')


def operation_types():
    kinds=('CreateModel','AddField','AlterField','RemoveField','RenameField','AddIndex',
        'RemoveIndex','AddConstraint','RunPython','RunSQL','SeparateDatabaseAndState')
    result={kind:type(kind,(Operation,),{'__module__':'django.db.migrations.operations.fixtures'}) for kind in kinds}
    result['RunPython'].noop=object(); result['RunSQL'].noop=''
    return SimpleNamespace(**result)


def scope_environment(state):
    def resolve(remote,app,model_name):
        if remote=='self': return app,model_name.lower()
        if '.' in remote:
            app,remote=remote.split('.',1)
        return app,remote.lower()
    env=dict(ast=ast,re=re,migrations=operation_types(),models=SimpleNamespace(CharField=StockCharField),schemas={'postgres':'public','clickhouse':'default'},
        resolve_relation=resolve,strip_quotes=lambda s:s.replace('"',''),truncate_name=lambda s,length:s[:length])
    # Use the actual resolver's functions/constants, with imports and final
    # loader invocation left to the native test. Opaque declarations are AST data.
    nodes=[n for n in ast.parse(p.PENDING_TABLE_SCOPE).body if
        isinstance(n,ast.FunctionDef) and n.name!='declared' or
        isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('IDENT','NO_TABLE','MODEL_OPS') for t in n.targets)]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<shipping-table-scope>','exec'),env)
    env['declared']=lambda migration:{env['resolved'](*v.split(':',1)) for v in migration.declaration['tables']}
    return env


def migration_path(app,name):
    return ROOT/'futureagi'/('ee/usage' if app=='usage' else app)/'migrations'/(name+'.py')


def baseline_state(revision):
    state=State()
    # Model table options/initial fields are read from real CreateModel ASTs.
    # Only usage's pre-AlterField inbound edges/uniqueness are relevant among
    # baseline fields in these plans; replace them with exact source model ASTs
    # below rather than deriving that key fact from the target's added choices.
    for path in sorted((ROOT/'futureagi').glob('**/migrations/*.py')):
        app=path.parent.parent.name
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and
                    isinstance(node.func.value,ast.Name) and node.func.value.id=='migrations' and node.func.attr=='CreateModel'): continue
            options=keywords(node)
            if 'name' not in options or 'fields' not in options: continue
            state.models[(app,ast.literal_eval(options['name']).lower())]=model(node)
    usage=ast.parse(source(revision,'ee/usage/models/usage.py'))
    for node in usage.body:
        if not isinstance(node,ast.ClassDef): continue
        fields={}
        for item in node.body:
            if not (isinstance(item,ast.Assign) and isinstance(item.value,ast.Call) and
                    isinstance(item.value.func,ast.Attribute) and isinstance(item.value.func.value,ast.Name)
                    and item.value.func.value.id=='models'): continue
            call=copy.deepcopy(item.value)
            # Source model imports are classes; normalize only real FK targets.
            if call.func.attr in ('ForeignKey','OneToOneField') and isinstance(call.args[0],ast.Name):
                name=call.args[0].id
                call.args[0]=ast.Constant(('accounts.' if name in ('Organization','Workspace','User') else 'usage.')+name.lower())
            fields[item.targets[0].id]=field(call)
        if fields:
            state.models[('usage',node.name.lower())]=SimpleNamespace(options={},fields=fields)
    # Pin the actual source field before choices-only tracer0130; CreateModel
    # alone predates intervening AlterFields and cannot describe this boundary.
    previous=ast.parse(source(revision,'tracer/migrations/0026_alter_observationspan_observation_type.py'))
    operation=next(node for node in ast.walk(previous) if isinstance(node,ast.Call)
        and isinstance(node.func,ast.Attribute) and node.func.attr=='AlterField'
        and ast.literal_eval(keywords(node)['name'])=='observation_type')
    state.models[('tracer','observationspan')].fields['observation_type']=field(keywords(operation)['field'])
    previous_usage=ast.parse(source(revision,'ee/usage/migrations/0021_add_code_evaluator_choice.py'))
    usage_operation=next(node for node in ast.walk(previous_usage) if isinstance(node,ast.Call)
        and isinstance(node.func,ast.Attribute) and node.func.attr=='AlterField'
        and ast.literal_eval(keywords(node)['name'])=='name')
    state.models[('usage','apicalltype')].fields['name']=field(keywords(usage_operation)['field'])
    return state


def pending_plan(identities,env):
    migrations={}
    for app,name in identities:
        tree=ast.parse(migration_path(app,name).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Migration')
        values={n.targets[0].id:n.value for n in cls.body if isinstance(n,ast.Assign)}
        dependencies=[ast.literal_eval(n) for n in values['dependencies'].elts if isinstance(n,ast.Tuple)]
        operations=[getattr(env['migrations'],node.func.attr)(node) for node in values['operations'].elts]
        declaration=next((ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and
            any(isinstance(t,ast.Name) and t.id=='UPDATE_SAFETY' for t in n.targets)),None)
        migrations[(app,name)]=SimpleNamespace(app_label=app,name=name,operations=operations,dependencies=dependencies,declaration=declaration)
    ordered=[]
    while migrations:
        ready=sorted(key for key,m in migrations.items() if not set(m.dependencies)&set(migrations))
        assert ready, 'cyclic pending fixture'
        for key in ready: ordered.append(migrations.pop(key))
    return ordered


class CompleteUsagePendingIntersectionTests(unittest.TestCase):
    def test_complete_latest_and_floor_including_unique_lookup_inbound_edges(self):
        registry=p.load_high_volume_registry()
        for revision,identities,count in ((REVISIONS[1],LATEST,21),(REVISIONS[0],LATEST+FLOOR_ONLY,26)):
            with self.subTest(revision=revision):
                # Additions, not edited applied migrations. This deliberately
                # breaks if the shipping plan changes without refreshing coverage.
                paths=subprocess.check_output(['git','-C',str(ROOT),'diff','--name-only','--diff-filter=A',
                    revision,'HEAD','--','futureagi/**/migrations/*.py'],text=True).splitlines()
                actual={(Path(path).parent.parent.name,Path(path).stem) for path in paths}
                self.assertEqual(actual,set(identities)); self.assertEqual(len(actual),count)
                state=baseline_state(revision); env=scope_environment(state)
                plan=pending_plan(identities,env); scopes=[]
                for migration in plan:
                    tables=set()
                    for op in migration.operations:
                        tables |= env['touched'](op,migration,state)
                        op.state_forwards(migration.app_label,state)
                    scopes.append(dict(app_label=migration.app_label,name=migration.name,
                        tables=[dict(engine=e,table=t) for e,t in sorted(tables)]))
                identities_in_order=[dict(app_label=m.app_label,name=m.name) for m in plan]
                selected=p.select_reference_registry(registry,scopes,plan=identities_in_order)
                expected={'tracer_trace','tracer_observation_span','tracer_eval_task'}
                if count==26: expected|={'tracer_error_cluster_traces','tracer_eval_logger','tracer_trace_error_group'}
                self.assertEqual({row['table'] for row in selected},expected)
                if count==21:
                    references=[dict(engine=row['database'],table='public.'+row['table']) for row in selected]
                    certified=q.reference_scope(identities_in_order,scopes,references)
                    self.assertEqual(len(certified),21)
                    physical=[(table['engine'],table['table']) for scope in certified for table in scope['tables']]
                    self.assertEqual(len(physical),len(set(physical)))
                    self.assertEqual(len(physical),3)
                self.assertTrue(all(row['database']=='postgres' for row in selected))
                self.assertFalse(any(row.get('app_label')=='prism' or row.get('model') in ('UsageEventLog','UsageSummary') for row in selected))
                usage=next(row for row in scopes if row['app_label']=='usage')
                self.assertEqual(usage['tables'],[])
                for row in scopes:
                    scoped=p.select_reference_registry(registry,[row],plan=[{k:row[k] for k in ('app_label','name')}])
                    names={item['table'] for item in scoped}
                    if row['name']=='0030_queue_existing_demo_trace_publications': expected_row={'tracer_trace','tracer_observation_span'}
                    elif row['app_label']=='usage': expected_row=set()
                    elif row['name']=='0118_instant_eval_answers': expected_row={'tracer_eval_task'}
                    elif row['name']=='0130_observationspan_internal_type': expected_row=set()
                    elif row['name']=='0119_retire_deleted_eval_memberships': expected_row={'tracer_error_cluster_traces','tracer_eval_logger','tracer_trace_error_group'}
                    else: expected_row=set()
                    self.assertEqual(names,expected_row,(row['app_label'],row['name']))

    def test_unique_name_preserves_inbound_scope_for_a_physical_change(self):
        state=baseline_state(REVISIONS[1]); env=scope_environment(state)
        migration=pending_plan([('usage','0030_add_jev_evaluator_call_type')],env)[0]
        op=migration.operations[0]
        self.assertEqual(env['touched'](op,migration,state),set())
        # The real choices change is state-only. A wider unique column is a
        # physical change whose inbound log/pricing/rate-limit tables still count.
        op.field.kwargs['max_length']=60
        self.assertFalse(op.field.primary_key); self.assertTrue(op.field.unique)
        self.assertIn(('postgres','public.usage_apicalllog'),env['touched'](op,migration,state))
        # Negative control removes BOTH before/after unique flags. The actual
        # source and target keep them; do not "fix" production by changing this.
        state.models[('usage','apicalltype')].fields['name'].unique=False
        op.field.unique=False
        self.assertEqual(env['touched'](op,migration,state),{('postgres','public.usage_apicalltype')})
