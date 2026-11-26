"""Pending-migration A9 scope regressions for the supported releases, without Django.

Run the shipped touched()/inbound() resolver against AST-derived operation and
ModelState doubles. No migration module, RunPython, SQL or app is executed.
Not a substitute for the native B MigrationLoader scope probe or PostgreSQL
constraint observation.

The pending inventory is every migration git reports as added since each
supported release; no hand-kept list restates it. Each pending migration's
high-volume scope must match a declaration in its own file (UPDATE_SAFETY
tables, or UPDATE_SCOPE), so a migration that reaches a high-volume table
cannot merge unannounced. Migrations merged before declarations existed keep
their reviewed scopes in PINNED_SCOPES. Opaque operations without a
declaration still fail closed.
"""
import ast
import copy
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import rehearse_update_compatibility as p
from deploy.tests.test_update_usage_reference import ROOT, REVISIONS, source

# Eight SQL additions shared with the SQL-only candidate. These identities
# account for the graph; they are not safety declarations for opaque operations.
SQL_ADDITIONS = (
    ('tracer','0117_sql_workbench'),
    ('tracer','0118_merge_sql_workbench_and_simulation_project'),
    ('tracer','0119_sql_execution_result_reuse'),
    ('tracer','0120_merge_sql_execution_and_shared_issue'),
    ('tracer','0121_merge_sql_execution_and_alert_high_water'),
    ('tracer','0122_sql_payload_expiry_index'),
    ('tracer','0130_merge_20261104_0940'),
    ('tracer','0131_merge_sql_workbench_and_observationspan_internal_type'),
)
# Ten more identities on the sampling layer, including its graph joins.
SAMPLING_ADDITIONS = (
    ('tracer','0117_cohort_sampling'),
    ('tracer','0117_sampling_project_db_default'),
    ('tracer','0119_merge_sampling_default_and_simulation_project'),
    ('tracer','0120_sampling_cohort_lease'),
    ('tracer','0121_merge_sampling_and_sql_reuse'),
    ('tracer','0122_merge_sampling_and_alert_issue_state'),
    ('tracer','0123_merge_20261104_0743'),
    ('tracer','0131_merge_20261104_1308'),
    ('tracer','0132_merge_sampling_and_sql_workbench_dev_merge'),
    ('tracer','0133_merge_sampling_and_alert_webhook'),
)
# Metrics identities and the new graph join; these are not UPDATE_SAFETY claims.
METRICS_ADDITIONS = (
    ('tracer','0117_derived_metrics'),
    ('tracer','0118_derived_metric_delivery'),
    ('tracer','0119_derived_metric_requester'),
    ('tracer','0120_derived_metric_delivery_ledger'),
    ('tracer','0121_merge_sampling_project_default'),
    ('tracer','0122_merge_metrics_and_simulation_project'),
    ('tracer','0123_derived_metric_confident_alert'),
    ('tracer','0124_merge_metrics_and_sampling_sql'),
    ('tracer','0125_merge_metrics_and_dev_tracer_leaf'),
    ('tracer','0126_derived_metric_refresh'),
    ('tracer','0127_metric_lifecycle_safety'),
    ('tracer','0128_base_list_current_stores'),
    ('tracer','0129_metric_compaction_range'),
    ('tracer','0129_metric_pg_response_revisions'),
    ('tracer','0129_metric_source_intent'),
    ('tracer','0129_user_activity_store'),
    ('tracer','0130_merge_session_components'),
    ('tracer','0131_metric_range_user_activity_choice'),
    ('tracer','0132_merge_metrics_b1_dev'),
    ('tracer','0133_metric_compaction_user_activity_choice'),
    ('tracer','0134_metric_compaction_user_activity_constraint'),
    ('tracer','0135_merge_metrics_b1_assembly'),
    ('tracer','0136_merge_metrics_and_current_sampling_sql'),
    ('tracer','0137_metric_version_history_mode'),
    ('tracer','0138_metric_refresh_pass_capture_budget'),
    ('tracer','0139_metric_attempt_rows_collected_at'),
    ('tracer','0140_metric_refresh_pause'),
    ('tracer','0141_metric_refresh_busy_guard'),
    ('tracer','0142_merge_metrics_and_eval_put_back'),
)
# Error Feed escalation (TH-8613) and its joins with dev's tracer leaves.
FEED_ESCALATION_ADDITIONS = (
    ('tracer','0117_feed_escalation_policy'),
    ('tracer','0118_backfill_feed_issue_statuses'),
    ('tracer','0131_merge_feed_escalation_and_internal_span_type'),
    ('tracer','0143_merge_feed_escalation_and_metrics'),
)
FLOOR_ONLY = (
    ('simulate','0099_harness_versioning'),('simulate','0100_agent_fix'),
    ('simulate','0101_agent_fix_candidate_override'),
    ('tracer','0119_retire_deleted_eval_memberships'),
    ('tracer','0120_alert_issue_state_occurrences_high_water'),
)
# Reviewed high-volume scopes of migrations that merged before UPDATE_SCOPE
# existed. The gate refuses edits to a merged migration, so these cannot carry
# their own declaration. Every other pending migration declares in its file.
# Identities are (migration directory, name): agentcc/ runs as app "prism".
PINNED_SCOPES = {
    ('accounts','0030_queue_existing_demo_trace_publications'): {'tracer_trace','tracer_observation_span'},
    ('usage','0030_add_jev_evaluator_call_type'): {'usage_apicalllog'},
    ('tracer','0117_cohort_sampling'): {'tracer_eval_task'},
    ('tracer','0118_instant_eval_answers'): {'tracer_eval_task'},
    ('tracer','0130_observationspan_internal_type'): set(),  # Choices-only state change.
    ('tracer','0119_retire_deleted_eval_memberships'): {'tracer_error_cluster_traces','tracer_eval_logger',
        'tracer_trace_error_group'},
    ('simulate','0116_agent_fix_issue_job'): {'tracer_trace_error_group'},  # Relation to the source issue.
    ('tracer','0117_feed_escalation_policy'): {'tracer_error_cluster_traces','tracer_trace_error_group'},
    ('tracer','0118_backfill_feed_issue_statuses'): {'tracer_error_cluster_traces',
        'tracer_trace_investigation_finding','tracer_trace_investigation_job','tracer_trace_investigation_report'},
}
DECLARED_TABLE = re.compile(r'(postgres|clickhouse):[a-z_][a-z0-9_]*')


def git_lines(*args):
    return subprocess.check_output(['git','-C',str(ROOT),*args],text=True).splitlines()


def added_migrations(revision):
    """Migration files added between a supported release and HEAD, in path order."""
    paths=git_lines('diff','--name-only','--diff-filter=A',revision,'HEAD','--','futureagi/**/migrations/*.py')
    return tuple((Path(path).parent.parent.name,Path(path).stem) for path in sorted(paths)
        if not Path(path).name.startswith('__'))


def declared_scope(app,name):
    """(UPDATE_SAFETY tables, UPDATE_SCOPE tables) as engine:table strings, by AST."""
    path=migration_path(app,name)
    safety,scope=set(),set()
    for node in ast.parse(path.read_text()).body:
        targets=[t.id for t in getattr(node,'targets',()) if isinstance(t,ast.Name)]
        if 'UPDATE_SAFETY' in targets:
            safety|=set(ast.literal_eval(node.value)['tables'])
        if 'UPDATE_SCOPE' in targets:
            value=ast.literal_eval(node.value)
            assert type(value) in (tuple,list) and all(type(t) is str and DECLARED_TABLE.fullmatch(t) for t in value), (
                f"{path}: UPDATE_SCOPE must be a literal tuple of 'postgres:<table>' strings")
            scope|=set(value)
    return safety,scope


def keywords(node): return {k.arg:k.value for k in node.keywords}


class CharField(SimpleNamespace):
    """AST-only built-in-field double; constructor equality is not SQL proof."""
    non_db_attrs = ('choices',)

    def deconstruct(self):
        return None, 'django.db.models.CharField', self.arguments, dict(self.options)


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
    cls=CharField if kind=='CharField' else SimpleNamespace
    return cls(remote_field=remote,many_to_many=kind=='ManyToManyField',
        primary_key=ast.literal_eval(options['primary_key']) if 'primary_key' in options else False,
        unique=ast.literal_eval(options['unique']) if 'unique' in options else kind=='OneToOneField',
        db_index=ast.literal_eval(options['db_index']) if 'db_index' in options else False,
        db_table=ast.literal_eval(options['db_table']) if 'db_table' in options else None,
        arguments=[ast.dump(arg) for arg in node.args],
        options={key:ast.dump(value) for key,value in options.items()})


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
        kind=type(self).__name__
        positional = {
            'AddField': ('model_name', 'name', 'field'),
            'AlterField': ('model_name', 'name', 'field'),
            'RemoveField': ('model_name', 'name'),
            'AddConstraint': ('model_name', 'constraint'),
            'RemoveConstraint': ('model_name', 'name'),
        }.get(kind, ())
        for key, value in zip(positional, node.args):
            assert key not in options
            options[key] = value
        for key in ('name','model_name','old_name','new_name'):
            if key in options: setattr(self,key,ast.literal_eval(options[key]))
        if 'field' in options: self.field=field(options['field'])
        if kind=='RunPython':
            code = node.args[0] if node.args else options['code']
            self.code = type(self).noop if ast.unparse(code)=='migrations.RunPython.noop' else object()
        if kind=='RunSQL':
            sql = node.args[0] if node.args else options['sql']
            self.sql = type(self).noop if ast.unparse(sql)=='migrations.RunSQL.noop' else 'opaque'
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
            assert kind in ('RunPython','RunSQL','AddIndex','AddIndexConcurrently','RemoveIndex','AddConstraint',
                'RemoveConstraint')


def operation_types():
    kinds=('CreateModel','AddField','AlterField','RemoveField','RenameField','AddIndex',
        'RemoveIndex','AddConstraint','RemoveConstraint','RunPython','RunSQL','SeparateDatabaseAndState')
    result={kind:type(kind,(Operation,),{'__module__':'django.db.migrations.operations.fixtures'}) for kind in kinds}
    result['AddIndexConcurrently']=type('AddIndexConcurrently',(result['AddIndex'],),
        {'__module__':'django.contrib.postgres.operations'})
    result['RunPython'].noop=object(); result['RunSQL'].noop=''
    return SimpleNamespace(**result)


def scope_environment(state):
    def resolve(remote,app,model_name):
        if remote=='self': return app,model_name.lower()
        if '.' in remote:
            app,remote=remote.split('.',1)
        return app,remote.lower()
    env=dict(ast=ast,re=re,migrations=operation_types(),models=SimpleNamespace(CharField=CharField),
        schemas={'postgres':'public','clickhouse':'default'},
        resolve_relation=resolve,strip_quotes=lambda s:s.replace('"',''),truncate_name=lambda s,length:s[:length])
    # Use the actual resolver's functions/constants, with imports and final
    # loader invocation left to the native test. Opaque declarations are AST data.
    nodes=[n for n in ast.parse(p.PENDING_TABLE_SCOPE).body if
        isinstance(n,ast.FunctionDef) and n.name!='declared' or
        isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('IDENT','NO_TABLE','MODEL_OPS') for t in n.targets)]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<shipping-table-scope>','exec'),env)
    def declared(migration):
        assert migration.declaration is not None, 'opaque_operation_table_scope_unavailable'
        return {env['resolved'](*v.split(':',1)) for v in migration.declaration['tables']}
    env['declared']=declared
    return env


def migration_directories():
    """Tracked migration packages by directory name (ee/usage is "usage")."""
    roots=[Path(path).parent for path in git_lines('ls-files','futureagi/**/migrations/__init__.py')]
    result={root.parent.name:ROOT/root for root in roots}
    assert len(result)==len(roots), 'two migration packages share a directory name'
    return result


MIGRATION_DIRECTORIES=migration_directories()


def migration_path(app,name):
    return MIGRATION_DIRECTORIES[app]/(name+'.py')


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
    # agentcc 0024 builds its RenameModel/AlterModelTable operations in a loop
    # over a literal MODEL_RENAMES table, so no CreateModel names the current
    # Agentcc* models. Re-key them from that literal (AST only, never executed).
    renames=ast.parse(migration_path('agentcc','0024_rename_prism_to_agentcc').read_text())
    renames=next(ast.literal_eval(n.value) for n in renames.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='MODEL_RENAMES' for t in n.targets))
    for old,new,table in renames:
        renamed=state.models.pop(('agentcc',old.lower()))
        renamed.options={**renamed.options,'db_table':table}
        state.models[('agentcc',new.lower())]=renamed
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
    return state


def operation_nodes(nodes):
    """Expand literal operation comprehensions as AST data, never Python code."""
    class Substitute(ast.NodeTransformer):
        def __init__(self, bindings): self.bindings = bindings
        def visit_Name(self, node):
            return copy.deepcopy(self.bindings.get(node.id, node))

    def bind(target, value, bindings):
        if isinstance(target, ast.Name):
            return dict(bindings, **{target.id: value})
        assert isinstance(target, ast.Tuple) and isinstance(value, (ast.Tuple, ast.List))
        assert len(target.elts) == len(value.elts)
        for child, item in zip(target.elts, value.elts):
            bindings = bind(child, item, bindings)
        return bindings

    for node in nodes:
        if not isinstance(node, ast.Starred):
            assert isinstance(node, ast.Call)
            yield node
            continue
        assert isinstance(node.value, ast.ListComp)
        comprehension = node.value
        bindings = [{}]
        for generator in comprehension.generators:
            assert not generator.ifs and not generator.is_async
            expanded = []
            for binding in bindings:
                iterable = Substitute(binding).visit(copy.deepcopy(generator.iter))
                assert isinstance(iterable, (ast.Tuple, ast.List))
                expanded.extend(bind(generator.target, item, binding) for item in iterable.elts)
            bindings = expanded
        for binding in bindings:
            operation = Substitute(binding).visit(copy.deepcopy(comprehension.elt))
            assert isinstance(operation, ast.Call)
            yield operation


def pending_plan(identities,env):
    migrations={}
    for app,name in identities:
        tree=ast.parse(migration_path(app,name).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Migration')
        values={n.targets[0].id:n.value for n in cls.body if isinstance(n,ast.Assign)}
        dependencies=[ast.literal_eval(n) for n in values['dependencies'].elts if isinstance(n,ast.Tuple)]
        operations=[]
        for node in operation_nodes(values['operations'].elts):
            if isinstance(node.func,ast.Name):
                # The SQL migration imports this standard Django operation by
                # name. Do not accept arbitrary custom operations as AddIndex.
                assert node.func.id=='AddIndexConcurrently'
                assert any(isinstance(item,ast.ImportFrom) and item.module=='django.contrib.postgres.operations'
                    and any(alias.name=='AddIndexConcurrently' and alias.asname is None for alias in item.names)
                    for item in tree.body)
                kind=node.func.id
            else:
                assert isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name)
                assert node.func.value.id=='migrations'
                kind=node.func.attr
            operations.append(getattr(env['migrations'],kind)(node))
        declaration=next((ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and
            any(isinstance(t,ast.Name) and t.id=='UPDATE_SAFETY' for t in n.targets)),None)
        migrations[(app,name)]=SimpleNamespace(app_label=app,name=name,operations=operations,dependencies=dependencies,declaration=declaration)
    ordered=[]
    while migrations:
        ready=sorted(key for key,m in migrations.items() if not set(m.dependencies)&set(migrations))
        assert ready, 'cyclic pending fixture'
        for key in ready: ordered.append(migrations.pop(key))
    return ordered


def plan_scopes(revision, identities):
    """Each pending migration's resolved tables, in plan order, as the producer reports them."""
    state=baseline_state(revision); env=scope_environment(state)
    scopes=[]
    for migration in pending_plan(identities,env):
        tables=set()
        for op in migration.operations:
            tables |= env['touched'](op,migration,state)
            op.state_forwards(migration.app_label,state)
        scopes.append(dict(app_label=migration.app_label,name=migration.name,
            tables=[dict(engine=e,table=t) for e,t in sorted(tables)]))
    return scopes


def declared_high_volume_scope(registry, row):
    """The high-volume tables one migration must declare; fails with the fix."""
    key=(row['app_label'],row['name'])
    scoped=p.select_reference_registry(registry,[row],plan=[{'app_label':key[0],'name':key[1]}])
    actual={item['table'] for item in scoped}
    if key in PINNED_SCOPES:
        declared=PINNED_SCOPES[key]
    else:
        high_volume={(item['database'],item['table']) for item in registry}
        safety,scope=declared_scope(*key)
        unknown=sorted(t for t in scope if tuple(t.split(':',1)) not in high_volume)
        assert not unknown, (f'{key[0]}/{key[1]}: UPDATE_SCOPE lists tables that are not in '
            f'deploy/update/high-volume-tables.json: {unknown}')
        declared={t.split(':',1)[1] for t in safety|scope if tuple(t.split(':',1)) in high_volume}
    wanted=', '.join(repr('postgres:'+t) for t in sorted(actual))
    assert actual==declared, (f'{key[0]}/{key[1]}: its high-volume scope is {sorted(actual)} but it declares '
        f'{sorted(declared)}. Declare it in the migration: UPDATE_SCOPE = ({wanted},) at module level, '
        "or UPDATE_SAFETY['tables'] when it carries one. scripts/check_update_safety.py refuses edits "
        'to a merged migration, so pin a merged one in PINNED_SCOPES instead.')
    return declared


PROBE = '''from django.db import migrations, models
import django.db.models.deletion

{declaration}


def forwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('tracer', '0001_initial')]
    operations = [
        {operation},
    ]
'''


class CompleteUsagePendingIntersectionTests(unittest.TestCase):
    def test_pending_inventory_is_derived_from_git(self):
        # Guards the derivation: a shallow clone, a wrong pathspec or a moved
        # release must not shrink the pending plan unnoticed.
        self.assertEqual(len(SQL_ADDITIONS),8)
        self.assertEqual(len(SAMPLING_ADDITIONS),10)
        self.assertEqual(len(METRICS_ADDITIONS),29)
        floor,latest=(added_migrations(revision) for revision in REVISIONS)
        for identities in (floor,latest):
            self.assertEqual(len(identities),len(set(identities)))
        self.assertLessEqual(set(SQL_ADDITIONS+SAMPLING_ADDITIONS+METRICS_ADDITIONS+FEED_ESCALATION_ADDITIONS),
            set(latest))
        self.assertLessEqual(set(latest),set(floor))
        self.assertEqual(set(floor)-set(latest),set(FLOOR_ONLY))
        self.assertLessEqual(set(PINNED_SCOPES),set(floor))
        self.assertIn(('usage','0030_add_jev_evaluator_call_type'),latest)

    def test_sql_index_operations_have_explicit_state_table_scope(self):
        # The sampling layer retains SQL's concurrent-index scope regression.
        state = baseline_state(REVISIONS[1])
        env = scope_environment(state)
        migration = pending_plan([('tracer', '0122_sql_payload_expiry_index')], env)[0]
        self.assertEqual([type(op).__name__ for op in migration.operations],
                         ['AddIndexConcurrently', 'AddIndexConcurrently', 'RunPython'])
        for op in migration.operations[:2]:
            self.assertEqual(env['touched'](op, migration, state),
                             {('postgres', 'public.tracer_sqlexecution')})
        # The RunPython has its own upstream-reviewed table declaration.
        self.assertEqual(env['touched'](migration.operations[2], migration, state),
                         {('postgres', 'public.tracer_sqlexecution')})

    def test_sampling_layer_has_exact_table_scopes(self):
        expected={
            '0117_cohort_sampling': {'public.tracer_project','public.tracer_eval_task',
                'public.tracer_eval_sampling_cohort','public.tracer_eval_sampling_member'},
            # The default ships in 0117_cohort_sampling; this AlterField is
            # identical, so Django emits no SQL and it touches no table.
            '0117_sampling_project_db_default': set(),
            '0119_merge_sampling_default_and_simulation_project': set(),
            '0120_sampling_cohort_lease': {'public.tracer_eval_sampling_cohort'},
            '0121_merge_sampling_and_sql_reuse': set(),
            '0122_merge_sampling_and_alert_issue_state': set(),
            '0123_merge_20261104_0743': set(),
            '0131_merge_20261104_1308': set(),
            '0132_merge_sampling_and_sql_workbench_dev_merge': set(),
            '0133_merge_sampling_and_alert_webhook': set(),
        }
        for revision in REVISIONS:
            state=baseline_state(revision); env=scope_environment(state)
            actual={}
            for migration in pending_plan(SAMPLING_ADDITIONS,env):
                tables=set()
                for op in migration.operations:
                    tables |= env['touched'](op,migration,state)
                    op.state_forwards(migration.app_label,state)
                self.assertTrue(all(engine=='postgres' for engine,table in tables))
                actual[migration.name]={table for engine,table in tables}
            with self.subTest(revision=revision):
                self.assertEqual(actual,expected)

    def test_guardrail_policy_runs_on_gateway_scope_is_its_own_table(self):
        # prism 0030 is one AddField on the guardrail policy model; it must
        # resolve to that table alone and select no high-volume reference.
        registry=p.load_high_volume_registry()
        identity=('agentcc','0030_guardrail_policy_runs_on_gateway')
        for revision in REVISIONS:
            with self.subTest(revision=revision):
                state=baseline_state(revision)
                env=scope_environment(state)
                migration=pending_plan([identity],env)[0]
                self.assertEqual([type(op).__name__ for op in migration.operations],['AddField'])
                tables=env['touched'](migration.operations[0],migration,state)
                self.assertEqual(tables,{('postgres','public.agentcc_guardrail_policy')})
                plan=[{'app_label':identity[0],'name':identity[1]}]
                row={**plan[0],'tables':[{'engine':e,'table':t} for e,t in sorted(tables)]}
                self.assertEqual(list(p.select_reference_registry(registry,[row],plan=plan)),[])

    def test_each_pending_migration_declares_its_high_volume_scope(self):
        # Fail closed on undeclared opaque operations; then each migration's
        # computed high-volume scope must equal its own declaration.
        registry=p.load_high_volume_registry()
        for revision in REVISIONS:
            with self.subTest(revision=revision):
                scopes=plan_scopes(revision,added_migrations(revision))
                expected=set()
                for row in scopes:
                    expected|=declared_high_volume_scope(registry,row)
                plan=[{k:row[k] for k in ('app_label','name')} for row in scopes]
                selected=p.select_reference_registry(registry,scopes,plan=plan)
                self.assertEqual({row['table'] for row in selected},expected)
                self.assertTrue(all(row['database']=='postgres' for row in selected))
                self.assertFalse(any(row.get('app_label')=='prism' or row.get('model') in ('UsageEventLog','UsageSummary')
                    for row in selected))
                usage=next(row for row in scopes if (row['app_label'],row['name'])==('usage','0030_add_jev_evaluator_call_type'))
                self.assertEqual({row['table'] for row in usage['tables']},
                    {'public.usage_apicalltype','public.usage_apicalllog','public.usage_pricing','public.usage_ratelimit'})

    def test_new_migration_must_declare_its_high_volume_scope(self):
        # Negative controls on a synthetic new migration. A nullable FK to the
        # trace table reaches tracer_trace; a plain column reaches no
        # high-volume table and needs no declaration.
        registry=p.load_high_volume_registry()
        identity=('tracer','9999_devflow_probe')
        fk=("migrations.AddField(model_name='project', name='devflow_probe', field=models.ForeignKey("
            "null=True, on_delete=django.db.models.deletion.SET_NULL, to='tracer.trace'))")
        column="migrations.AddField(model_name='project', name='devflow_note', field=models.TextField(null=True))"
        cases=(
            ('undeclared high-volume scope',fk,'',False),
            ('declared high-volume scope',fk,"UPDATE_SCOPE = ('postgres:tracer_trace',)",True),
            ('stale declaration',fk,"UPDATE_SCOPE = ('postgres:tracer_trace', 'postgres:tracer_eval_task')",False),
            ('non high-volume declaration',fk,"UPDATE_SCOPE = ('postgres:tracer_trace', 'postgres:tracer_project')",False),
            ('no high-volume scope',column,'',True),
            ('undeclared opaque operation','migrations.RunPython(forwards)','',None),
        )
        original=migration_path
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/(identity[1]+'.py')
            def resolve(app,name): return path if (app,name)==identity else original(app,name)
            with mock.patch.object(sys.modules[__name__],'migration_path',side_effect=resolve):
                for label,operation,declaration,passes in cases:
                    path.write_text(PROBE.format(declaration=declaration,operation=operation))
                    with self.subTest(case=label):
                        if passes is None:
                            with self.assertRaisesRegex(AssertionError,'opaque_operation_table_scope_unavailable'):
                                plan_scopes(REVISIONS[1],[identity])
                            continue
                        row,=plan_scopes(REVISIONS[1],[identity])
                        if passes:
                            declared_high_volume_scope(registry,row)
                        else:
                            with self.assertRaises(AssertionError):
                                declared_high_volume_scope(registry,row)

    def test_opaque_operations_without_a_declaration_remain_a_plan_scope_blocker(self):
        expected = {
            '0126_derived_metric_refresh': ('RunPython',),
            '0127_metric_lifecycle_safety': ('RunPython', 'RunSQL'),
        }
        for revision in REVISIONS:
            state = baseline_state(revision)
            env = scope_environment(state)
            for name, kinds in expected.items():
                tree = ast.parse(migration_path('tracer', name).read_text())
                # Reviewed declarations now cover these migrations; the opaque
                # operations must still block a plan that carries no declaration.
                self.assertTrue(any(isinstance(node, ast.Assign) and
                    any(isinstance(target, ast.Name) and target.id == 'UPDATE_SAFETY'
                        for target in node.targets) for node in tree.body))
                cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                    and node.name == 'Migration')
                operations = next(node.value for node in cls.body if isinstance(node, ast.Assign)
                    and node.targets[0].id == 'operations')
                for kind in kinds:
                    node = next(node for node in operations.elts if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and node.func.attr == kind)
                    operation = getattr(env['migrations'], kind)(node)
                    migration = SimpleNamespace(app_label='tracer', name=name, declaration=None)
                    with self.subTest(revision=revision, migration=name, operation=kind):
                        with self.assertRaisesRegex(AssertionError, 'opaque_operation_table_scope_unavailable'):
                            env['touched'](operation, migration, state)

    def test_unique_name_is_the_non_pk_reason_usage_is_selected(self):
        state=baseline_state(REVISIONS[1]); env=scope_environment(state)
        migration=pending_plan([('usage','0030_add_jev_evaluator_call_type')],env)[0]
        op=migration.operations[0]
        self.assertFalse(op.field.primary_key); self.assertTrue(op.field.unique)
        self.assertIn(('postgres','public.usage_apicalllog'),env['touched'](op,migration,state))
        # Negative control removes BOTH before/after unique flags. The actual
        # source and target keep them; do not "fix" production by changing this.
        state.models[('usage','apicalltype')].fields['name'].unique=False
        op.field.unique=False
        # Retain a real column alteration: without one this becomes choices-only,
        # which now correctly has no physical-table scope at all.
        op.field.options['max_length']=ast.dump(ast.Constant(51))
        self.assertEqual(env['touched'](op,migration,state),{('postgres','public.usage_apicalltype')})
