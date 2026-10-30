"""Synthetic callback integration through the real strict ExecutedCases sink.

These tests execute validators/assertion wiring, not Docker or qualification.
"""
import copy
import json
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
    driver.witnesses = SimpleNamespace(assert_absent=mock.Mock(return_value=None))
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


if __name__ == '__main__':
    unittest.main()
