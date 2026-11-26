"""Stdlib orchestration and executable fixture-logic tests, NOT qualification.

FakeApp models committed boundaries/count observations. The pure payload code
and the fixed chunk program run against a small ORM double (no Django or SQL on
the host); native CI must establish schema, SQL, timing and 0118's actual effect.
"""
import ast
from collections import Counter
from contextlib import contextmanager, redirect_stdout
import copy
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
from scripts import update_rehearsal_investigation_reference as m


ROOT = Path(__file__).resolve().parents[2]
TABLES = tuple(m.INVESTIGATION_REFERENCE_MODELS.values())
ENTRIES = tuple(row for row in p.load_high_volume_registry()
                if row['database'] == 'postgres' and row['table'] in TABLES)
ARGS = dict(namespace='reference-source', email='reference-source@example.test', rows=100000,
    organization_id=str(uuid.UUID(int=1)), workspace_id=str(uuid.UUID(int=2)))
CONTEXT = dict(user_id=str(uuid.UUID(int=3)), project_id=str(uuid.UUID(int=4)),
    historical_at='2026-01-01T00:00:00+00:00')
PROGRAMS = (s.DJANGO_PREFIX+m.PREPARE, s.DJANGO_PREFIX+m.CHUNK, s.DJANGO_PREFIX+m.VERIFY)
KINDS = ('Group', 'Job', 'Attempt', 'Report', 'Finding', 'Member')
OMEGA_REQUIRED = ('job_id', 'attempt_id', 'idempotency_key', 'result_digest', 'contract_version', 'evidence_digest',
    'outcome', 'coverage_scope', 'read_complete', 'model_calls', 'input_tokens', 'output_tokens', 'cost_status',
    'trace_id', 'observed_span_count', 'future_arrivals_known')


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
        self.plan = m.investigation_reference_plan(scale)
        self.baseline = dict(zip(TABLES, (5, 7, 11)))
        self.committed = set()
        self.calls, self.skipped = [], []
        self.fail_at = None
        self.alter_verified = self.alter_prepared = lambda value: value

    def partial_counts(self):
        # Abstract committed-boundary accounting, NOT simulated physical data.
        chunks = 100000 // m.CHUNK_JOBS
        per_chunk = {table: count // chunks for table, count in m.investigation_reference_plan(1)['counts'].items()}
        return {table: len(self.committed) * per_chunk[table] for table in TABLES}

    def __call__(self, program, args):
        self.calls.append((program, copy.deepcopy(args)))
        if program == s.DJANGO_PREFIX+m.PREPARE:
            seeded = self.partial_counts()
            return self.alter_prepared(dict(context=copy.deepcopy(CONTEXT), seeded_counts=seeded,
                counts={table:self.baseline[table]+seeded[table] for table in TABLES}))
        if program == s.DJANGO_PREFIX+m.CHUNK:
            assert args['scale'] == self.scale
            assert args['stop'] == args['start']+m.CHUNK_JOBS
            assert args['context'] == CONTEXT
            if args['start'] == self.fail_at:
                self.fail_at = None
                raise RuntimeError('synthetic chunk failure')
            skipped = args['start'] in self.committed
            if skipped: self.skipped.append(args['start'])
            self.committed.add(args['start'])
            return {'skipped':skipped}
        if program == s.DJANGO_PREFIX+m.VERIFY:
            assert args['scale'] == self.scale
            assert self.committed == set(range(0, 100000*self.scale, m.CHUNK_JOBS)), 'premature verify'
            return self.alter_verified(dict(counts={table:self.baseline[table]+count
                for table,count in self.plan['counts'].items()}, seeded_counts=copy.deepcopy(self.plan['counts']),
                cohorts=copy.deepcopy(self.plan['cohorts'])))
        raise AssertionError('unexpected source operation')


def payload_env():
    env = {'a':copy.deepcopy(ARGS)}
    exec(m.ROWS, env)
    return env


def fixture_context(env):
    return dict(CONTEXT, project_id=str(env['identity']('project', 0)))


def execute(env, source):
    with redirect_stdout(io.StringIO()) as output:
        exec(source, env)
    return json.loads(output.getvalue()) if output.getvalue() else None


def orm_env():
    """Executes the actual COMMON functions and CHUNK body against PK-indexed doubles."""
    from deploy.tests.test_update_trace_reference import Query
    env = payload_env()
    context = fixture_context(env)
    sample = env['payloads'](context, 0, m.CHUNK_JOBS)
    project = env['project_payload'](context)

    def double(name, row):
        class Model:
            records = {}
            fail_insert = False
            def __init__(self, **kwargs): self.__dict__.update(copy.deepcopy(kwargs))
            @property
            def pk(self): return self.id
        Model.__name__ = name
        Model._meta = SimpleNamespace(concrete_fields=[SimpleNamespace(attname=key) for key in row])
        Model.all_objects = Query(Model)
        return Model
    for name in KINDS:
        env[name] = double(name, sample[name][0])
    env['Project'] = double('Project', project)
    env['Project'].records[project['id']] = copy.deepcopy(project)

    @contextmanager
    def atomic():
        names = ('Project',) + KINDS
        before = [dict(env[name].records) for name in names]
        try: yield
        except BaseException:
            for name, records in zip(names, before): env[name].records = records
            raise
    functions = ast.Module(body=[node for node in ast.parse(m.COMMON).body
        if isinstance(node, ast.FunctionDef)], type_ignores=[])
    exec(compile(functions, '<fixed-common-functions>', 'exec'), env)
    env.update(json=json, transaction=SimpleNamespace(atomic=atomic))
    return env, context


def declared_fields(raw, cls):
    """Source field name -> attname, from a released model declaration."""
    node = next(node for node in ast.parse(raw).body if isinstance(node, ast.ClassDef) and node.name == cls)
    fields = {}
    for item in node.body:
        if not isinstance(item, ast.Assign) or not isinstance(item.value, ast.Call): continue
        function = item.value.func
        if (isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id == 'models'
                and (function.attr.endswith('Field') or function.attr == 'ForeignKey')):
            name = item.targets[0].id
            fields[name] = name + '_id' if function.attr in ('ForeignKey', 'OneToOneField') else name
    for name in ('created_at', 'updated_at', 'deleted', 'deleted_at'):
        fields[name] = name
    return fields


class InvestigationReferenceTests(unittest.TestCase):
    def test_registry_entries_match_the_reviewed_model_map(self):
        self.assertEqual(len(ENTRIES), 3)
        self.assertEqual({(row['app_label'], row['model']): row['table'] for row in ENTRIES},
            m.INVESTIGATION_REFERENCE_MODELS)
        plan = m.investigation_reference_plan(1)
        self.assertEqual(set(plan['counts']), set(TABLES))
        self.assertTrue(all(count >= p.REFERENCE_ROWS for count in plan['counts'].values()))

    def test_dispatch_exact_table_model_engine_and_inheritance(self):
        for cls in (s.ShippingRehearsal, q.QualificationRehearsal):
            for entry in ENTRIES:
                driver = driver_for(cls=cls)
                driver.seed_investigation_reference_family = mock.Mock()
                driver.app_python = mock.Mock()
                driver.seed_reference_table(entry, 100000)
                driver.seed_investigation_reference_family.assert_called_once_with(100000)
                mutations = [('database','clickhouse'),('database','sqlite'),('app_label','unknown'),
                    ('model','Unknown'),('table','public.'+entry['table']),('table','tracer_trace_investigation_attempt'),
                    ('table','tracer_error_cluster_traces')]
                mutations += [('table',other['table']) for other in ENTRIES if other != entry]
                for key, value in mutations:
                    with self.subTest(cls=cls.__name__, entry=entry['table'], key=key, value=value), \
                            self.assertRaisesRegex(p.ManualRelease, 'high_volume_reference_unavailable'):
                        driver.seed_reference_table(dict(entry, **{key:value}), 100000)
                self.assertEqual(driver.seed_investigation_reference_family.call_count, 1)
                driver.app_python.assert_not_called()

    def test_unseeded_investigation_tables_still_refuse(self):
        siblings = [row for row in p.load_high_volume_registry()
            if row['table'].startswith('tracer_trace_investigation_') and row['table'] not in TABLES]
        self.assertTrue(siblings)
        for cls in (s.ShippingRehearsal, q.QualificationRehearsal):
            driver = driver_for(cls=cls)
            driver.seed_investigation_reference_family = mock.Mock()
            driver.app_python = mock.Mock()
            for entry in siblings:
                with self.subTest(cls=cls.__name__, table=entry['table']), \
                        self.assertRaisesRegex(p.ManualRelease, 'high_volume_reference_unavailable'):
                    driver.seed_reference_table(entry, 100000)
            driver.seed_investigation_reference_family.assert_not_called()
            driver.app_python.assert_not_called()

    def test_diagnostics_keep_only_the_closed_registry_identity(self):
        driver = driver_for()
        driver._diagnostic_data = {}
        driver.seed_investigation_reference_family = mock.Mock()
        driver.app_python = mock.Mock()
        for entry in ENTRIES:
            driver.seed_reference_table(entry, 10**100)
            last = driver._diagnostic_data['last_reference']
            self.assertEqual(last, dict({key:entry[key] for key in ('database','app_label','model','table')},
                requested_rows=None))
            self.assertLess(len(json.dumps(last)), 512)
            driver.seed_reference_table(entry, 100000)
            self.assertEqual(driver._diagnostic_data['last_reference']['requested_rows'], 100000)
        driver.app_python.assert_not_called()

    def test_programs_compile_and_only_reviewed_models_are_resolved(self):
        expected = {('tracer', name) for name in ('TraceInvestigationJob', 'TraceInvestigationAttempt',
            'TraceInvestigationReport', 'TraceInvestigationFinding', 'TraceErrorGroup', 'ErrorClusterTraces',
            'Project')} | {('accounts', 'User'), ('accounts', 'Workspace')}
        for program in (m.PREPARE, m.CHUNK, m.VERIFY):
            compile(s.DJANGO_PREFIX+program, '<fixed-source-operation>', 'exec')
            tree = ast.parse(program)
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
            names = [(ast.literal_eval(node.args[0]), ast.literal_eval(node.args[1])) for node in calls
                if isinstance(node.func, ast.Attribute) and node.func.attr == 'get_model']
            self.assertEqual(set(names), expected)
            self.assertFalse(any(isinstance(node, ast.keyword) and node.arg == 'ignore_conflicts' for node in ast.walk(tree)))
            self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr in ('delete', 'schema_editor', 'migrate', 'raw', 'extra')
                for node in ast.walk(tree)))
            for text in ('ANALYZE', 'pg_stat', 'TRIGGER', 'session_replication_role', 'INSERT', 'UPDATE '):
                self.assertNotIn(text, program)
        mutations = {'create', 'bulk_create', 'bulk_update', 'update', 'delete', 'save', 'get_or_create', 'update_or_create'}
        verify_calls = {node.func.attr for node in ast.walk(ast.parse(m.VERIFY))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(verify_calls & mutations)

    def test_missing_source_model_fails_before_any_write(self):
        apps = mock.Mock()
        apps.get_model.side_effect = LookupError('source model missing')
        db = SimpleNamespace(connection=mock.Mock(), transaction=mock.Mock(), models=mock.Mock())
        with mock.patch.dict('sys.modules', {'django.apps':SimpleNamespace(apps=apps), 'django.db':db,
                'django.db.models':SimpleNamespace(Q=mock.Mock(), F=mock.Mock(), Exists=mock.Mock(), OuterRef=mock.Mock())}):
            with self.assertRaises(LookupError): exec(m.PREPARE, {'a':ARGS})
        db.transaction.atomic.assert_not_called()
        db.connection.cursor.assert_not_called()

    def test_source_inventories_and_payloads_match_released_models(self):
        from deploy.tests.test_update_usage_reference import REVISIONS, source
        classes = dict(Job=('trace_investigation.py', 'TraceInvestigationJob'),
            Attempt=('trace_investigation.py', 'TraceInvestigationAttempt'),
            Report=('trace_investigation.py', 'TraceInvestigationReport'),
            Finding=('trace_investigation.py', 'TraceInvestigationFinding'),
            Group=('trace_error_analysis.py', 'TraceErrorGroup'),
            Member=('trace_error_analysis.py', 'ErrorClusterTraces'))
        env = payload_env()
        context = fixture_context(env)
        samples = [env['payloads'](context, start, start + m.GROUP_SIZE) for start in (0, 69000, 70000, 95000)]
        for revision in REVISIONS:
            for name, (filename, cls) in classes.items():
                with self.subTest(revision=revision, model=name):
                    fields = declared_fields(source(revision, 'tracer/models/'+filename), cls)
                    self.assertEqual(set(m.SOURCE_FIELDS[name].split()), set(fields))
                    for sample in samples:
                        for row in sample[name]:
                            self.assertEqual(set(row), set(fields.values()))
            project = declared_fields(source(revision, 'tracer/models/project.py'), 'Project')
            self.assertLessEqual(set(env['project_payload'](context)), set(project.values()))

    def test_actual_payload_distribution_chain_and_constraints(self):
        env = payload_env()
        context = fixture_context(env)
        plan = m.investigation_reference_plan(1)
        counts, tally = Counter(), Counter()
        seen = {name: set() for name in KINDS}
        traces, keys, records, codes, ended = set(), set(), set(), set(), set()
        for start in range(0, 100000, m.CHUNK_JOBS):
            rows = env['payloads'](context, start, start + m.CHUNK_JOBS)
            for name in KINDS:
                for row in rows[name]:
                    self.assertIsInstance(row['id'], uuid.UUID)
                    self.assertNotIn(row['id'], seen[name])
                    seen[name].add(row['id'])
            groups, jobs, attempts, reports, findings = ({row['id']: row for row in rows[name]}
                for name in ('Group', 'Job', 'Attempt', 'Report', 'Finding'))
            counts.update(tracer_trace_investigation_finding=len(findings), tracer_trace_investigation_job=len(jobs),
                tracer_trace_investigation_report=len(reports))
            tally.update(groups=len(groups), attempts=len(attempts), memberships=len(rows['Member']))
            for row in groups.values():
                self.assertTrue(row['cluster_id'].startswith('QI') and len(row['cluster_id']) <= 20)
                self.assertNotIn(row['cluster_id'], codes)
                codes.add(row['cluster_id'])
                self.assertEqual((row['source'], row['target_type'], row['test_execution_id'], row['status'], row['deleted']),
                    ('scanner', 'error_feed', None, 'escalating', False))
            for row in jobs.values():
                self.assertEqual((row['workload_type'], row['test_execution_id'], row['call_execution_id']), ('trace', None, None))
                self.assertNotIn(row['trace_id'], traces)
                traces.add(row['trace_id'])
                if row['root_end_time'] is None:
                    tally['no_root_end_jobs'] += 1
                else:
                    self.assertNotIn(row['root_end_time'], ended)
                    ended.add(row['root_end_time'])
            for row in attempts.values():
                self.assertIn(row['job_id'], jobs)
                self.assertEqual(len(row['lease_token_digest']), 64)
                self.assertLessEqual(len(row['memory_digest']), 71)
                self.assertLessEqual(len(row['engine_version']), 20)
            for row in reports.values():
                if row['source'] == 'omega':
                    self.assertTrue(all(row[key] is not None for key in OMEGA_REQUIRED))
                    self.assertEqual((row['observed_call_count'], row['source_record_id'], row['test_execution_id'],
                        row['is_current']), (None, None, None, True))
                    self.assertEqual(attempts[row['attempt_id']]['job_id'], row['job_id'])
                    self.assertEqual(jobs[row['job_id']]['trace_id'], row['trace_id'])
                    self.assertNotIn(row['idempotency_key'], keys)
                    keys.add(row['idempotency_key'])
                    for key in ('result_digest', 'evidence_digest'):
                        self.assertLessEqual(len(row[key]), 71)
                else:
                    self.assertEqual(row['source'], 'legacy_scan')
                    tally['legacy_reports'] += 1
                    self.assertEqual((row['job_id'], row['attempt_id'], row['idempotency_key'], row['is_current']),
                        (None, None, None, False))
                    self.assertNotIn(row['source_record_id'], records)
                    records.add(row['source_record_id'])
            for row in findings.values():
                self.assertIn(row['report_id'], reports)
                self.assertEqual((row['finding_id'], row['source_finding_id']), ('f0', None))
                self.assertTrue(row['cluster_id'] is None or row['cluster_id'] in groups)
            members = {row['finding_id'] for row in rows['Member']}
            tally['findings_without_membership'] += sum(1 for finding in findings if finding not in members)
            for row in rows['Member']:
                self.assertIn(row['cluster_id'], groups)
                self.assertEqual((row['span_id'], row['trace_session_id'], row['scan_issue_id'], row['eval_logger_id']),
                    (None, None, None, None))
                finding = findings[row['finding_id']]
                report = reports[finding['report_id']]
                self.assertEqual(finding['cluster_id'], row['cluster_id'])
                if row['trace_id'] == report['trace_id']:
                    tally['chain_consistent_memberships'] += 1
                job = jobs.get(report['job_id'])
                if job is None:
                    tally['legacy_memberships'] += 1
                elif job['root_end_time'] is None:
                    tally['no_root_end_memberships'] += 1
                else:
                    # 0118's join: membership -> finding -> report -> job root end time.
                    tally['backfill_memberships'] += 1
                    tally['deleted_backfill_memberships'] += row['deleted']
                    self.assertEqual((finding['deleted'], report['deleted'], job['deleted']), (row['deleted'],) * 3)
        self.assertEqual(dict(counts), plan['counts'])
        self.assertEqual(dict(tally), plan['cohorts'])
        second = Counter(env['cohort'](ordinal) for ordinal in range(100000, 200000))
        self.assertEqual(dict(second), m.COHORTS)
        doubled = m.investigation_reference_plan(2)
        self.assertEqual(doubled['counts'], {key: 2*value for key, value in plan['counts'].items()})
        self.assertEqual(doubled['cohorts'], {key: 2*value for key, value in plan['cohorts'].items()})

    def test_verify_predicate_is_the_migration_join(self):
        migration = ROOT/'futureagi/tracer/migrations/0118_backfill_feed_issue_statuses.py'

        def predicate(raw):
            call = next(node for node in ast.walk(ast.parse(raw)) if isinstance(node, ast.Call) and any(
                keyword.arg == 'finding__report__job__root_end_time__isnull' and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False for keyword in node.keywords))
            return {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        expected = predicate(migration.read_text())
        # The source schema has no occurred_at: every source membership is pending.
        self.assertIs(expected.pop('occurred_at__isnull'), True)
        self.assertEqual(predicate(m.VERIFY_BODY), expected)

    def test_actual_chunk_insert_replay_conflict_and_rollback(self):
        env, context = orm_env()
        env['a'].update(context=context, start=0, stop=m.CHUNK_JOBS, scale=1)
        project = copy.deepcopy(env['Project'].records)
        self.assertEqual(execute(env, m.CHUNK_BODY), {'skipped':False})
        self.assertEqual({name: len(env[name].records) for name in KINDS},
            dict(Group=25, Job=2500, Attempt=2500, Report=2525, Finding=2525, Member=2525))
        original = {name: copy.deepcopy(env[name].records) for name in KINDS}
        self.assertEqual(execute(env, m.CHUNK_BODY), {'skipped':True})
        self.assertEqual(original, {name: env[name].records for name in KINDS})
        self.assertEqual(project, env['Project'].records)
        # Same IDs/counts do not make a changed relation a matching replay.
        member = next(iter(env['Member'].records))
        env['Member'].records[member]['finding_id'] = None
        with self.assertRaisesRegex(AssertionError, 'incompatible existing Member'):
            execute(env, m.CHUNK_BODY)
        env, context = orm_env()
        env['a'].update(context=context, start=0, stop=m.CHUNK_JOBS, scale=1)
        env['Finding'].fail_insert = True
        with self.assertRaisesRegex(RuntimeError, 'insert failure'):
            execute(env, m.CHUNK_BODY)
        self.assertTrue(all(not env[name].records for name in KINDS))
        env['Finding'].fail_insert = False
        self.assertEqual(execute(env, m.CHUNK_BODY), {'skipped':False})
        # A lost row inside a supposedly atomic committed chunk is not repaired.
        env['Job'].records.pop(next(iter(env['Job'].records)))
        with self.assertRaisesRegex(AssertionError, 'partial atomic chunk'):
            execute(env, m.CHUNK_BODY)

    def test_actual_chunk_refuses_bad_slices_and_changed_context(self):
        for start, stop, scale, message in ((100, 2600, 1, 'misaligned'), (0, 5000, 1, 'misaligned'),
                (100000, 102500, 1, 'invalid slice'), (0, 2500, 0, 'invalid slice'), (0, 2500, True, 'invalid slice')):
            env, context = orm_env()
            env['a'].update(context=context, start=start, stop=stop, scale=scale)
            with self.subTest(start=start, stop=stop, scale=scale), self.assertRaisesRegex(AssertionError, message):
                execute(env, m.CHUNK_BODY)
            self.assertTrue(all(not env[name].records for name in KINDS))
        env, context = orm_env()
        env['a'].update(context=context, start=95000, stop=97500, scale=1)
        self.assertEqual(execute(env, m.CHUNK_BODY), {'skipped':False})
        # Findings without membership: only the 25 legacy memberships remain.
        self.assertEqual((len(env['Finding'].records), len(env['Member'].records)), (2525, 25))
        env['a']['rows'] = 100001
        with self.assertRaisesRegex(AssertionError, 'incompatible existing Project'):
            execute(env, m.CHUNK_BODY)
        env, context = orm_env()
        env['a'].update(context=dict(context, project_id=str(uuid.UUID(int=9))), start=0, stop=2500, scale=1)
        with self.assertRaisesRegex(AssertionError, 'project identity changed'):
            execute(env, m.CHUNK_BODY)

    def test_full_family_precedes_first_independent_measurement(self):
        for entries in (ENTRIES, ENTRIES[:1], ENTRIES[1:]):
            fake = FakeApp()
            driver = driver_for(fake, cls=q.QualificationRehearsal)
            measured = []
            def measure(entry):
                self.assertEqual(len(fake.committed), 40)
                self.assertTrue(hasattr(driver, '_investigation_reference'))
                self.assertFalse(hasattr(driver, '_investigation_reference_pending'))
                self.assertEqual(fake.calls[-1][0], s.DJANGO_PREFIX+m.VERIFY)
                measured.append(entry['table'])
                return dict(engine='postgres', table='public.'+entry['table'], rows=222222, bytes=7654321)
            result = p.seed_reference_tables(entries, driver.seed_reference_table, measure)
            self.assertEqual([row['rows'] for row in result], [222222]*len(entries))
            self.assertEqual(measured, [entry['table'] for entry in entries])
            programs = Counter(program for program, args in fake.calls)
            self.assertEqual(programs, Counter({PROGRAMS[0]:1, PROGRAMS[1]:40, PROGRAMS[2]:len(entries)}))
            self.assertEqual(fake.calls[0][1], ARGS)
            self.assertEqual(len(driver.corpus['reference_seed_operations']), 1)
            operation = driver.corpus['reference_seed_operations'][0]
            self.assertEqual((operation['table'], operation['operation'], operation['chunks'], operation['cohorts']),
                ('tracer_trace_investigation_family', 'investigation_reference_fixture', 40, fake.plan['cohorts']))
            for secret in ('Authorization', 'SECRET', 'password', 'DO-NOT-FORWARD'):
                self.assertNotIn(secret, json.dumps([args for program, args in fake.calls]))

    def test_independent_measurement_cannot_be_replaced_by_fixture_counts(self):
        for bad in (dict(rows=99999, bytes=1000), dict(rows=100000, bytes=0), dict(rows=None, bytes=None)):
            driver = driver_for()
            with self.assertRaises(p.ManualRelease):
                p.seed_reference_tables(ENTRIES[:1], driver.seed_reference_table,
                    lambda entry: dict(engine='postgres', table='public.'+entry['table'], **bad))

    def test_same_and_new_instance_resume_and_validate_baseline_growth(self):
        for fresh in (False, True):
            fake = FakeApp()
            fake.fail_at = 7500
            driver = driver_for(fake)
            with self.assertRaises(RuntimeError): driver.seed_investigation_reference_family(100000)
            self.assertFalse(hasattr(driver, '_investigation_reference'))
            self.assertNotIn('reference_seed_operations', driver.corpus)
            if fresh: driver = driver_for(fake)
            driver.seed_investigation_reference_family(100000)
            self.assertEqual(fake.skipped, [0, 2500, 5000])
            self.assertEqual(driver._investigation_reference['baseline'], fake.baseline)
            self.assertEqual(len(fake.committed), 40)
            calls = len(fake.calls)
            fake.alter_verified = lambda value: dict(value, counts={k: v+111 for k, v in value['counts'].items()})
            driver.seed_investigation_reference_family(100000)
            self.assertEqual(len(fake.calls), calls+1)
            self.assertEqual(fake.calls[-1][0], s.DJANGO_PREFIX+m.VERIFY)
            self.assertEqual(len(driver.corpus['reference_seed_operations']), 1)

    def test_bad_counts_and_unknown_cohorts_never_complete(self):
        mutations = [lambda value: None, lambda value: dict(value, counts={}), lambda value: dict(value, seeded_counts={}),
            lambda value: dict(value, cohorts={}),
            lambda value: dict(value, cohorts=dict(value['cohorts'], backfill_memberships=0)),
            lambda value: dict(value, cohorts=dict(value['cohorts'], unexpected=1)),
            lambda value: dict(value, seeded_counts={k: float(v) for k, v in value['seeded_counts'].items()}),
            lambda value: dict(value, counts=m.investigation_reference_plan(1)['counts']),
            lambda value: dict(value, counts=dict(value['counts'], tracer_trace_investigation_job=99999))]
        for change in mutations:
            fake = FakeApp()
            fake.alter_verified = change
            driver = driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(100000)
            self.assertFalse(hasattr(driver, '_investigation_reference'))
            self.assertNotIn('reference_seed_operations', driver.corpus)
            fake.alter_verified = lambda value: value
            driver.seed_investigation_reference_family(100000)
            self.assertEqual(fake.skipped, list(range(0, 100000, 2500)))

    def test_invalid_prepare_rows_and_context_fail_closed(self):
        for change in (lambda value: None, lambda value: dict(value, counts={}),
                lambda value: dict(value, context=None),
                lambda value: dict(value, seeded_counts={k: False for k in TABLES}),
                lambda value: dict(value, seeded_counts={k: 1000000 for k in TABLES})):
            fake = FakeApp()
            fake.alter_prepared = change
            driver = driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(100000)
            self.assertEqual(len(fake.calls), 1)
            self.assertFalse(hasattr(driver, '_investigation_reference_pending'))
        for rows in (True, 99999, 0, 100000001, 100000.0, '100000'):
            driver = driver_for(mock.Mock())
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(rows)
            driver.app_python.assert_not_called()
        for source in (None, {}, dict(namespace='source', headers={}), dict(namespace='source', headers={}, account={})):
            driver = driver_for(mock.Mock())
            driver.witnesses.namespaces = {'source':source}
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(100000)
            driver.app_python.assert_not_called()

    def test_scale_rounding_and_changed_arguments_refuse(self):
        fake = FakeApp(scale=2)
        driver = driver_for(fake)
        driver.seed_investigation_reference_family(100001)
        self.assertEqual(len(fake.committed), 80)
        self.assertEqual(driver._investigation_reference['cohorts']['groups'], 2000)
        calls = len(fake.calls)
        for rows in (100000, 200000):
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(rows)
        self.assertEqual(len(fake.calls), calls)
        driver.witnesses.namespaces['source']['headers']['X-Workspace-Id'] = 'different'
        with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(100001)
        self.assertEqual(len(fake.calls), calls)

    def test_family_completes_before_shared_error_feed_tables_are_measured(self):
        # collect_executed_row requires each seed-time measurement to equal the
        # pre-backup one, and registry order measures these Error Feed tables first.
        from deploy.tests.test_update_error_feed_reference import FakeApp as ErrorApp, TABLES as ERROR_TABLES
        shared = set(m.INVESTIGATION_REFERENCE_SHARED_TABLES)
        self.assertLessEqual(shared, set(ERROR_TABLES))
        selected = [row for row in p.load_high_volume_registry()
            if row['database'] == 'postgres' and row['table'] in set(ERROR_TABLES) | set(TABLES)]
        order = [row['table'] for row in selected]
        self.assertLess(max(order.index(table) for table in shared), min(order.index(table) for table in TABLES))
        investigation, error = FakeApp(), ErrorApp()
        calls = []
        def app(program, args):
            calls.append(program)
            return investigation(program, args) if program in PROGRAMS else error(program, args)
        driver = driver_for(app)
        driver.reference_registry = copy.deepcopy(selected)
        measured = []
        def measure(entry):
            if entry['table'] in shared or entry['table'] in TABLES:
                self.assertEqual(len(investigation.committed), 40)
                self.assertTrue(hasattr(driver, '_investigation_reference'))
            measured.append((entry['table'], len(calls)))
            return dict(engine='postgres', table='public.'+entry['table'], rows=222222, bytes=7654321)
        p.seed_reference_tables(selected, driver.seed_reference_table, measure)
        self.assertEqual([table for table, count in measured], order)
        first = min(count for table, count in measured if table in shared)
        writes = [index for index, program in enumerate(calls) if program in PROGRAMS[:2]]
        self.assertTrue(writes)
        self.assertLess(max(writes), first)
        self.assertEqual(sum(program == PROGRAMS[0] for program in calls), 1)
        self.assertEqual(sum(program == PROGRAMS[1] for program in calls), 40)
        self.assertEqual(len(error.committed), 20)
        self.assertEqual([row['table'] for row in driver.corpus['reference_seed_operations']],
            ['tracer_trace_investigation_family', 'tracer_error_feed_family'])

    def test_shared_tables_do_not_seed_the_family_when_it_is_not_selected(self):
        from deploy.tests.test_update_error_feed_reference import FakeApp as ErrorApp, ENTRIES as ERROR_ENTRIES
        investigation, error = FakeApp(), ErrorApp()
        driver = driver_for(lambda program, args: investigation(program, args) if program in PROGRAMS else error(program, args))
        driver.reference_registry = copy.deepcopy(list(ERROR_ENTRIES))
        for entry in ERROR_ENTRIES:
            driver.seed_reference_table(entry, 100000)
        self.assertEqual(investigation.calls, [])
        self.assertFalse(hasattr(driver, '_investigation_reference'))
        self.assertEqual(len(error.committed), 20)
        # An inexact Error Feed identity refuses without seeding either family.
        driver = driver_for(lambda program, args: investigation(program, args) if program in PROGRAMS else error(program, args))
        driver.reference_registry = copy.deepcopy(list(ENTRIES))
        member = next(row for row in ERROR_ENTRIES if row['table'] == 'tracer_error_cluster_traces')
        with self.assertRaisesRegex(p.ManualRelease, 'high_volume_reference_unavailable'):
            driver.seed_reference_table(dict(member, model='Unknown'), 100000)
        self.assertEqual(investigation.calls, [])

    def test_error_feed_and_investigation_families_keep_independent_state(self):
        from deploy.tests.test_update_error_feed_reference import FakeApp as ErrorApp, ENTRIES as ERROR_ENTRIES
        investigation, error = FakeApp(), ErrorApp()
        def app(program, args):
            return investigation(program, args) if program in PROGRAMS else error(program, args)
        driver = driver_for(app)
        driver.seed_reference_table(ERROR_ENTRIES[0], 100000)
        before = copy.deepcopy(driver._error_feed_reference)
        driver.seed_reference_table(ENTRIES[0], 100000)
        self.assertEqual(before, driver._error_feed_reference)
        driver.seed_reference_table(ERROR_ENTRIES[1], 100000)
        self.assertEqual(len(error.committed), 20)
        self.assertEqual(len(investigation.committed), 40)
        self.assertEqual(len(driver.corpus['reference_seed_operations']), 2)
        namespace = payload_env()['NAMESPACE']
        for prefix in ('futureagi-qualification-reference:', 'futureagi-qualification-trace-reference:'):
            self.assertNotEqual(uuid.uuid5(uuid.NAMESPACE_URL, prefix+ARGS['namespace']), namespace)


if __name__ == '__main__':
    unittest.main()
