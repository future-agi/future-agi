"""Stdlib orchestration and executable fixture-logic tests, NOT qualification.

FakeApp models committed boundaries/count observations. The pure payload code
and the fixed chunk program run against a small ORM double (no Django or SQL on
the host); native CI must establish schema, SQL, timing and 0118's actual effect.
"""
import ast
from collections import Counter
from contextlib import contextmanager, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
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
PROGRAMS = (s.DJANGO_PREFIX+m.PREPARE, s.DJANGO_PREFIX+m.BASELINE_PROGRAM, s.DJANGO_PREFIX+m.CHUNK,
    s.DJANGO_PREFIX+m.VERIFY_SLICE, s.DJANGO_PREFIX+m.COHORTS_PROGRAM)
TARGET_PROGRAMS = (s.DJANGO_PREFIX+m.TARGET_SLICE, s.DJANGO_PREFIX+m.TARGET_COHORTS)
# One full bounded source verification at scale one: payload slices, then buckets.
VERIFY_CALLS = 100000 // m.VERIFY_JOBS + 1
KINDS = ('Group', 'Job', 'Attempt', 'Report', 'Finding', 'Member')
# Documented per-block geometry (module docstring, DEVELOPER_GUIDE and PR body).
SCALE_ONE_COUNTS = {'tracer_trace_investigation_finding': 101000, 'tracer_trace_investigation_job': 100000,
    'tracer_trace_investigation_report': 101000}
SCALE_ONE_COHORTS = dict(groups=1000, attempts=100000, legacy_reports=1000, no_root_end_jobs=25000,
    memberships=96000, backfill_memberships=70000, deleted_backfill_memberships=1000,
    no_root_end_memberships=25000, legacy_memberships=1000, chain_consistent_memberships=96000,
    findings_without_membership=5000)
SCALE_ONE_TARGET = dict(memberships=96000, filled_memberships=70000, filled_deleted_memberships=1000,
    null_memberships=26000, null_no_root_end_memberships=25000, null_legacy_memberships=1000)
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
        self.committed, self.verified = set(), set()
        self.calls, self.skipped = [], []
        self.fail_at = None
        self.alter_verified = self.alter_prepared = self.alter_baseline = self.alter_slice = lambda value: value
        self.alter_target_slice = self.alter_target = lambda value: value

    def bucket_counts(self, args):
        # Abstract the UUID partition as even shares, with any remainder in the first buckets.
        index = args['bucket']
        assert args['buckets'] == self.scale == args['scale'] and 0 <= index < self.scale
        def share(count): return count // self.scale + (index < count % self.scale)
        chunks = self.scale * 100000 // m.CHUNK_JOBS
        seeded = {table: share(len(self.committed) * (count // chunks)) for table, count in self.plan['counts'].items()}
        return dict(counts={table: share(self.baseline[table]) + seeded[table] for table in TABLES}, seeded_counts=seeded)

    def __call__(self, program, args):
        self.calls.append((program, copy.deepcopy(args)))
        if program == s.DJANGO_PREFIX+m.PREPARE:
            return self.alter_prepared(dict(context=copy.deepcopy(CONTEXT)))
        if program == s.DJANGO_PREFIX+m.BASELINE_PROGRAM:
            return self.alter_baseline(self.bucket_counts(args))
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
        one = m.investigation_reference_plan(1)
        if program == s.DJANGO_PREFIX+m.VERIFY_SLICE:
            assert args['scale'] == self.scale and args['context'] == CONTEXT
            assert args['stop'] == args['start']+m.VERIFY_JOBS and args['start'] % m.VERIFY_JOBS == 0
            assert self.committed == set(range(0, 100000*self.scale, m.CHUNK_JOBS)), 'premature verify'
            if args['start'] == 0: self.verified = set()
            self.verified.add(args['start'])
            return self.alter_slice({'verified':m.VERIFY_JOBS})
        if program == s.DJANGO_PREFIX+m.COHORTS_PROGRAM:
            assert self.verified == set(range(0, 100000*self.scale, m.VERIFY_JOBS)), 'cohorts before payload slices'
            return self.alter_verified(dict(self.bucket_counts(args), cohorts=one['cohorts']))
        if program == s.DJANGO_PREFIX+m.TARGET_SLICE:
            assert args['scale'] == self.scale and args['context'] == CONTEXT
            assert args['stop'] == args['start']+m.VERIFY_JOBS and args['start'] % m.VERIFY_JOBS == 0
            cohort = payload_env()['cohort']
            checked = sum(cohort(o) != 'no_membership' for o in range(args['start'], args['stop'])) \
                + m.VERIFY_JOBS // m.LEGACY_EVERY
            return self.alter_target_slice(dict(checked=checked, mismatched=0))
        if program == s.DJANGO_PREFIX+m.TARGET_COHORTS:
            assert args['scale'] == self.scale and args['buckets'] == self.scale and 0 <= args['bucket'] < self.scale
            return dict(cohorts=self.alter_target(m.investigation_target_plan(1)))
        raise AssertionError('unexpected source operation')


def payload_env(**arguments):
    env = {'a':dict(copy.deepcopy(ARGS), **arguments)}
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
    from deploy.tests.test_update_trace_reference import Query as TraceQuery

    class Query(TraceQuery):
        def bulk_update(self, objects, fields, batch_size):
            # Only the job -> current report link is set after insert.
            assert batch_size == 500 and fields == ['current_report_id']
            for obj in objects:
                assert obj.pk in self.model.records, 'bulk_update of a missing row'
                self.model.records[obj.pk] = dict(self.model.records[obj.pk], current_report_id=obj.current_report_id)
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

    def test_plan_returns_documented_scale_one_counts(self):
        plan = m.investigation_reference_plan(1)
        self.assertEqual(plan['counts'], SCALE_ONE_COUNTS)
        self.assertEqual(plan['cohorts'], SCALE_ONE_COHORTS)

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
        programs = {m.PREPARE: expected, m.CHUNK: expected, m.BASELINE_PROGRAM: expected,
            m.VERIFY_SLICE: expected, m.COHORTS_PROGRAM: expected,
            # Target reads follow 0118's own relation path from the membership model.
            m.TARGET_SLICE: {('tracer', 'ErrorClusterTraces')}, m.TARGET_COHORTS: {('tracer', 'ErrorClusterTraces')}}
        for program, models in programs.items():
            compile(s.DJANGO_PREFIX+program, '<fixed-source-operation>', 'exec')
            tree = ast.parse(program)
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
            names = [(ast.literal_eval(node.args[0]), ast.literal_eval(node.args[1])) for node in calls
                if isinstance(node.func, ast.Attribute) and node.func.attr == 'get_model']
            self.assertEqual(set(names), models)
            self.assertFalse(any(isinstance(node, ast.keyword) and node.arg == 'ignore_conflicts' for node in ast.walk(tree)))
            self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr in ('delete', 'schema_editor', 'migrate', 'raw', 'extra')
                for node in ast.walk(tree)))
            for text in ('ANALYZE', 'pg_stat', 'TRIGGER', 'session_replication_role', 'INSERT', 'UPDATE '):
                self.assertNotIn(text, program)
        mutations = {'create', 'bulk_create', 'bulk_update', 'update', 'delete', 'save', 'get_or_create', 'update_or_create'}
        for program in (m.BASELINE_PROGRAM, m.VERIFY_SLICE, m.COHORTS_PROGRAM, m.TARGET_SLICE, m.TARGET_COHORTS):
            verify_calls = {node.func.attr for node in ast.walk(ast.parse(program))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
            self.assertFalse(verify_calls & mutations)
            # Target programs never reuse the source inventory or context checks.
            if program in (m.TARGET_SLICE, m.TARGET_COHORTS):
                self.assertNotIn('matching(', program)
                self.assertNotIn('check_context(', program)

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
        recounted = 0
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
                # Each job points at its own current Omega report.
                current = reports[row['current_report_id']]
                self.assertEqual((current['job_id'], current['is_current'], current['source'], current['deleted']),
                    (row['id'], True, 'omega', row['deleted']))
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
            # Issue counters follow the released grouping _recount, recomputed
            # here from the generated rows with its own predicate.
            for group in groups.values():
                live = [row for row in rows['Member'] if row['cluster_id'] == group['id'] and not row['deleted']
                    and row['span_id'] is None and row['trace_session_id'] is None and row['finding_id'] is not None
                    and not findings[row['finding_id']]['deleted'] and findings[row['finding_id']]['cluster_id'] == group['id']
                    and not reports[findings[row['finding_id']]['report_id']]['deleted']
                    and reports[findings[row['finding_id']]['report_id']]['is_current']
                    and reports[findings[row['finding_id']]['report_id']]['source'] == 'omega'
                    and reports[findings[row['finding_id']]['report_id']]['execution_status'] == 'completed'
                    and reports[findings[row['finding_id']]['report_id']]['workload_type'] == 'trace'
                    and row['trace_id'] == reports[findings[row['finding_id']]['report_id']]['trace_id']]
                recorded = [reports[findings[row['finding_id']]['report_id']]['recorded_at'] for row in live]
                distinct = len({row['finding_id'] for row in live})
                self.assertEqual((group['error_count'], group['total_events'], group['unique_traces'],
                    group['first_seen'], group['last_seen'], group['error_ids']),
                    (distinct, distinct, len({row['trace_id'] for row in live}),
                     min(recorded) if recorded else None, max(recorded) if recorded else None, []))
                recounted += bool(live)
        self.assertEqual(dict(counts), plan['counts'])
        self.assertEqual(dict(tally), plan['cohorts'])
        # 10 issues per block hold only soft-deleted chains and 50 only findings
        # without a membership, so they recount to zero with no seen times.
        self.assertEqual(recounted, 940)
        # The documented scale-one geometry, pinned literally.
        self.assertEqual(plan['counts'], SCALE_ONE_COUNTS)
        self.assertEqual(plan['cohorts'], SCALE_ONE_COHORTS)
        self.assertEqual(m.investigation_target_plan(1), SCALE_ONE_TARGET)
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
        self.assertEqual(predicate(m.COHORTS_BODY), expected)
        # On the target, filled rows carry 0118's subquery value and source.
        source = ast.parse(migration.read_text())
        update = next(node for node in ast.walk(source) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == 'update')
        self.assertEqual({keyword.arg for keyword in update.keywords}, set(m.TARGET_ONLY_MEMBER_COLUMNS))
        self.assertEqual(next(ast.literal_eval(keyword.value) for keyword in update.keywords
            if keyword.arg == 'occurrence_time_source'), 'omega_root_end')
        self.assertIn("occurrence_time_source='omega_root_end'", m.TARGET_COHORTS_BODY)
        self.assertIn("F('finding__report__job__root_end_time')", m.TARGET_COHORTS_BODY)

    def test_actual_chunk_insert_replay_conflict_and_rollback(self):
        env, context = orm_env()
        env['a'].update(context=context, start=0, stop=m.CHUNK_JOBS, scale=1)
        project = copy.deepcopy(env['Project'].records)
        self.assertEqual(execute(env, m.CHUNK_BODY), {'skipped':False})
        self.assertEqual({name: len(env[name].records) for name in KINDS},
            dict(Group=25, Job=2500, Attempt=2500, Report=2525, Finding=2525, Member=2525))
        # Set in the same chunk: every job points at its own current report.
        for job in env['Job'].records.values():
            report = env['Report'].records[job['current_report_id']]
            self.assertEqual((report['job_id'], report['is_current']), (job['id'], True))
        original = {name: copy.deepcopy(env[name].records) for name in KINDS}
        self.assertEqual(execute(env, m.CHUNK_BODY), {'skipped':True})
        self.assertEqual(original, {name: env[name].records for name in KINDS})
        self.assertEqual(project, env['Project'].records)
        # Same IDs/counts do not make a changed relation a matching replay.
        member = next(iter(env['Member'].records))
        env['Member'].records[member]['finding_id'] = None
        with self.assertRaisesRegex(AssertionError, 'incompatible existing Member'):
            execute(env, m.CHUNK_BODY)
        env['Member'].records = copy.deepcopy(original['Member'])
        # A replay also refuses a job whose current report link was lost.
        env['Job'].records[next(iter(env['Job'].records))]['current_report_id'] = None
        with self.assertRaisesRegex(AssertionError, 'incompatible existing Job'):
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
                self.assertEqual(fake.calls[-1][0], s.DJANGO_PREFIX+m.COHORTS_PROGRAM)
                measured.append(entry['table'])
                return dict(engine='postgres', table='public.'+entry['table'], rows=222222, bytes=7654321)
            result = p.seed_reference_tables(entries, driver.seed_reference_table, measure)
            self.assertEqual([row['rows'] for row in result], [222222]*len(entries))
            self.assertEqual(measured, [entry['table'] for entry in entries])
            programs = Counter(program for program, args in fake.calls)
            # Each table callback runs the same full bounded verification.
            self.assertEqual(programs, Counter({PROGRAMS[0]:1, PROGRAMS[1]:1, PROGRAMS[2]:40,
                PROGRAMS[3]:(VERIFY_CALLS-1)*len(entries), PROGRAMS[4]:len(entries)}))
            self.assertEqual(fake.calls[0][1], ARGS)
            self.assertEqual(len(driver.corpus['reference_seed_operations']), 1)
            operation = driver.corpus['reference_seed_operations'][0]
            self.assertEqual((operation['table'], operation['operation'], operation['chunks'], operation['cohorts'],
                operation['verify_calls'], len(operation['verify_seconds'])),
                ('tracer_trace_investigation_family', 'investigation_reference_fixture', 40, fake.plan['cohorts'],
                 VERIFY_CALLS, VERIFY_CALLS))
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
            # A repeat call is one full bounded verification and no writes.
            self.assertEqual([program for program, args in fake.calls[calls:]],
                [PROGRAMS[3]]*(VERIFY_CALLS-1) + [PROGRAMS[4]])
            self.assertEqual(len(driver.corpus['reference_seed_operations']), 1)

    def test_bad_counts_and_unknown_cohorts_never_complete(self):
        mutations = [lambda value: None, lambda value: dict(value, counts={}), lambda value: dict(value, seeded_counts={}),
            lambda value: dict(value, cohorts={}),
            lambda value: dict(value, cohorts=dict(value['cohorts'], backfill_memberships=0)),
            lambda value: dict(value, cohorts=dict(value['cohorts'], unexpected=1)),
            lambda value: dict(value, seeded_counts={k: float(v) for k, v in value['seeded_counts'].items()}),
            lambda value: dict(value, counts=m.investigation_reference_plan(1)['counts']),
            lambda value: dict(value, counts=dict(value['counts'], tracer_trace_investigation_job=99999)),
            lambda value: dict(value, counts=None), lambda value: dict(value, extra=1)]
        slices = [lambda value: None, lambda value: {}, lambda value: {'verified':m.VERIFY_JOBS-1},
            lambda value: {'verified':True}, lambda value: dict(value, extra=1)]
        for hook, change in [('alter_verified', change) for change in mutations] + [('alter_slice', change) for change in slices]:
            fake = FakeApp()
            setattr(fake, hook, change)
            driver = driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(100000)
            self.assertFalse(hasattr(driver, '_investigation_reference'))
            self.assertNotIn('reference_seed_operations', driver.corpus)
            setattr(fake, hook, lambda value: value)
            driver.seed_investigation_reference_family(100000)
            self.assertEqual(fake.skipped, list(range(0, 100000, 2500)))
        fake = FakeApp(scale=2)
        fake.alter_verified = lambda value: dict(value, seeded_counts=dict(value['seeded_counts'],
            tracer_trace_investigation_job=0))
        with self.assertRaises(p.ManualRelease): driver_for(fake).seed_investigation_reference_family(200000)

    def test_invalid_prepare_rows_and_context_fail_closed(self):
        for change in (lambda value: None, lambda value: dict(value, context=None),
                lambda value: dict(value, context=1)):
            fake = FakeApp()
            fake.alter_prepared = change
            driver = driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(100000)
            self.assertEqual(len(fake.calls), 1)
            self.assertFalse(hasattr(driver, '_investigation_reference_pending'))
        for change in (lambda value: None, lambda value: dict(value, counts={}),
                lambda value: dict(value, seeded_counts={}),
                lambda value: dict(value, counts=dict(value['counts'], tracer_trace_investigation_job=True)),
                lambda value: dict(value, seeded_counts=dict(value['seeded_counts'], tracer_trace_investigation_job=1000000))):
            fake = FakeApp()
            fake.alter_baseline = change
            driver = driver_for(fake)
            with self.assertRaises(p.ManualRelease): driver.seed_investigation_reference_family(100000)
            self.assertEqual(len(fake.calls), 2)
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
        # Read-only work per call is fixed: baseline and cohort reads use one
        # UUID bucket per scale block, while payload slices stay 10,000 jobs.
        programs = Counter(program for program, args in fake.calls)
        self.assertEqual((programs[PROGRAMS[3]], programs[PROGRAMS[4]]), (2*(VERIFY_CALLS-1), 2))
        self.assertEqual(sorted(args['bucket'] for program, args in fake.calls if program == PROGRAMS[4]), [0, 1])
        self.assertTrue(all(args['stop']-args['start'] == m.VERIFY_JOBS for program, args in fake.calls
            if program == PROGRAMS[3]))
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
        writes = [index for index, program in enumerate(calls) if program in (PROGRAMS[0], PROGRAMS[2])]
        self.assertTrue(writes)
        self.assertLess(max(writes), first)
        self.assertEqual(sum(program == PROGRAMS[0] for program in calls), 1)
        self.assertEqual(sum(program == PROGRAMS[2] for program in calls), 40)
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

    def test_source_namespaces_give_disjoint_ids_and_regeneration_is_stable(self):
        first, again, other = payload_env(), payload_env(), payload_env(namespace='reference-other-source')
        self.assertNotEqual(first['NAMESPACE'], other['NAMESPACE'])
        generated = [env['payloads'](fixture_context(env), start, start + m.CHUNK_JOBS)
            for env in (first, again, other) for start in (0, 97500)]
        # The same namespace regenerates byte-identical payloads, so replay and
        # every later verify compare against exactly what was written.
        self.assertEqual(generated[:2], generated[2:4])
        self.assertEqual(first['project_payload'](fixture_context(first)), again['project_payload'](fixture_context(again)))
        ids = lambda batches: {row['id'] for rows in batches for name in KINDS for row in rows[name]}
        self.assertEqual(len(ids(generated[:2])), sum(len(rows[name]) for rows in generated[:2] for name in KINDS))
        self.assertFalse(ids(generated[:2]) & ids(generated[4:]))
        # Issue codes repeat across namespaces, but they are unique per project
        # and each namespace has its own project.
        self.assertNotEqual(first['identity']('project', 0), other['identity']('project', 0))
        self.assertEqual({row['project_id'] for rows in generated[4:] for row in rows['Group']},
            {other['identity']('project', 0)})

    def test_attempt_uniqueness_must_be_physical_unconditional_and_single_column(self):
        function = next(node for node in ast.parse(m.SCHEMA_CHECK).body
            if isinstance(node, ast.FunctionDef) and node.name == 'single_column_unique')

        def decide(rows):
            cursor = mock.MagicMock()
            cursor.fetchall.return_value = rows
            connection = mock.MagicMock()
            connection.cursor.return_value.__enter__.return_value = cursor
            env = {'connection': connection}
            exec(compile(ast.Module(body=[function], type_ignores=[]), '<schema-unique>', 'exec'), env)
            return env['single_column_unique']('tracer_trace_investigation_report', 'attempt_id'), cursor
        primary = (True, True, None, None, ['id'])
        # Regression: only the FK (and its plain index) remains, while Django
        # metadata still says one_to_one. That must refuse before any write.
        only_fk = [primary, (False, True, None, None, ['attempt_id'])]
        for name, rows in (('only_fk', only_fk), ('no_index', [primary]), ('nothing', []),
                ('partial', [primary, (True, True, '(deleted = false)', None, ['attempt_id'])]),
                ('expression', [primary, (True, True, None, 'lower((attempt_id)::text)', [])]),
                ('composite', [primary, (True, True, None, None, ['attempt_id', 'job_id'])]),
                ('invalid', [primary, (True, False, None, None, ['attempt_id'])]),
                ('other_column', [primary, (True, True, None, None, ['job_id'])])):
            with self.subTest(case=name):
                self.assertIs(decide(rows)[0], False)
        accepted, cursor = decide([primary, (False, True, None, None, ['attempt_id']),
            (True, True, None, None, ['attempt_id'])])
        self.assertIs(accepted, True)
        sql, params = cursor.execute.call_args.args
        self.assertEqual(params, ['tracer_trace_investigation_report'])
        for text in ('pg_index', 'indpred', 'indexprs', 'indisvalid', 'indnkeyatts', 'to_regclass(%s)'):
            self.assertIn(text, sql)
        # The source check requires it for Report.attempt before any write.
        required = [node for node in ast.walk(ast.parse(m.SCHEMA_CHECK)) if isinstance(node, ast.Assert)
            and isinstance(node.test, ast.Call) and getattr(node.test.func, 'id', None) == 'single_column_unique']
        self.assertEqual(len(required), 1)
        self.assertIn("Report._meta.get_field('attempt').column", ast.unparse(required[0].test))
        self.assertLess(m.PREPARE.index('attempt uniqueness drift'), m.PREPARE.index('transaction.atomic()'))

    def test_source_membership_table_must_not_have_target_only_columns(self):
        raw = (ROOT/'futureagi/tracer/migrations/0117_feed_escalation_policy.py').read_text()
        added = {ast.literal_eval(keyword.value) for node in ast.walk(ast.parse(raw)) if isinstance(node, ast.Call)
            and getattr(node.func, 'attr', None) == 'AddField'
            and any(k.arg == 'model_name' and ast.literal_eval(k.value) == 'errorclustertraces' for k in node.keywords)
            for keyword in node.keywords if keyword.arg == 'name'}
        self.assertEqual(added, set(m.TARGET_ONLY_MEMBER_COLUMNS))
        self.assertFalse(added & set(m.SOURCE_FIELDS['Member'].split()))
        self.assertIn("name!='Member' or not columns&set(TARGET_ONLY_MEMBER_COLUMNS)", m.SCHEMA_CHECK)
        self.assertLess(m.PREPARE.index('target-only physical column'), m.PREPARE.index('transaction.atomic()'))

    def test_buckets_partition_the_uuid_space_and_refuse_bad_indices(self):
        class Recorded:
            def __init__(self, filters=()): self.filters = filters
            def filter(self, **kwargs): return Recorded(self.filters + tuple(kwargs.items()))
        env = {'a': {}, 'uuid': uuid}
        exec(m.BUCKET, env)
        for scale in (1, 2, 3, 1000):
            bounds = []
            for index in range(scale):
                env['a'] = dict(scale=scale, buckets=scale, bucket=index)
                bounds.append(dict(env['bucket'](Recorded()).filters))
            self.assertEqual(bounds[0]['pk__gte'], uuid.UUID(int=0))
            self.assertNotIn('pk__lt', bounds[-1])
            for left, right in zip(bounds, bounds[1:]):
                self.assertEqual(left['pk__lt'], right['pk__gte'])
                self.assertLess(left['pk__gte'], left['pk__lt'])
        for bad in (dict(scale=2, buckets=1, bucket=0), dict(scale=2, buckets=2, bucket=2),
                dict(scale=1, buckets=1, bucket=True), dict(scale=1, buckets=1, bucket=-1)):
            env['a'] = bad
            with self.subTest(arguments=bad), self.assertRaisesRegex(AssertionError, 'invalid bucket'):
                env['bucket'](Recorded())
        for start, stop, scale in ((5000, 15000, 1), (0, 20000, 1), (100000, 110000, 1), (0, 10000, 0), (True, 10001, 1)):
            env['a'] = dict(scale=scale)
            with self.subTest(start=start, stop=stop, scale=scale), self.assertRaises(AssertionError):
                env['verify_slice'](start, stop)
        env['a'] = dict(scale=2)
        self.assertEqual(list(env['verify_slice'](190000, 200000)), list(range(190000, 200000, 2500)))

    def test_occurrence_oracle_matches_the_target_plan(self):
        env = payload_env()
        context = fixture_context(env)
        states = Counter()
        for start in range(0, 100000, m.CHUNK_JOBS):
            rows = env['payloads'](context, start, start + m.CHUNK_JOBS)
            expected = env['occurrences'](rows)
            self.assertEqual(set(expected), {row['id'] for row in rows['Member']})
            deleted = {row['id'] for row in rows['Member'] if row['deleted']}
            for key, (occurred, source, ended) in expected.items():
                if source:
                    self.assertEqual((source, occurred), ('omega_root_end', ended))
                    self.assertIsNotNone(ended)
                    states['filled_memberships'] += 1
                    states['filled_deleted_memberships'] += key in deleted
                else:
                    self.assertEqual((occurred, ended), (None, None))
                    states['null_memberships'] += 1
        self.assertEqual(states['filled_memberships'] + states['null_memberships'], SCALE_ONE_TARGET['memberships'])
        self.assertEqual({key: states[key] for key in ('filled_memberships', 'filled_deleted_memberships',
            'null_memberships')}, {key: SCALE_ONE_TARGET[key] for key in ('filled_memberships',
            'filled_deleted_memberships', 'null_memberships')})

    def test_actual_target_slice_counts_every_wrong_or_missing_membership(self):
        class Members:
            def __init__(self, store): self.store, self.pks = store, None
            def filter(self, pk__in, **filters):
                self.assert_project(filters)
                self.pks = pk__in; return self
            def assert_project(self, filters):
                assert set(filters) == {'cluster__project_id'}
                self.expected_project = filters['cluster__project_id']
            def values_list(self, *fields):
                assert fields == ('id', 'occurred_at', 'occurrence_time_source', 'finding__report__job__root_end_time')
                return [(pk,) + self.store[pk] for pk in self.pks if pk in self.store]

        def run(start, change=None):
            env = payload_env()
            context = fixture_context(env)
            exec(m.BUCKET, env)
            store = {}
            for begin in range(start, start + m.VERIFY_JOBS, m.CHUNK_JOBS):
                rows = env['payloads'](context, begin, begin + m.CHUNK_JOBS)
                deleted = {row['id'] for row in rows['Member'] if row['deleted']}
                store.update({key: (value, key in deleted) for key, value in env['occurrences'](rows).items()})
            if change: store = change(store)
            env.update(context=context, json=json, Member=SimpleNamespace(all_objects=Members(
                {key: value for key, (value, deleted) in store.items()})))
            env['a'].update(context=context, scale=1, start=start, stop=start + m.VERIFY_JOBS)
            result = execute(env, m.TARGET_SLICE_BODY)
            self.assertEqual(env['Member'].all_objects.expected_project, env['identity']('project',0))
            return result, store
        # 60,000-70,000: live and soft-deleted eligible chains plus legacy rows.
        # 90,000-100,000: no root end time, findings without membership, legacy.
        for start in (60000, 90000):
            result, store = run(start)
            self.assertEqual(result, dict(checked=len(store), mismatched=0))
        self.assertEqual((len(run(60000)[1]), len(run(90000)[1])), (10000 + 100, 5000 + 100))
        # 0118 did nothing: all 10,000 eligible rows in the slice stay NULL.
        nothing = lambda store: {key: (((None, '', value[2]) if value[1] else value), deleted)
            for key, (value, deleted) in store.items()}
        self.assertEqual(run(60000, nothing)[0]['mismatched'], 10000)
        # Runtime repair alone (live rows only) still leaves the deleted cohort.
        live_only = lambda store: {key: (((None, '', value[2]) if value[1] and deleted else value), deleted)
            for key, (value, deleted) in store.items()}
        self.assertEqual(run(60000, live_only)[0]['mismatched'], 1000)
        later = lambda store: {key: (((value[0] + timedelta(seconds=1), value[1], value[2]) if value[1] else value),
            deleted) for key, (value, deleted) in store.items()}
        self.assertEqual(run(60000, later)[0]['mismatched'], 10000)
        # Negative controls (no root end time, legacy) must stay NULL.
        negative = lambda store: {key: (((datetime(2026, 1, 1, tzinfo=timezone.utc), 'trace_end', value[2])
            if not value[1] else value), deleted) for key, (value, deleted) in store.items()}
        self.assertEqual(run(90000, negative)[0]['mismatched'], 5000 + 100)
        missing = lambda store: dict(list(store.items())[1:])
        result, store = run(90000, missing)
        self.assertEqual(result, dict(checked=len(store) + 1, mismatched=1))

    def test_target_check_runs_bounded_reads_and_fails_when_the_backfill_is_wrong(self):
        fake = FakeApp()
        driver = driver_for(fake, cls=q.QualificationRehearsal)
        driver.reference_registry = copy.deepcopy(list(ENTRIES))
        driver.seed_investigation_reference_family(100000)
        calls = len(fake.calls)
        m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
        self.assertEqual([program for program, args in fake.calls[calls:]],
            [TARGET_PROGRAMS[0]]*(VERIFY_CALLS-1) + [TARGET_PROGRAMS[1]])
        self.assertEqual([args['start'] for program, args in fake.calls[calls:-1]], list(range(0, 100000, m.VERIFY_JOBS)))
        record = driver.corpus['investigation_reference_target_check']
        self.assertEqual({key: record[key] for key in ('scale', 'slices', 'buckets', 'checked', 'mismatched', 'cohorts')},
            dict(scale=1, slices=VERIFY_CALLS-1, buckets=1, checked=96000, mismatched=0, cohorts=SCALE_ONE_TARGET))
        for secret in ('Authorization', 'SECRET', 'password', 'DO-NOT-FORWARD'):
            self.assertNotIn(secret, json.dumps([args for program, args in fake.calls]))
        # A wrong migrated value is a failed assertion, not an unavailable seeder.
        wrong = [('alter_target_slice', lambda value: dict(value, mismatched=1)),
            ('alter_target', lambda value: dict(value, filled_memberships=69000, null_memberships=27000)),
            ('alter_target', lambda value: dict(value, filled_deleted_memberships=0)),
            ('alter_target', lambda value: dict(value, memberships=95999))]
        for hook, change in wrong:
            setattr(fake, hook, change)
            with self.subTest(hook=hook), self.assertRaises(p.RehearsalError) as caught:
                m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
            self.assertNotIsInstance(caught.exception, p.ManualRelease)
            setattr(fake, hook, lambda value: value)
        unavailable = [('alter_target_slice', lambda value: None), ('alter_target_slice', lambda value: dict(value, checked=1)),
            ('alter_target_slice', lambda value: dict(value, mismatched=-1)), ('alter_target', lambda value: None),
            ('alter_target', lambda value: dict(value, extra=1)), ('alter_target', lambda value: dict(value, memberships=True))]
        for hook, change in unavailable:
            setattr(fake, hook, change)
            with self.subTest(hook=hook), self.assertRaisesRegex(p.ManualRelease, 'high_volume_reference_unavailable'):
                m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
            setattr(fake, hook, lambda value: value)
        m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)

    def test_target_check_requires_the_seeded_family_only_when_it_was_selected(self):
        driver = driver_for(mock.Mock(), cls=q.QualificationRehearsal)
        m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
        driver.reference_registry = [row for row in p.load_high_volume_registry() if row['table'] not in TABLES]
        m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
        driver.app_python.assert_not_called()
        driver.reference_registry = copy.deepcopy(list(ENTRIES[:1]))
        with self.assertRaisesRegex(p.ManualRelease, 'high_volume_reference_unavailable'):
            m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
        driver._investigation_reference_pending = dict(arguments=dict(ARGS), context=dict(CONTEXT))
        driver._investigation_reference = dict(arguments=dict(ARGS), context=dict(CONTEXT))
        with self.assertRaisesRegex(p.ManualRelease, 'high_volume_reference_unavailable'):
            m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
        driver.app_python.assert_not_called()
        # Scale follows the seeded rows: two blocks read twice the slices and buckets.
        fake = FakeApp(scale=2)
        driver = driver_for(fake)
        driver.seed_investigation_reference_family(200000)
        calls = len(fake.calls)
        m.verify_investigation_backfill(driver, prefix=s.DJANGO_PREFIX, producer=p)
        programs = Counter(program for program, args in fake.calls[calls:])
        self.assertEqual(programs, Counter({TARGET_PROGRAMS[0]: 2*(VERIFY_CALLS-1), TARGET_PROGRAMS[1]: 2}))
        self.assertEqual(driver.corpus['investigation_reference_target_check']['cohorts'],
            {key: 2*value for key, value in SCALE_ONE_TARGET.items()})

    def test_qualification_runs_the_target_check_inside_large_volume_migrations(self):
        from deploy.tests.test_update_qualification_assertions import migration_driver
        for wrong in (False, True):
            driver, cases = migration_driver()
            fake = FakeApp()
            driver.app_python = fake
            driver.reference_registry = copy.deepcopy(list(ENTRIES))
            driver._investigation_reference = dict(arguments=dict(ARGS), context=dict(CONTEXT))
            if wrong:
                fake.alter_target = lambda value: dict(value, filled_deleted_memberships=0)
                with self.assertRaisesRegex(p.RehearsalError, 'investigation backfill cohorts differ on target'):
                    driver.migration_suite()
                self.assertEqual(cases.failed, ('migration_forward', 'large_volume_migrations'))
                driver.witnesses.create.assert_not_called()
            else:
                driver.migration_suite()
                self.assertIsNone(cases.failed)
                self.assertIn('large_volume_migrations', cases.cases['migration_forward'])
                self.assertEqual(Counter(program for program, args in fake.calls),
                    Counter({TARGET_PROGRAMS[0]: VERIFY_CALLS-1, TARGET_PROGRAMS[1]: 1}))
        # The reference inventory is still asserted first.
        driver = object.__new__(q.QualificationRehearsal)
        order = []
        driver.assert_reference_inventory = lambda: order.append('reference')
        with mock.patch.object(q, 'verify_investigation_backfill', side_effect=lambda d, **kw: order.append((d, kw))):
            self.assertIsNone(driver.assert_large_volume_migrations())
        self.assertEqual(order, ['reference', (driver, dict(prefix=s.DJANGO_PREFIX, producer=p))])


if __name__ == '__main__':
    unittest.main()
