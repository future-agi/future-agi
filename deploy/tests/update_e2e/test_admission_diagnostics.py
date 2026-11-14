"""Deterministic daemon-free diagnostics; never product qualification."""
import ast
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from deploy.tests.update_e2e import admission_diagnostics as diag
from deploy.tests.test_update_bootstrap_envelope_contract import fixture
from deploy.tests.test_update_standalone_state import FakePGResult
from deploy.update.runner_state import StoreBusy


def reference():
    return diag.reference_summary(fixture()[2])


def catalog(count=1, size=32, **kwargs):
    return dict(database_matches_config=True, current_schema_matches_reference=True,
                schema_exists=True, relation_exists=True, kind='r', reltuples=count, bytes=size, **kwargs)


class WiringTests(unittest.TestCase):
    def test_daemon_free_ci_executes_diagnostic_suite(self):
        source = (ROOT/'.github/workflows/update-ci.yml').read_text()
        step = source.split('      - name: Daemon-free real harness, TLS publisher and native CI plans\n', 1)[1]
        step = step.split('\n  compose:', 1)[0]
        self.assertIn("          python -m unittest discover -s deploy/tests/update_e2e "
                      "-p 'test_admission_diagnostics.py' -v", step.splitlines())

    def test_failure_collection_does_not_replay_a_tick(self):
        source = (ROOT/'deploy/tests/test_update_e2e_automatic.py').read_text()
        self.assertNotIn('diagnostic_scheduler_replay', source)
        self.assertIn('collect_admission_diagnostics(', source)

    def test_teardown_and_completed_block_check_are_used(self):
        source = (ROOT/'deploy/tests/test_update_e2e_automatic.py').read_text()
        self.assertIn('finish_acceptance(', source)
        self.assertIn('completed_admission_block(', source)

    def test_both_artifact_allowlists_retain_only_new_summaries(self):
        source = (ROOT/'.github/workflows/update-ci.yml').read_text()
        for name in ('migration-reference.json', 'admission-diagnostics.json', 'teardown-diagnostics.json'):
            self.assertEqual(source.count('/'+name), 2)
        for name in ('/proof.json', '/compatibility.json', '/product.log', '/command-*.stderr'):
            self.assertNotIn(name, source)


class ProjectionTests(unittest.TestCase):
    def project(self, row, **extra):
        return diag.closed_probe_result(json.dumps(dict(catalog=[row], **extra)).encode(), reference())

    def test_unknown_zero_and_oversized_are_not_interchangeable(self):
        for value, state in ((-1, 'unknown'), (0, 'estimated_zero'), (42.5, 'estimated'),
                             (10**100, 'invalid'), (float('nan'), 'invalid'), (True, 'invalid')):
            with self.subTest(value=value):
                result = self.project(catalog(value))['postgres'][0]
                self.assertEqual(result['estimate_state'], state)
                self.assertEqual(result['reltuples'], None if state == 'invalid' else value)
        from deploy.update.contracts import HostTableSize
        for value in (-1, 10**100):
            with self.assertRaises(ValueError):
                HostTableSize('postgres', 'public.synthetic_table', value, 32)
        self.assertEqual(HostTableSize('postgres', 'public.synthetic_table', 0, 32).rows, 0)

    def test_missing_schema_relation_and_other_kind_are_distinct(self):
        for updates, state in ((dict(schema_exists=False, relation_exists=False), 'missing_schema'),
                               (dict(relation_exists=False), 'missing_relation'),
                               (dict(kind='v'), 'unsupported_kind')):
            row = catalog(); row.update(updates)
            self.assertEqual(self.project(row)['postgres'][0]['estimate_state'], state)

    def test_actual_identity_mismatches_are_retained_as_booleans_only(self):
        row = catalog(); row.update(database_matches_config=False, current_schema_matches_reference=False,
                                    database='/private/password', schema='secret')
        result = self.project(row)['postgres'][0]
        self.assertFalse(result['database_matches_config'])
        self.assertFalse(result['current_schema_matches_reference'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('/private', json.dumps(result))

    def test_arbitrary_errors_paths_frames_and_rows_cannot_escape(self):
        result = self.project(catalog(), error='/private/token', shipping_refusal={
            'reason_code': 'restore_capacity_unknown', 'message': 'password', 'frames': [
                dict(module='/private', function='secret', line=2),
                dict(module='standalone_probe', function='postgres_table_sizes', line=376, path='/private')]})
        self.assertEqual(result['shipping_table_observer']['frames'], [dict(
            module='standalone_probe', function='postgres_table_sizes', line=376)])
        self.assertNotIn('/private', json.dumps(result))
        self.assertNotIn('password', json.dumps(result))
        self.assertEqual(diag.failure_category(type('SecretException', (Exception,), {})('/private')), 'collection_failed')

    def test_output_and_reference_limits_fail_closed(self):
        self.assertEqual(diag.closed_probe_result(b'x'*(diag.MAX_OUTPUT+1), reference()), {'error':'output_limit'})
        for raw in (b'[]', b'not JSON', b'{"catalog": ["private"]}'):
            self.assertEqual(diag.closed_probe_result(raw, reference()), {'error':'invalid_probe_output'})
        row = fixture()[2]
        row['migration_measurements']['pending_plan'] *= diag.MAX_ITEMS+1
        with self.assertRaises(ValueError):
            diag.reference_summary(row)

    def test_authenticated_scope_and_reference_are_preserved_not_proof_extras(self):
        row = fixture(3)[2]
        row['private'] = '/private/token'
        result = diag.reference_summary(row)
        self.assertEqual(len(result['migrations']), 3)
        self.assertEqual(result['migrations'][0]['tables'], [{'engine':'postgres','table':'public.synthetic_table'}])
        self.assertEqual(result['tables'][0]['reference_rows'], 100000)
        self.assertEqual(result['tables'][0]['reference_bytes'], 1000)
        self.assertNotIn('/private', json.dumps(result))
        self.assertFalse(result['qualification_evidence'])
        self.assertEqual(result['sample'], 'authenticated_reference_not_host_sample')


class ProbeTests(unittest.TestCase):
    def probe(self, count, *, actual_count=17, schema=True, found=True, kind='r'):
        # Execute the shipping read-only table API, NOT a substitute estimate.
        namespace = {}
        source = diag.fixed_source('standalone.py').decode() + '\n' + diag.CATALOG_PROBE.rsplit('diagnostic_main()', 1)[0]
        exec(compile(source, '<fixed-probe>', 'exec'), namespace)
        calls = []
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            pgresult = None
            def execute(self, sql, params=None, **kwargs):
                calls.append((sql, params)); self.sql = sql
                if isinstance(self.pgresult, FakePGResult):
                    self.pgresult.expired = True
                self.pgresult = None
                if sql.startswith('SELECT t.tableoid'):
                    assert kwargs == {'prepare': False}
                    if isinstance(actual_count, Exception):
                        raise actual_count
                    if not schema or not found or kind in ('v', 'f'):
                        raise namespace['ProbeError']('restore_capacity_unknown')
                    self.pgresult = FakePGResult()
            def fetchone(self):
                return ('63',) if self.sql == 'SHOW max_identifier_length' else ('on',)
            def fetchmany(self, limit):
                if limit != 2: raise AssertionError('unbounded fetch')
                if 'current_database()' in self.sql: return [(True, True, schema)]
                if not schema or not found: return []
                if 'c.relkind,c.reltuples' in self.sql: return [(kind, count, 32)]
                if 'pg_catalog.pg_class' in self.sql:
                    assert calls[-1][1] == (12345,)
                    return [(kind, 'p', False, False, 'public', 'synthetic_table', False, 32)]
                if self.sql.startswith('SELECT t.tableoid'):
                    return [] if type(actual_count) is int and actual_count == 0 else [(12345, actual_count)]
                raise AssertionError('unexpected query')
        class DB:
            info = SimpleNamespace(server_version=160006)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def cursor(self): return Cursor()
            rollback = Mock()
        db = DB()
        namespace['postgres_connection'] = lambda: db
        namespace['fixed_environment'] = lambda: None
        raw = json.dumps(dict(postgres=['public.synthetic_table'], clickhouse=[])).encode()
        output = io.StringIO()
        with patch.object(namespace['signal'], 'alarm') as alarm, patch.dict(os.environ, {'PG_DB': 'private-db'}), \
             patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw))), contextlib.redirect_stdout(output):
            namespace['diagnostic_main']()
        alarm.assert_called_once_with(30)
        self.assertEqual(db.rollback.call_count, 2)
        result = diag.closed_probe_result(output.getvalue().encode(), reference())
        return result, calls

    def test_catalog_retains_unknown_while_shipping_measures_empty_and_nonempty_counts(self):
        for actual in (0, 17):
            with self.subTest(actual=actual):
                result, calls = self.probe(-1, actual_count=actual)
                self.assertEqual(result['postgres'][0]['estimate_state'], 'unknown')
                self.assertEqual(result['postgres'][0]['reltuples'], -1)
                self.assertEqual(result['shipping_table_observer'], dict(status='measured', tables=[dict(
                    engine='postgres', table='public.synthetic_table', rows=actual, bytes=32)]))
                self.assertIn(('public', 'synthetic_table'), [p for _, p in calls])
                counts = [sql for sql, _ in calls if 'synthetic_table' in sql]
                self.assertEqual(counts, ['SELECT t.tableoid, pg_catalog.count(*) FROM ONLY "public"."synthetic_table" AS t '
                                          'TABLESAMPLE pg_catalog.system(100) GROUP BY t.tableoid LIMIT 2'])
                self.assertEqual(sum(sql == 'SHOW transaction_read_only' for sql, _ in calls), 2)
                self.assertTrue(all(sql.startswith(('SET TRANSACTION', 'SET LOCAL', 'SHOW ', 'SELECT ')) for sql, _ in calls))

    def test_stale_positive_and_zero_catalog_estimates_do_not_replace_actual_counts(self):
        for estimated, actual, state in ((0, 100001, 'estimated_zero'), (1, 100001, 'estimated'),
                                         (99, 0, 'estimated')):
            with self.subTest(estimated=estimated, actual=actual):
                result, _ = self.probe(estimated, actual_count=actual)
                self.assertEqual(result['postgres'][0]['estimate_state'], state)
                self.assertEqual(result['postgres'][0]['reltuples'], estimated)
                self.assertEqual(result['shipping_table_observer']['tables'][0]['rows'], actual)
                self.assertEqual(result['shipping_table_observer']['tables'][0]['bytes'], 32)

    def test_failed_count_keeps_catalog_but_never_reports_it_as_a_shipping_measurement(self):
        for actual in (PermissionError('private'), TimeoutError('private'), LookupError('private'), -1, True, '17'):
            with self.subTest(actual=actual):
                result, calls = self.probe(0, actual_count=actual)
                self.assertEqual(result['postgres'][0]['estimate_state'], 'estimated_zero')
                shipping = result['shipping_table_observer']
                self.assertEqual(shipping['status'], 'unavailable' if isinstance(actual, Exception) else 'refused')
                self.assertNotIn('tables', shipping)
                self.assertNotIn('private', json.dumps(result))
                self.assertEqual(sum('count(*)' in sql for sql, _ in calls), 1)

    def test_unsupported_kind_never_publishes_measurements(self):
        for kind in ('p', 'v', 'f'):
            result, calls = self.probe(0, kind=kind)
            self.assertEqual(result['postgres'][0]['estimate_state'], 'unsupported_kind')
            self.assertEqual(result['shipping_table_observer']['status'], 'refused')
            self.assertEqual(sum('count(*)' in sql for sql, _ in calls), 1)

    def test_missing_schema_or_relation_refuses_shipping_without_fake_zero(self):
        for schema in (True, False):
            result, _ = self.probe(0, schema=schema, found=False)
            self.assertEqual(result['postgres'][0]['estimate_state'], 'missing_relation' if schema else 'missing_schema')
            self.assertEqual(result['shipping_table_observer']['status'], 'refused')

    def test_source_has_finite_reads_and_no_database_mutation(self):
        source = diag.CATALOG_PROBE
        self.assertIn('read(32769)', source)
        self.assertIn('fetchmany(2)', source)
        self.assertIn('signal.alarm(30)', source)
        self.assertNotIn('COUNT(', source)
        self.assertNotIn('ANALYZE', source)
        self.assertNotIn('django', source)
        shipping = diag.fixed_source('standalone.py').decode()
        self.assertIn('connect_timeout=5', shipping)
        self.assertIn('statement_timeout=5000', shipping)
        self.assertIn('lock_timeout=1000', shipping)
        self.assertIn('signal.alarm(45)', shipping)
        self.assertIn('SELECT t.tableoid, pg_catalog.count(*) FROM ONLY', shipping)
        self.assertIn('TABLESAMPLE pg_catalog.system(100)', shipping)

    def test_real_compose_discovery_defaults_to_registered_environment(self):
        from deploy.update import orchestrator
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.docker_host import ReadOnlyRunner
        binding = SimpleNamespace(docker_context='registered', checkout=Path('/unused'),
                                  production=False, env_alias=Path('/unused/.env'))
        launcher = SimpleNamespace(home=Path('/registered-home'), path='/registered-bin')
        store = SimpleNamespace(read_state=lambda: SimpleNamespace(active_attempt_id=None),
            read_registration=lambda: SimpleNamespace(binding=binding, launcher=launcher))
        configuration = SimpleNamespace(policy=AutomationPolicy())
        explicit = {'HOME': '/registered-home', 'DOCKER_CONTEXT': 'registered', 'EXPLICIT': 'overlay'}
        inst = SimpleNamespace(docker=['docker', '--context', 'registered'], root=binding.checkout,
                               subprocess_env=lambda: explicit)
        runtime = (None, {'app': {'id': 'app-id'}}, None)
        # Run real registered_installation + Compose discovery; mock only its
        # installation/binding fixtures and the bounded runner's subprocess boundary.
        for fallback in (False, True):
            calls = []
            bounded = ReadOnlyRunner(diag.TIMEOUT, max_output=diag.MAX_OUTPUT)
            deadline = bounded.deadline
            def run(runner, args, **kwargs):
                calls.append((runner, args, kwargs))
                if args[-2:] == ['version', '--short']:
                    code = 1 if fallback and args[0] == 'docker' else 0
                    return SimpleNamespace(returncode=code, stdout='v2.24.7')
                return SimpleNamespace(returncode=0, stdout=b'{"catalog":[],"shipping_tables":[]}')
            with self.subTest(fallback=fallback), \
                 patch.dict(os.environ, {'HOME': '/ambient-home', 'PATH': '/ambient-bin',
                    'DOCKER_HOST': 'tcp://unwanted', 'DOCKER_CONTEXT': 'ambient', 'PYTHONPATH': '/injected'}), \
                 patch.object(diag, 'idle_read_locks', return_value=contextlib.nullcontext()), \
                 patch.object(diag, 'ReadOnlyRunner', return_value=bounded) as make, \
                 patch.object(ReadOnlyRunner, '__call__', autospec=True, side_effect=run), \
                 patch('shutil.which', side_effect=lambda name, *, path: path+'/'+name) as which, \
                 patch.object(orchestrator, 'Installation', return_value=inst) as installation, \
                 patch.object(orchestrator, 'check_binding'), \
                 patch('deploy.update.docker_host.checked_daemon'), \
                 patch('deploy.update.standalone_state._runtime', return_value=runtime):
                result = diag.observe_admission(store, configuration, dict(tables=[]))
                adapter = installation.call_args.kwargs['runner']
                adapter(['explicit-empty-env'], env={})
            make.assert_called_once_with(40, max_output=128*1024)
            self.assertTrue(result['runtime_unchanged'])
            self.assertEqual(len(calls), 4 if fallback else 3)
            for runner, _, _ in calls:
                self.assertIs(runner, bounded)
                self.assertEqual(runner.deadline, deadline)
                self.assertEqual(runner.max_output, 128*1024)
            for _, _, kwargs in calls[:-2]:
                env = kwargs.get('env')
                self.assertIsNotNone(env, 'Compose discovery must not inherit ambient environment')
                self.assertEqual(env, installation.call_args.kwargs['environ'])
                self.assertEqual(env['HOME'], '/registered-home')
                self.assertEqual(env['PATH'], '/registered-bin')
                self.assertEqual(env['DOCKER_CONTEXT'], 'registered')
                self.assertFalse(set(env) & {'DOCKER_HOST', 'PYTHONPATH'})
            self.assertIs(calls[-2][2]['env'], explicit)
            self.assertEqual(calls[-1][2]['env'], {})
            self.assertTrue(all(call.kwargs['path'] == '/registered-bin' for call in which.call_args_list))

    def test_host_probe_uses_only_inspection_and_existing_app_exec(self):
        binding = SimpleNamespace(docker_context='unit')
        store = SimpleNamespace(read_state=lambda: SimpleNamespace(active_attempt_id=None),
            read_registration=lambda: SimpleNamespace(binding=binding, launcher=SimpleNamespace(home=Path.home(), path=os.defpath)))
        from deploy.update.contracts import AutomationPolicy
        configuration = SimpleNamespace(policy=AutomationPolicy())
        inst = SimpleNamespace(docker=['docker', '--context', 'unit'], root=Path('/unused'), subprocess_env=lambda: {})
        bounded = Mock(return_value=SimpleNamespace(stdout=b'{"catalog":[],"shipping_tables":[]}', returncode=0))
        runtime = (None, {'app': {'id': 'app-id'}}, None)
        selected = dict(tables=[dict(engine='postgres', table='qualified_schema.exact_table'),
                                dict(engine='clickhouse', table='qualified_db.other_table')])
        with patch.object(diag, 'idle_read_locks', return_value=contextlib.nullcontext()), \
             patch.object(diag, 'ReadOnlyRunner', return_value=bounded) as make, \
             patch.object(diag, 'registered_installation', return_value=inst), \
             patch('deploy.update.docker_host.checked_daemon'), \
             patch('deploy.update.standalone_state._runtime', return_value=runtime):
            result = diag.observe_admission(store, configuration, selected)
        make.assert_called_once_with(40, max_output=128*1024)
        args = bounded.call_args.args[0]
        self.assertEqual(args[:6], ['docker', '--context', 'unit', 'exec', '-i', 'app-id'])
        self.assertEqual(args[7:10], ['-I', '-B', '-S'])
        self.assertEqual(bounded.call_count, 1)
        self.assertEqual(json.loads(bounded.call_args.kwargs['input']), {
            'postgres': ['qualified_schema.exact_table'], 'clickhouse': ['qualified_db.other_table']})
        self.assertTrue(result['runtime_unchanged'])
        source = Path(diag.__file__).read_text()
        tree = ast.parse(source)
        observe = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'observe_admission')
        methods = {n.func.attr for n in ast.walk(observe) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertFalse(methods & {'run', 'apply', 'retry', 'setup', 'cleanup', 'measure_update_sizes'})


class ExerciseFailureTests(unittest.TestCase):
    def exercise_failure(self, *, stage, diagnostic_error=False, status_error=False):
        from deploy.tests import test_update_e2e_automatic as automatic
        from deploy.update.contracts import AutomationPolicy
        row = fixture()[2]
        migration = row['migration_evidence']
        original = TimeoutError('restore seam timeout') if stage != 'wait' else AssertionError('scheduler timeout')
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            checkout = Mock(root=root, evidence=root/'evidence', context='registered')
            checkout.evidence.mkdir()
            checkout.install.return_value = dict(seconds=1)
            checkout.bin_update.return_value = SimpleNamespace(
                stdout=automatic.AUTOMATIC_UPDATE_DISCLOSURE.encode(), returncode=0)
            checkout.containers.return_value = [dict(Config={'Labels': {'com.docker.compose.service': 'app'}},
                                                     Image='source-image')]
            checkout.migration_inventory.return_value = dict(inventory_sha256=migration['source_inventory_sha256'],
                                                            applied_sha256=migration['source_applied_set_sha256'])
            checkout.fail_after_migrations.return_value = dict(pending=[],
                inventory_sha256=migration['target_inventory_sha256'], applied_sha256=migration['target_applied_set_sha256'])
            if stage == 'restore':
                checkout.fail_after_migrations.side_effect = original
            elif stage == 'install':
                checkout.install.side_effect = original
            if status_error:
                def record(name, result):
                    if name == 'public-status.json' and result.get('sample'):
                        raise OSError('unavailable')
                checkout.record.side_effect = record
            publisher = Mock(source='test-source', public_key=root/'key', ca_file=root/'ca',
                             _prepared={'v1.56.2': {'manifest': {}}})
            publisher.new_checkout.return_value = checkout
            store = Mock(path=root/'state')
            store.read_registration.return_value = SimpleNamespace(binding=SimpleNamespace(slot_id='unit'))
            status_dir = checkout.evidence/'public-status'/'unit'
            status_dir.mkdir(parents=True)
            (status_dir/'status.json').write_text('{"discovery":{"state":"blocked"}}')
            policy = AutomationPolicy()
            configuration = SimpleNamespace(policy=policy, scheduler_options={})
            baseline = Mock()
            baseline.current.return_value = SimpleNamespace(release=SimpleNamespace(version='v1.56.1'))
            def mock(name, **kwargs):
                return stack.enter_context(patch.object(automatic, name, **kwargs))
            mock('selected_store', return_value=store)
            mock('BaselineStore', return_value=baseline)
            mock('load_setup_configuration', return_value=configuration)
            mock('inspect_activation', return_value=SimpleNamespace(active=True))
            mock('acceptance_policy', return_value=policy)
            mock('scheduler_wait_budget', return_value=60)
            mock('setup_timeout_seconds', return_value=60)
            mock('policy_cli_arguments', return_value=())
            mock('data_usage', return_value={'kib': {}})
            history = mock('scheduler_history', return_value={'schema_version': 1, 'qualification_evidence': False})
            collect = mock('collect_admission_diagnostics', wraps=diag.collect_admission_diagnostics,
                           side_effect=RuntimeError('diagnostic unavailable') if diagnostic_error else None)
            stack.enter_context(patch.object(diag, 'observe_admission', return_value={'error': 'busy'}))
            wait = stack.enter_context(patch.object(automatic.e2e, 'wait_for', side_effect=original))
            case = automatic.AutomaticLifecycleE2E('test_one_opt_in_scheduled_A_B_C')
            with self.assertRaises(type(original)) as raised:
                case.exercise(dict(deadline_monotonic=automatic.time.monotonic()+1000, mode='upgrade'),
                    ('v1.56.1', 'v1.56.2'), publisher, {'v1.56.2': row}, policy, restore=True)
            self.assertIs(raised.exception, original)
            if stage != 'wait':
                wait.assert_not_called()
            else:
                wait.assert_called_once()
            records = {call.args[0]: call.args[1] for call in checkout.record.call_args_list}
            self.assertNotIn('acceptance-result.json', records)
            self.assertNotIn('restored-source.json', records)
            if stage == 'install':
                collect.assert_not_called()
                history.assert_not_called()
            else:
                collect.assert_called_once_with(checkout, store, configuration, row)
                history.assert_called_once()
                self.assertEqual(records['scheduler-history.json']['sample'], 'after_failure_not_original_tick')
                self.assertEqual(records['public-status.json']['sample'], 'after_failure_not_original_tick')
                self.assertEqual(records['public-status.json']['status']['discovery']['state'], 'blocked')
                if not diagnostic_error:
                    self.assertEqual(records['admission-diagnostics.json']['error'], 'busy')
            self.assertEqual(records['teardown-diagnostics.json']['original_failure'], diag.failure_category(original))

    def test_restore_seam_timeout_collects_before_scheduler_wait(self):
        self.exercise_failure(stage='restore')

    def test_scheduler_wait_failure_collects_exactly_once(self):
        self.exercise_failure(stage='wait')

    def test_diagnostic_or_status_failure_preserves_restore_timeout_and_history(self):
        self.exercise_failure(stage='restore', diagnostic_error=True, status_error=True)

    def test_early_failure_without_selected_inputs_does_not_sample(self):
        self.exercise_failure(stage='install')


class AdmissionAndTeardownTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.status = dict(discovery=dict(state='blocked', candidate_version='v1.56.2', checked_at=self.now.isoformat(),
                                         reason_codes=['restore_capacity_unknown']), operation=dict(state='idle', started_at=None))
        self.state = SimpleNamespace(active_attempt_id=None)
        self.store = SimpleNamespace(read_state=lambda: self.state)

    def blocked(self):
        return diag.completed_admission_block(self.status, self.state, 'v1.56.2',
                                             checked_after=self.now-timedelta(seconds=1), store=self.store)

    def test_both_restore_capacity_refusals_fail_fast_only_when_completed(self):
        with patch.object(diag, 'idle_read_locks', return_value=contextlib.nullcontext()):
            for code in diag.ADMISSION:
                self.status['discovery']['reason_codes'] = [code]
                self.assertTrue(self.blocked())
            self.status['operation'] = self.operation('check', 'blocked', seconds=0)
            self.assertTrue(self.blocked())
            self.status['operation']['finished_at'] = None
            self.assertFalse(self.blocked())

    def operation(self, kind='check', state='succeeded', *, seconds=-10):
        from deploy.update.contracts import OperationEvent
        from deploy.update.public_status import operation_projection
        finished = self.now + timedelta(seconds=seconds)
        return operation_projection(OperationEvent(state, kind, None, finished, 'private'),
                                    started_at=finished-timedelta(seconds=5), finished_at=finished)

    def test_previous_terminal_check_and_apply_allow_fresh_refusal(self):
        with patch.object(diag, 'idle_read_locks', return_value=contextlib.nullcontext()):
            for kind in ('check', 'apply'):
                for seconds in (-10, 0):
                    with self.subTest(kind=kind, seconds=seconds):
                        self.status['operation'] = self.operation(kind, seconds=seconds)
                        self.assertTrue(self.blocked())

    def test_new_active_or_ambiguous_operation_never_fails_fast(self):
        invalid = [self.operation('apply', 'applying', seconds=1),
                   self.operation(seconds=1), self.operation('check', 'blocked', seconds=1),
                   self.operation('apply', 'recovery_required'), self.operation('apply', 'staged'),
                   self.operation('apply', 'unknown')]
        for updates in (dict(started_at=None), dict(finished_at=None), dict(finished_at='invalid'),
                        dict(finished_at=42), dict(started_at=self.now.isoformat()),
                        dict(kind='invented'), dict(state='idle'),
                        dict(finished_at=self.now.replace(tzinfo=None).isoformat())):
            invalid.append(dict(self.operation(), **updates))
        with patch.object(diag, 'idle_read_locks', return_value=contextlib.nullcontext()):
            for operation in invalid:
                with self.subTest(operation=operation):
                    self.status['operation'] = operation
                    self.assertFalse(self.blocked())
            self.status['operation'] = self.operation('apply')
            self.store.read_state = lambda: SimpleNamespace(active_attempt_id='new-active-attempt')
            self.assertFalse(self.blocked())

    def test_active_operation_attempt_busy_or_stale_candidate_never_fail_fast(self):
        with patch.object(diag, 'idle_read_locks', return_value=contextlib.nullcontext()):
            self.status['operation'] = dict(state='applying', kind='apply', finished_at=None)
            self.assertFalse(self.blocked())
            self.status['operation'] = dict(state='idle', started_at=None)
            self.state.active_attempt_id = 'private'
            self.assertFalse(self.blocked())
            self.state.active_attempt_id = None
            self.status['discovery']['candidate_version'] = 'v1.56.1'
            self.assertFalse(self.blocked())
            self.status['discovery']['candidate_version'] = 'v1.56.2'
            self.status['discovery']['checked_at'] = (self.now-timedelta(days=1)).isoformat()
            self.assertFalse(self.blocked())
            self.status['discovery']['checked_at'] = self.now.isoformat()
        with patch.object(diag, 'idle_read_locks', side_effect=StoreBusy()):
            self.assertFalse(self.blocked())

    def test_busy_diagnostic_is_one_attempt_and_never_runs_a_command(self):
        checkout = Mock()
        with patch.object(diag, 'idle_read_locks', side_effect=StoreBusy()) as lock, \
             patch.object(diag, 'ReadOnlyRunner') as runner:
            result = diag.collect_admission_diagnostics(checkout, self.store, None, fixture()[2])
        lock.assert_called_once(); runner.assert_not_called()
        self.assertEqual(result['error'], 'busy')
        self.assertEqual(result['sample'], 'after_failure_not_original_tick')
        self.assertFalse(result['qualification_evidence'])

    def test_failed_deactivation_preserves_original_and_skips_cleanup(self):
        checkout = Mock()
        original = AssertionError('original')
        deactivate = Mock(side_effect=RuntimeError('/private/deactivation-token'))
        with self.assertRaises(AssertionError) as raised:
            try:
                raise original
            finally:
                diag.finish_acceptance(checkout, failure=original, deactivate=deactivate)
        self.assertIs(raised.exception, original)
        checkout.cleanup.assert_not_called()
        name, result = checkout.record.call_args.args
        self.assertEqual(name, 'teardown-diagnostics.json')
        self.assertEqual(result['original_failure'], 'assertion_failed')
        self.assertEqual(result['deactivation'], 'failed')
        self.assertEqual(result['cleanup'], 'skipped')
        self.assertNotIn('/private', json.dumps(result))

    def test_teardown_failures_cannot_turn_a_passing_test_into_success(self):
        for stage in ('collect_diagnostics', 'collect_logs', 'cleanup', 'record'):
            checkout = Mock()
            error = RuntimeError('private')
            getattr(checkout, stage).side_effect = error
            with self.assertRaises(RuntimeError) as raised:
                diag.finish_acceptance(checkout, failure=None, deactivate=Mock())
            self.assertIs(raised.exception, error)
        checkout = Mock()
        with self.assertRaises(RuntimeError):
            diag.finish_acceptance(checkout, failure=None, deactivate=Mock(side_effect=RuntimeError('private')))
        checkout.cleanup.assert_not_called()

    def test_evidence_failures_also_preserve_original(self):
        checkout = Mock()
        for name in ('collect_diagnostics', 'collect_logs', 'record'):
            getattr(checkout, name).side_effect = RuntimeError('private')
        result = diag.finish_acceptance(checkout, failure=AssertionError(), deactivate=Mock(side_effect=RuntimeError()))
        checkout.cleanup.assert_not_called()
        self.assertEqual(result['original_failure'], 'assertion_failed')
        self.assertEqual(len(result['errors']), 4)

    def test_existing_lock_is_nonblocking_and_not_created_when_missing(self):
        from deploy.update.runner_state import RunnerStateStore
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = root/'.env'; env.touch()
            store = RunnerStateStore(root, env)
            state = root/'.futureagi-updates'; state.mkdir(mode=0o700)
            automation = state/'automation'; automation.mkdir(mode=0o700)
            for path in (state/'update.lock', automation/'runner.lock'):
                path.touch(mode=0o600)
            with diag.idle_read_locks(store):
                with self.assertRaises(StoreBusy):
                    with store.runner_lock(): pass
            (automation/'runner.lock').unlink()
            with self.assertRaises(FileNotFoundError):
                with diag.idle_read_locks(store): pass
            self.assertFalse((automation/'runner.lock').exists())


if __name__ == '__main__':
    unittest.main()
