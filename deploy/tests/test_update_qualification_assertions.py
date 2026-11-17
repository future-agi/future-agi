"""Synthetic callback integration through the real strict ExecutedCases sink.

These tests execute validators/assertion wiring, not Docker or qualification.
"""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy.tests.test_update_qualification_executor import envelope_fixture
from deploy.tests.test_update_qualify_assembly import SyntheticExecutor
from deploy.tests.update_e2e.real_automatic import ProductHarness
from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_qualification as q


def driver_fixture():
    fixture = SyntheticExecutor()
    driver = object.__new__(q.QualificationRehearsal)
    cases = p.ExecutedCases()
    driver.sink = SimpleNamespace(check=cases.check, reference=[])
    driver.row, driver.target = fixture.row, fixture.target
    driver.row.release.manifest_sha256 = p.manifest_hash(driver.row.release.manifest)
    driver.corpus = copy.deepcopy(fixture.corpus)
    witnesses = p.witness_inventory(driver.corpus['source_witnesses'])
    driver.corpus.update(source_witnesses=witnesses, source_witnesses_rechecked=copy.deepcopy(witnesses))
    driver.registry = p.load_high_volume_registry()
    driver._source_migrations = dict(pending=[], applied=[], planned=[])
    driver.ledger = SimpleNamespace(backup_seal=object(), phase='restored')
    driver.artifact = SimpleNamespace(seal=driver.ledger.backup_seal,
        proof=SimpleNamespace(covered_domains=p.topology_profile('standalone', ()).backup_domains))
    driver._primary_snapshot = copy.deepcopy(driver.corpus['source_snapshot_inventory'])
    driver.binding = SimpleNamespace(raw=json.dumps(driver._primary_snapshot['binding']))
    driver.engine = SimpleNamespace(automatic_outcome='restored')
    driver.current_source_witnesses = mock.Mock(side_effect=lambda: copy.deepcopy(witnesses))
    driver.verify_restored_source = mock.Mock(return_value=SimpleNamespace(
        facts=SimpleNamespace(runtime_observation=SimpleNamespace(ready=True, stable=True))))
    driver.witnesses = SimpleNamespace(assert_absent=mock.Mock(return_value=True))
    return driver, cases


def migration_driver():
    driver, cases = driver_fixture()
    fields, corpus, target, refs = envelope_fixture()
    driver.corpus.update(corpus)
    driver.target = target
    driver._bootstrap_measurement = copy.deepcopy(fields['migration_measurements'])
    driver.migration_evidence = lambda: copy.deepcopy(fields['migration_evidence'])
    pending = [[row['app_label'], row['name']] for row in corpus['target_pending_plan']]
    driver._target_migrations = dict(pending=[], applied=pending, inventory_sha256='c'*64)
    driver.target_plan = dict(planned=copy.deepcopy(pending), inventory_sha256='c'*64)
    driver.sink.reference = copy.deepcopy(list(refs.values()))
    driver.corpus['large_volume_reference'] = dict(snapshot_generation='a'*64,
        registry_sha256=p.sha((p.ROOT/'deploy/update/high-volume-tables.json').read_bytes()),
        reference_rows=p.REFERENCE_ROWS, tables=copy.deepcopy(driver.sink.reference))
    target_witnesses = [dict(row, identity='target/'+row['identity']) for row in driver.corpus['source_witnesses']]
    driver.witnesses.create = mock.Mock(return_value=copy.deepcopy(target_witnesses))
    driver.witnesses.read = mock.Mock(return_value=copy.deepcopy(target_witnesses))
    driver.spool_witness = SimpleNamespace(observe_bootstrap_delivery=mock.Mock(return_value=None))
    return driver, cases


class QualificationAssertionTests(unittest.TestCase):
    def assert_suite(self, cases, suite, omitted=()):
        required = p.required_cases(suite, p.topology_profile('standalone', ())) - set(omitted)
        self.assertEqual(set(cases.cases[suite]), required)
        self.assertIsNone(cases.failed)
        self.assertTrue(all(row['outcome']=='passed' for row in cases.timings))

    def test_source_callbacks_validate_data_then_return_none(self):
        driver, cases = driver_fixture()
        driver.source_suite()
        self.assert_suite(cases, 'source_snapshot')
        # Completing one suite is never full affirmative qualification.
        with self.assertRaisesRegex(p.ManualRelease, 'suite_unrun'):
            cases.complete()

    def test_invalid_or_changed_source_namespace_still_raises_and_latches_failure(self):
        for change in ('missing_domain', 'changed_payload'):
            driver, cases = driver_fixture()
            if change == 'missing_domain':
                driver.corpus['source_witnesses_rechecked'].pop()
            else:
                driver.corpus['source_witnesses_rechecked'][0]['payload_sha256'] = 'f'*64
            with self.subTest(change=change), self.assertRaises(p.RehearsalError):
                driver.source_suite()
            self.assertEqual(cases.failed, ('source_snapshot', 'witness_namespace_complete'))
            self.assertNotIn('witness_namespace_complete', cases.cases['source_snapshot'])
            with self.assertRaisesRegex(p.ManualRelease, 'suite_failed'):
                cases.complete()

    def test_source_reference_must_match_prepared_measurements_even_when_empty(self):
        driver, cases = driver_fixture()
        driver.sink.reference = [dict(engine='postgres', table='public.unmeasured', rows=100000, bytes=1)]
        with self.assertRaisesRegex(p.RehearsalError, 'prepared reference'):
            driver.source_suite()
        self.assertEqual(cases.failed, ('source_snapshot', 'large_volume_reference'))

    def test_migration_callbacks_use_real_scope_envelope_and_reference_validators(self):
        driver, cases = migration_driver()
        driver.migration_suite()
        self.assert_suite(cases, 'migration_forward')
        driver.spool_witness.observe_bootstrap_delivery.assert_called_once_with('source')
        driver.witnesses.create.assert_called_once_with('target')
        driver.witnesses.read.assert_called_once_with('target')

    def test_resource_validator_failure_or_boolean_result_cannot_become_a_pass(self):
        for result in (False, True):
            driver, cases = migration_driver()
            with self.subTest(result=result), mock.patch.object(q, 'validate_bootstrap_envelope', return_value=result), \
                 self.assertRaisesRegex(p.RehearsalError, 'validated bootstrap measurement differs'):
                driver.migration_suite()
            self.assertEqual(cases.failed, ('migration_forward', 'migration_resource_measurements'))
            driver.witnesses.create.assert_not_called()
        driver, cases = migration_driver()
        driver.corpus['bootstrap_envelope']['ordered_plan_sha256'] = 'f'*64
        with self.assertRaises(p.RehearsalError):
            driver.migration_suite()
        self.assertEqual(cases.failed, ('migration_forward', 'migration_resource_measurements'))

    def test_changed_reference_or_underseeding_still_refuses(self):
        for change in ('prepared', 'underseeded'):
            driver, cases = migration_driver()
            if change == 'prepared': driver.sink.reference[0]['bytes'] += 1
            else: driver.corpus['large_volume_reference']['tables'][0]['rows'] -= 1
            with self.subTest(change=change), self.assertRaises(p.RehearsalError):
                driver.assert_reference_inventory()

    def test_restore_callbacks_return_none_and_reobserve_source_readiness(self):
        for suite in ('failure_restore', 'interruption_restore'):
            driver, cases = driver_fixture()
            with self.subTest(suite=suite), mock.patch.object(ProductHarness, 'migration_inventory',
                                                            return_value=driver._source_migrations):
                driver.restore_assertions(suite)
            omitted = ('failure_after_migration',) if suite=='failure_restore' else (
                'updater_process_terminated', 'fresh_recovery_process', 'durable_boundaries')
            self.assert_suite(cases, suite, omitted)
            driver.verify_restored_source.assert_called_once_with()
            driver.witnesses.assert_absent.assert_called_once_with('target')

    def test_real_absence_oracle_returns_true_and_void_callback_checks_it(self):
        from scripts.update_rehearsal_shipping import ProductWitnesses
        for remaining in (None, 'relational', 'span', 'object', 'temporal', 'spool'):
            driver, cases = driver_fixture()
            io_driver = SimpleNamespace(
                app_python=mock.Mock(side_effect=[
                    dict(user=remaining=='relational',organization=False,workspace=False,project=False),
                    {'absent':remaining!='object'}, {'absent':remaining!='temporal'}]),
                clickhouse=mock.Mock(return_value=[{'id':'target-span'}] if remaining=='span' else []),
                spool_witness=SimpleNamespace(assert_absent=mock.Mock(return_value=remaining!='spool')))
            driver.witnesses = ProductWitnesses(io_driver)
            driver.witnesses.namespaces['target'] = dict(account={'email':'unit@example.invalid'},
                headers={'X-Organization-Id':'unit-org','X-Workspace-Id':'unit-workspace'},
                namespace='unit-target', trace_id='unit-trace',span_id='unit-span',object={},workflow={'run_id':'unit-run'})
            with self.subTest(remaining=remaining), mock.patch.object(ProductHarness, 'migration_inventory',
                                                                    return_value=driver._source_migrations):
                if remaining is None:
                    driver.restore_assertions('failure_restore')
                    self.assert_suite(cases, 'failure_restore', ('failure_after_migration',))
                    self.assertEqual(io_driver.app_python.call_count, 3)
                    io_driver.spool_witness.assert_absent.assert_called_once_with('target')
                else:
                    with self.assertRaises(p.RehearsalError):
                        driver.restore_assertions('failure_restore')
                    self.assertEqual(cases.failed, ('failure_restore', 'candidate_only_absent'))
                    self.assertNotIn('candidate_only_absent', cases.cases['failure_restore'])
        for verdict in (False, None, 1, {'absent':True}):
            driver, cases = driver_fixture()
            driver.witnesses.assert_absent.return_value = verdict
            with self.subTest(verdict=verdict), mock.patch.object(ProductHarness, 'migration_inventory',
                                                                return_value=driver._source_migrations), \
                 self.assertRaisesRegex(p.RehearsalError, 'absence was not observed'):
                driver.restore_assertions('failure_restore')
            self.assertEqual(cases.failed, ('failure_restore', 'candidate_only_absent'))

    def test_unready_or_unstable_restored_source_never_counts_as_ready(self):
        for ready, stable in ((False, True), (True, False)):
            driver, cases = driver_fixture()
            observed = driver.verify_restored_source.return_value.facts.runtime_observation
            observed.ready, observed.stable = ready, stable
            with self.subTest(ready=ready, stable=stable), self.assertRaisesRegex(p.RehearsalError, 'ready and stable'):
                driver.restore_assertions('failure_restore')
            self.assertEqual(cases.failed, ('failure_restore', 'source_ready'))
            driver.witnesses.assert_absent.assert_not_called()

    def test_restored_namespace_difference_still_fails_at_the_real_sink(self):
        driver, cases = driver_fixture()
        changed = copy.deepcopy(driver.corpus['source_witnesses'])
        changed[0]['payload_sha256'] = 'f'*64
        driver.current_source_witnesses.side_effect = lambda: changed
        with mock.patch.object(ProductHarness, 'migration_inventory', return_value=driver._source_migrations), \
             self.assertRaisesRegex(p.RehearsalError, 'restored witness namespace changed'):
            driver.restore_assertions('failure_restore')
        self.assertEqual(cases.failed, ('failure_restore', 'restored_namespace_complete'))


class TargetApplicationMappingTests(unittest.TestCase):
    def setUp(self):
        from deploy.update.standalone_state import load_migration_mapping
        self.mapping = load_migration_mapping()
        self.active = [(row, Path('/app/backend')/row['source_root']) for row in self.mapping['apps']
                       if row['location']=='backend' and row['condition']=='always']
        self.actual = [SimpleNamespace(label=row['label'], name=row['module'], path=str(root))
                       for row, root in self.active]

    def run_registry_check(self, actual):
        from deploy.update.docker_host import fixed_source
        from scripts.update_rehearsal_shipping import DJANGO_PREFIX
        fixed = fixed_source('standalone.py').decode('utf-8')
        self.assertEqual(q.target_app_mapping_probe(), DJANGO_PREFIX+fixed+q.TARGET_APP_MAPPING_CHECK)
        # The fixed AST-based active_apps probe has its own source-fixture tests.
        # Here exercise the real comparison/digest code against a Django registry
        # double, without importing application code into the test host.
        namespace = {'a':self.mapping}
        exec(compile(fixed, 'fixed-standalone-probe', 'exec'), namespace)
        namespace['active_apps'] = mock.Mock(return_value=self.active)
        django_apps = SimpleNamespace(apps=SimpleNamespace(get_app_configs=lambda:actual))
        output = io.StringIO()
        with mock.patch.dict('sys.modules', {'django.apps':django_apps}), redirect_stdout(output):
            exec(compile(q.TARGET_APP_MAPPING_CHECK, 'target-app-mapping-check', 'exec'), namespace)
        namespace['active_apps'].assert_called_once_with(self.mapping)
        return json.loads(output.getvalue())

    def test_real_registry_check_hashes_the_same_structured_mapping_as_the_host(self):
        result = self.run_registry_check(list(reversed(self.actual)))
        self.assertEqual(result, {'active_app_mapping_sha256':p.manifest_hash(self.mapping)})
        raw_configs = [vars(row) for row in self.actual]
        self.assertNotEqual(result['active_app_mapping_sha256'], p.manifest_hash(raw_configs))

    def test_registry_label_module_path_presence_and_duplicates_must_match(self):
        for change in ('label','name','path','extra','missing','duplicate'):
            actual = copy.deepcopy(self.actual)
            if change in ('label','name','path'): setattr(actual[0], change, 'different')
            elif change == 'extra': actual.append(SimpleNamespace(label='unknown',name='unknown',path='/other'))
            elif change == 'missing': actual.pop()
            else: actual.append(copy.deepcopy(actual[0]))
            with self.subTest(change=change), self.assertRaisesRegex(AssertionError, 'registry differs'):
                self.run_registry_check(actual)

    def test_target_ready_checks_the_fixed_mapping_and_source_binding(self):
        driver, _ = driver_fixture()
        driver.corpus['source_runtime_observation']['active_app_mapping_sha256'] = p.manifest_hash(self.mapping)
        def execute(source, argument):
            self.assertEqual(source, q.target_app_mapping_probe())
            self.assertEqual(argument, self.mapping)
            return self.run_registry_check(self.actual)
        driver.app_python = mock.Mock(side_effect=execute)
        migrations = dict(applied=[],pending=[],planned=[])
        with mock.patch.object(ProductHarness, 'migration_inventory', return_value=migrations):
            self.assertIsNone(driver.target_ready(object(), {}))
            self.assertEqual(driver._target_migrations, migrations)
            driver.corpus['source_runtime_observation']['active_app_mapping_sha256'] = 'f'*64
            with self.assertRaisesRegex(p.RehearsalError, 'differs from source'):
                driver.target_ready(object(), {})
            driver.app_python.side_effect = None
            driver.app_python.return_value = {'active_app_mapping_sha256':'e'*64}
            with self.assertRaisesRegex(p.RehearsalError, 'fixed host contract'):
                driver.target_ready(object(), {})


if __name__ == '__main__':
    unittest.main()
