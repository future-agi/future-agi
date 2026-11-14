"""Daemon-free qualification plumbing. ALL positive data here is SYNTHETIC.

These tests exercise real producer/packager/contract objects, not product images.
No Docker, network, production keys, release uploads or scheduler changes occur.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from deploy.tests.test_update_automatic_release import (
    complete_snapshot_fields, default_v2, restore_release, snapshot_evidence_fixture,
    transfer_fixture,
)
from deploy.tests.test_update_release_static import load, one_step, runs_containing
from deploy.update.apply import Interrupted, Updater
from deploy.update.compatibility import (CompatibilityError, SUITES, shape, schema, topology_profile,
    validate_release_proof)
from deploy.update.manifest import validate_manifest
from deploy.update.manifest import canonical_json, manifest_hash
from deploy.update.standalone_restore import StandaloneRestore
from scripts import build_update_compatibility_proof as packager
from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_milestone as stages


def role_records(corpus):
    """Authored SYNTHETIC records; these are never product measurements."""
    return {action: dict(snapshot_generation=corpus[action + '_transfer']['snapshot_generation'],
        measurement_basis=p.TRANSFER_BASIS, roles={role: dict(started_monotonic=10,
            completed_monotonic=13, logical_bytes=size, throughput_bytes_per_second=size/3)
            for role, size in corpus[action + '_transfer']['reference_volume_bytes'].items()})
        for action in ('backup', 'restore')}


class SyntheticExecutor:
    """In-memory adapter only, with shipping-format synthetic raw evidence."""
    shipping_executor_class, shipping_restore_class = Updater, StandaloneRestore
    cleanup_verified = False

    def __init__(self):
        _, old_raw, proof, _ = restore_release(pending_count=0)
        self.target = default_v2()
        old = json.loads(old_raw)
        self.row = p.Row(SimpleNamespace(manifest=old, raw_manifest=old_raw,
            raw_bundle=b'SYNTHETIC-NOT-A-SIGNATURE'), copy.deepcopy(old['deployment']),
            old['source_sha'], tuple(proof['predecessors'][0]['dependencies']))
        self.fields = {key: copy.deepcopy(proof['predecessors'][0][key])
            for key in packager.MEASURED_FIELDS if key not in ('executor_sha256', 'evidence_inventory_sha256')}
        binding = self.row.binding()
        binding.update(self.fields)
        complete_snapshot_fields(binding)
        self.row.execution_deployment['files'].update(binding['execution_deployment']['files'])
        self.dependencies = self.row.dependencies
        self.corpus = snapshot_evidence_fixture(binding, target=self.target, predecessor_raw=old_raw)
        generation = p.sha(canonical_json(self.corpus['source_snapshot_inventory']))
        transfer = transfer_fixture(self.fields, self.corpus['source_snapshot_inventory'])
        origins = {'postgres_app': 'product_api', 'clickhouse': 'collector_ingestion',
            'object_store': 'bundled_s3_api', 'temporal': 'completed_product_workflow',
            'shared_files': 'product_spool'}
        witnesses = [dict(domain=domain, origin=origin, identity='SYNTHETIC-ONLY-' + domain,
            payload_sha256='1'*64) for domain, origin in origins.items()]
        self.corpus.update(source_witnesses=witnesses, restored_witnesses=copy.deepcopy(witnesses),
            resource_samples=[], target_pending_plan=[], pending_migration_tables=[],
            backup_transfer=transfer, restore_transfer=copy.deepcopy(transfer),
            large_volume_reference=dict(snapshot_generation=generation, reference_rows=p.REFERENCE_ROWS,
                registry_sha256=p.sha((p.ROOT/'deploy/update/high-volume-tables.json').read_bytes()), tables=[]),
            pending_table_scope_probe=dict(program_sha256=p.sha(p.target_table_scope_probe().encode()),
                target_image_digest=self.target['components']['standalone']['digest'],
                target_inventory_sha256=self.fields['migration_evidence']['target_inventory_sha256'],
                source_applied_sha256=self.fields['migration_evidence']['source_applied_set_sha256'],
                database_schemas=dict(postgres='public', clickhouse='default'), pending_migration_tables=[]))
        self.corpus['volume_role_transfers'] = role_records(self.corpus)

    def pending_migration_tables(self): return []
    def seed_reference_table(self, entry, rows):
        raise AssertionError('a measured empty plan must not seed unrelated tables')
    def measure_reference_table(self, entry):
        raise AssertionError('a measured empty plan must not measure unrelated tables')
    def measurements(self): return copy.deepcopy(self.fields)
    def private_evidence(self): return copy.deepcopy(self.corpus)
    def diagnostics(self): return dict(purpose='SYNTHETIC-UNIT-ONLY')


def all_assertions(session):
    layout = topology_profile('standalone', ())
    called = []
    for suite in SUITES:
        for name in sorted(packager.required_cases(suite, layout)):
            def assertion(s=suite, n=name):
                called.append((s, n))
            session.check(suite, name, assertion)
    return called


class SessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.executor = SyntheticExecutor()
        self.session = p.QualificationSession(self.executor.row, self.executor.target,
            self.root/'rows', run_id=42, deadline=time.monotonic()+60, registry=p.load_high_volume_registry())

    def test_zero_milestone_exit_or_status_json_cannot_qualify_unrun_suites(self):
        (self.root/'milestone.json').write_text('{"status":"passed","eligible":true}')
        with self.assertRaisesRegex(p.ManualRelease, 'suite_unrun'):
            self.session.finish()
        self.assertFalse(self.session.shipping_executor_available)
        self.assertEqual(list((self.root/'rows').iterdir()), [])

    def test_executed_assertions_only_then_raw_measurements_then_cleanup(self):
        self.session.prepare_reference(self.executor)
        called = all_assertions(self.session)
        self.assertEqual(len(called), sum(len(packager.required_cases(s, topology_profile('standalone', ()))) for s in SUITES))
        self.session.capture(self.executor)
        self.assertFalse(self.session.shipping_executor_available)
        with self.assertRaisesRegex(p.RehearsalError, 'cleanup'):
            self.session.finish()
        self.assertFalse((self.root/'rows/evidence.json').exists())
        self.executor.cleanup_verified = True
        self.session.finish()
        self.assertTrue(self.session.shipping_executor_available)
        envelope = json.loads((self.root/'rows/evidence.json').read_bytes())
        self.assertEqual(envelope['run_id'], 42)
        self.assertEqual(envelope['candidate_sha256'], p.sha(canonical_json(self.executor.target)))
        row = envelope['rows'][0]
        self.assertEqual(row['predecessor'], self.executor.row.binding()['predecessor'])
        self.assertEqual(set(row['reports']), set(SUITES))
        docs = packager.check_private_inventory(self.root/'rows', row)
        self.assertEqual(docs['0-qualification_driver.json']['value']['implementation_sha256'],
            p.sha((p.ROOT/'scripts/update_rehearsal_milestone.py').read_bytes()))
        for suite, report in row['reports'].items():
            xml = ET.fromstring((self.root/'rows'/report['path']).read_bytes())
            self.assertEqual({case.attrib['name'] for case in xml.findall('testcase')},
                packager.required_cases(suite, topology_profile('standalone', ())))
        # Exercise the real product packager/schema with ONLY its host signature
        # boundary stubbed. The files remain synthetic unit fixtures in temp.
        from deploy.update.trust import Verifier
        from deploy.update.settings import load_settings
        verifier = Verifier(load_settings(p.ROOT, environ={}), host_only=True)
        with mock.patch.object(verifier, 'verify', return_value=SimpleNamespace(verified=True)):
            proof, manifest = packager.build(self.executor.target,
                json.loads((self.root/'rows/policy.json').read_bytes()), self.root/'rows', run_id=42, verifier=verifier)
        shape(json.loads(proof), schema('compatibility-proof.schema.json'))
        shape(json.loads(manifest), schema('release-manifest.schema.json'))
        self.assertTrue(json.loads(manifest)['compatibility']['automatic_apply'])
        # Python validator parity with the JSON schemas on the SAME bytes.
        validated = validate_release_proof(proof, validate_manifest(json.loads(manifest)), repository=p.REPOSITORY)
        self.assertEqual(validated['run_id'], 42)
        self.assertEqual([r['predecessor']['raw_manifest_sha256'] for r in validated['predecessors']],
                         [p.sha(self.executor.row.release.raw_manifest)])

    def test_missing_each_suite_cannot_be_promoted(self):
        layout = topology_profile('standalone', ())
        for missing in SUITES:
            cases = p.ExecutedCases()
            for suite in SUITES:
                if suite != missing:
                    for name in packager.required_cases(suite, layout):
                        cases.check(suite, name, lambda: None)
            with self.subTest(missing=missing), self.assertRaisesRegex(p.ManualRelease, 'suite_unrun'):
                cases.complete()

    def test_failed_assertion_is_permanent_even_if_retried_successfully(self):
        self.session.prepare_reference(self.executor)
        def failed(): raise RuntimeError('SYNTHETIC assertion failure')
        with self.assertRaises(RuntimeError):
            self.session.check('source_snapshot', 'source_identity', failed)
        all_assertions(self.session)
        with self.assertRaisesRegex(p.ManualRelease, 'suite_failed'):
            self.session.capture(self.executor)
        with self.assertRaises(p.RehearsalError):
            self.session.executed_cases.render('source_snapshot', topology_profile('standalone', ()), {})
        self.assertFalse(self.session.shipping_executor_available)

    def test_unknown_duplicate_and_out_of_order_cases_cannot_run(self):
        self.session.prepare_reference(self.executor)
        callback = mock.Mock(return_value=None)
        for suite, name in (('target_readiness', 'health'), ('source_snapshot', 'made_up')):
            with self.subTest(suite=suite, name=name), self.assertRaises(p.RehearsalError):
                self.session.check(suite, name, callback)
        callback.assert_not_called()
        self.session.check('source_snapshot', 'source_identity', callback)
        with self.assertRaises(p.RehearsalError):
            self.session.check('source_snapshot', 'source_identity', callback)
        self.assertEqual(callback.call_count, 1)

    def test_every_missing_measurement_refuses_after_all_assertions(self):
        self.session.prepare_reference(self.executor)
        all_assertions(self.session)
        for missing, reason in (('pending_table_scope_probe', 'migration_measurement_unavailable'),
                                ('volume_role_transfers', 'transfer_measurement_unavailable')):
            original = self.executor.corpus.pop(missing)
            with self.subTest(missing=missing), self.assertRaisesRegex(p.ManualRelease, reason):
                self.session.capture(self.executor)
            self.executor.corpus[missing] = original
        self.assertFalse((self.root/'rows/evidence.json').exists())

    def test_capture_validates_and_retains_one_measurement_snapshot(self):
        self.session.prepare_reference(self.executor)
        all_assertions(self.session)
        with mock.patch.object(self.executor, 'measurements', wraps=self.executor.measurements) as measurements, \
             mock.patch.object(self.executor, 'private_evidence', wraps=self.executor.private_evidence) as evidence:
            self.session.capture(self.executor)
        self.assertEqual(measurements.call_count, 1)
        self.assertEqual(evidence.call_count, 1)

    def test_measurement_without_capture_and_expired_deadline_refuse(self):
        self.session.prepare_reference(self.executor)
        all_assertions(self.session)
        with self.assertRaisesRegex(p.ManualRelease, 'migration_measurement_unavailable'):
            self.session.finish()
        self.session.deadline = 0
        with self.assertRaisesRegex(p.ManualRelease, 'suite_timed_out'):
            self.session.capture(self.executor)

    def test_changed_product_harness_cannot_publish_captured_evidence(self):
        self.session.prepare_reference(self.executor)
        all_assertions(self.session)
        self.session.capture(self.executor)
        self.executor.cleanup_verified = True
        self.session.harness_sha256 = 'f'*64
        with self.assertRaisesRegex(p.RehearsalError, 'harness changed'):
            self.session.finish()
        self.assertFalse((self.root/'rows/evidence.json').exists())

    def test_changed_executor_and_implementation_are_not_qualified(self):
        self.session.prepare_reference(self.executor)
        all_assertions(self.session)
        with self.assertRaisesRegex(p.RehearsalError, 'executor changed'):
            self.session.capture(SyntheticExecutor())
        self.session.capture(self.executor)
        self.executor.cleanup_verified = True
        self.session.milestone_sha256 = 'e'*64
        with self.assertRaisesRegex(p.RehearsalError, 'implementation changed'):
            self.session.finish()
        self.assertFalse((self.root/'rows/evidence.json').exists())


@contextmanager
def six_suite_plan(name, source_image, target_image, platform='linux/amd64'):
    """Synthetic stage-only adapter: exactly one selected plan, no real executor."""
    real = stages.plan
    def plan(boot_timeout=4200, through='source_snapshot'):
        result = real(boot_timeout, through)
        if through == name:
            result.update(qualification_suites=dict(p.SIX_SUITES_PLANNED), source_image=source_image,
                          target_image=target_image, platform=platform)
        return result
    with mock.patch.object(stages, 'MILESTONES', stages.BOUNDED_MILESTONES), \
         mock.patch.object(stages, 'plan', side_effect=plan):
        yield


class ProtectedProducerTests(unittest.TestCase):
    @staticmethod
    def milestone(runner, *, planned=True):
        # Synthetic one-milestone contract; production still checks every plan.
        def run(name, args):
            if args.dry_run:
                args.out.mkdir(parents=True)
                (args.out/'job-plan.json').write_bytes(canonical_json(dict(milestone=name,
                    source_image=args.source_ref, target_image=args.target_ref, platform=args.platform,
                    qualification_suites={s: 'planned' if planned else 'unrun' for s in SUITES})))
                return 0
            return runner(name, args)
        return SimpleNamespace(MILESTONES=('synthetic_full',), run_milestone=run)

    def test_product_producer_dispatches_session_and_packages_bound_provenance(self):
        executor = SyntheticExecutor()
        dispatched = []

        def run(name, args):
            dispatched.append(name)
            session = args.qualification
            session.prepare_reference(executor)
            all_assertions(session)
            session.capture(executor)
            executor.cleanup_verified = True
            return 0

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(p, '_validate_context'), \
             mock.patch.object(p, 'milestone_api', return_value=self.milestone(run)):
            root = Path(tmp)
            p.produce(executor.target, [executor.row], root, root/'evidence', run_id=42)
            self.assertEqual(dispatched, ['synthetic_full'])
            envelope = json.loads((root/'evidence/evidence.json').read_bytes())
            docs = packager.check_private_inventory(root/'evidence', envelope['rows'][0])
            self.assertEqual(docs['0-qualification_driver.json']['value'], dict(run_id=42,
                candidate_sha256=p.sha(canonical_json(executor.target)),
                implementation_sha256=p.sha((p.ROOT/'scripts/update_rehearsal_milestone.py').read_bytes()),
                harness_sha256=p.sha((p.ROOT/'deploy/tests/update_e2e/real_automatic.py').read_bytes())))
            from deploy.update.trust import Verifier
            from deploy.update.settings import load_settings
            verifier = Verifier(load_settings(p.ROOT, environ={}), host_only=True)
            with mock.patch.object(verifier, 'verify', return_value=SimpleNamespace(verified=True)):
                proof, manifest = packager.build(executor.target,
                    json.loads((root/'evidence/policy.json').read_bytes()), root/'evidence', run_id=42, verifier=verifier)
            self.assertTrue(json.loads(manifest)['compatibility']['automatic_apply'])
            self.assertEqual(json.loads(proof)['run_id'], 42)

    def test_protected_main_reaches_product_session_without_legacy_shipping_factory(self):
        executor = SyntheticExecutor()
        def run(name, args):
            args.qualification.prepare_reference(executor)
            all_assertions(args.qualification)
            args.qualification.capture(executor)
            executor.cleanup_verified = True
            return 0
        env = dict(GITHUB_REPOSITORY=p.REPOSITORY, GITHUB_RUN_ID='42',
            GITHUB_REF='refs/tags/'+executor.target['version'], GITHUB_SHA=executor.target['source_sha'],
            GITHUB_EVENT_NAME='workflow_call')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env), \
             mock.patch.object(p, '_validate_context'), \
             mock.patch.object(p, 'Verifier', return_value=SimpleNamespace(require_host=lambda: None)), \
             mock.patch.object(p, 'HttpCache'), mock.patch.object(p, 'ReleaseDiscovery'), \
             mock.patch.object(p, 'select_predecessors', return_value=[executor.row]), \
             mock.patch.object(p, 'milestone_api', return_value=self.milestone(run)), \
             mock.patch.object(p, 'shipping_factory', side_effect=AssertionError('legacy factory not used')):
            root = Path(tmp)
            candidate = root/'candidate.json'
            candidate.write_bytes(canonical_json(executor.target))
            self.assertEqual(p.main(['--target', str(candidate), '--workspace', str(root/'workspace'),
                '--output', str(root/'evidence'), '--run-id', '42', '--purpose', 'product']), 0)
            envelope = json.loads((root/'evidence/evidence.json').read_bytes())
            self.assertEqual(envelope['lane'], 'product')
            self.assertIn('0-qualification_driver.json', envelope['rows'][0]['evidence_inventory'])

    def test_product_matrix_never_publishes_a_partial_or_unclean_row(self):
        for failure in ('second_unrun', 'first_unclean'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(p, '_validate_context'):
                root = Path(tmp)
                first, second = SyntheticExecutor(), SyntheticExecutor()
                executors = iter((first, second))
                def run(name, args):
                    executor = next(executors)
                    if failure == 'second_unrun' and executor is second:
                        return 0
                    args.qualification.prepare_reference(executor)
                    all_assertions(args.qualification)
                    args.qualification.capture(executor)
                    executor.cleanup_verified = True
                    if failure == 'first_unclean' and executor is second:
                        first.cleanup_verified = False
                    return 0
                with mock.patch.object(p, 'milestone_api', return_value=self.milestone(run)):
                    with self.assertRaises(p.ManualRelease):
                        p.produce(first.target, [first.row, second.row], root, root/'evidence', run_id=42)
                self.assertFalse((root/'evidence/evidence.json').exists())
                self.assertFalse((root/'evidence/policy.json').exists())
                self.assertTrue((root/'evidence/failure.json').is_file())

    def test_product_producer_refuses_unrun_plan_or_unconnected_sink(self):
        executor = SyntheticExecutor()
        for matching in (False, True):
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(p, '_validate_context'):
                root = Path(tmp)
                module = self.milestone(lambda name, args: 0, planned=matching)
                with mock.patch.object(p, 'milestone_api', return_value=module):
                    with self.assertRaises(p.ManualRelease):
                        p.produce(executor.target, [executor.row], root, root/'evidence', run_id=42)
                self.assertFalse((root/'evidence/evidence.json').exists())
                self.assertFalse((root/'evidence/policy.json').exists())


class DispatchTests(unittest.TestCase):
    def test_all_cli_choices_are_the_actual_milestone_dispatch_contract(self):
        self.assertTrue(stages.MILESTONES)
        for name in stages.BOUNDED_MILESTONES:
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(stages, 'run_milestone', return_value=0) as run:
                self.assertEqual(p.qualify(['--through', name, '--dry-run', '--out', str(Path(tmp)/'out')]), 0)
                self.assertEqual(run.call_args.args[0], name)
                self.assertFalse(hasattr(run.call_args.args[1], 'qualification'))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            p.qualify(['--through', 'not_a_milestone', '--dry-run', '--out', str(Path(tmp)/'out')])

    def test_actual_all_milestone_dry_runs_never_write_qualification(self):
        for name in stages.BOUNDED_MILESTONES:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)/'out'
                self.assertEqual(p.qualify(['--through', name, '--dry-run', '--out', str(out)]), 0)
                record = json.loads((out/'milestone.json').read_bytes())
                self.assertFalse(record['eligible'])
                self.assertEqual(record['executed_stages'], [])
                plan = json.loads((out/'job-plan.json').read_bytes())
                self.assertEqual(plan['milestone'], name)
                self.assertEqual(set(plan['qualification_suites'].values()), {'unrun'})
                for forbidden in ('proof-row.json', 'evidence.json', 'policy.json'):
                    self.assertFalse((out/forbidden).exists())

    def test_missing_dispatch_implementation_refuses(self):
        with mock.patch.object(stages, 'run_milestone', None):
            with self.assertRaisesRegex(p.ManualRelease, 'shipping_executor_unavailable'):
                p.milestone_api()

    def full(self, root, runner, *, verifier_ok=True):
        """Real dry-run plans through the real run_milestone; only real dispatch is faked."""
        executor = SyntheticExecutor()
        deployment = root/'retained'; deployment.mkdir()
        (deployment/'source-manifest.json').write_bytes(executor.row.release.raw_manifest)
        (deployment/'target-manifest.json').write_bytes(canonical_json(executor.target))
        (deployment/'source-manifest.sigstore.json').write_bytes(b'SYNTHETIC-NOT-A-SIGNATURE')
        old = executor.row.release.manifest['components']['standalone']
        new = executor.target['components']['standalone']
        verified = SimpleNamespace(verified=verifier_ok, method='key', signer='SYNTHETIC', issuer='',
            tool='SYNTHETIC', public_key_sha256='a'*64)
        real_run, dispatched = stages.run_milestone, []
        def dispatch(name, args):
            if args.dry_run:
                return real_run(name, args)
            dispatched.append((name, args))
            return runner(name, args)
        with mock.patch.object(p, '_validate_context'), \
             mock.patch.object(p, '_local_verifier', return_value=SimpleNamespace(verify=lambda _: verified)), \
             mock.patch.object(p, 'select_retained_layout', return_value=(executor.row.execution_deployment, executor.row.dependencies)), \
             mock.patch.object(stages, 'run_milestone', side_effect=dispatch):
            code = p.qualify(['--source-ref', old['image']+'@'+old['digest'], '--target-ref', new['image']+'@'+new['digest'],
                '--deployment-dir', str(deployment), '--platform', 'linux/amd64', '--reference-scale', 'registered-100k',
                '--out', str(root/'out'), '--run-id', '42'])
        return code, dispatched, executor

    @staticmethod
    def refs():
        executor = SyntheticExecutor()
        old = executor.row.release.manifest['components']['standalone']
        new = executor.target['components']['standalone']
        return old['image']+'@'+old['digest'], new['image']+'@'+new['digest']

    def assert_refused_before_provisioning(self, root, dispatched, reason):
        self.assertEqual(dispatched, [])
        row = json.loads((root/'out/proof-row.json').read_bytes())
        self.assertFalse(row['eligible']); self.assertFalse(row['shipping_executor_available'])
        self.assertEqual(row['reason'], reason)
        self.assertEqual(row['phase'], 'plan_milestones')
        self.assertIsNone(row['milestone_dispatched'])
        self.assertEqual(set(row['suites'].values()), {'unrun'})
        self.assertEqual(set(row['milestone_dispatch']), set(stages.MILESTONES))
        for name in stages.MILESTONES:
            self.assertTrue((root/'out/milestone-plans'/name/'job-plan.json').is_file())
        for forbidden in ('rows/evidence.json', 'rows/policy.json', 'workspace', 'milestones'):
            self.assertFalse((root/'out'/forbidden).exists())
        # The CI-uploaded diagnostics carry the same refusal and exact pins.
        uploaded = json.loads((root/'out/diagnostics.json').read_bytes())
        self.assertFalse(uploaded['qualification_evidence'])
        self.assertEqual(uploaded['failure']['reason'], reason)
        self.assertEqual(uploaded['failure']['milestone_dispatch'], row['milestone_dispatch'])
        return row

    def test_bounded_milestones_alone_refuse_before_any_docker_with_exact_pins(self):
        """Removing the full executor must never promote the bounded milestones."""
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(stages, 'MILESTONES', stages.BOUNDED_MILESTONES):
            root = Path(tmp)
            code, dispatched, _ = self.full(root, lambda name, args: self.fail('no real dispatch'))
            self.assertEqual(code, 2)
            row = self.assert_refused_before_provisioning(root, dispatched, 'shipping_executor_unavailable')
            for name, item in row['milestone_dispatch'].items():
                self.assertFalse(item['plans_six_suites'])
                self.assertFalse(item['pins_requested_pair'])
                self.assertEqual(item['source_image'], stages.IMAGE)
            self.assertEqual(row['milestone_dispatch']['target_readiness']['target_image'], stages.TARGET_IMAGE)

    def test_actual_exact_pair_plan_dispatches_without_promoting_an_empty_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, dispatched, _ = self.full(root, lambda name, args: 0)
            self.assertEqual(code, 2)
            self.assertEqual([name for name, _ in dispatched], ['qualification'])
            result = json.loads((root/'out/proof-row.json').read_bytes())
            self.assertEqual(result['reason'], 'suite_unrun')
            self.assertFalse(result['eligible'])
            self.assertEqual(result['milestone_dispatched'], 'qualification')
            self.assertFalse((root/'out/rows/evidence.json').exists())

    def test_six_suite_plan_of_another_pair_is_unrun_for_this_pair(self):
        source, _ = self.refs()
        with tempfile.TemporaryDirectory() as tmp, six_suite_plan('target_readiness', source, stages.TARGET_IMAGE):
            root = Path(tmp)
            code, dispatched, _ = self.full(root, lambda name, args: self.fail('no real dispatch'))
            self.assertEqual(code, 2)
            row = self.assert_refused_before_provisioning(root, dispatched, 'suite_unrun')
            self.assertTrue(row['milestone_dispatch']['target_readiness']['plans_six_suites'])
            self.assertFalse(row['milestone_dispatch']['target_readiness']['pins_requested_pair'])

    def test_matching_six_suite_plan_dispatches_once_but_zero_status_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, six_suite_plan('target_readiness', *self.refs()):
            root = Path(tmp)
            code, dispatched, _ = self.full(root, lambda name, args: 0)
            self.assertEqual(code, 2)
            self.assertEqual(len(dispatched), 1)
            name, args = dispatched[0]
            self.assertEqual((name, args.through, args.dry_run), ('target_readiness', 'target_readiness', False))
            self.assertIsInstance(args.qualification, p.QualificationSession)
            self.assertEqual(args.out, root/'out/milestones/target_readiness')
            row = json.loads((root/'out/proof-row.json').read_bytes())
            self.assertFalse(row['eligible']); self.assertFalse(row['shipping_executor_available'])
            self.assertEqual(row['reason'], 'suite_unrun')
            self.assertEqual(row['milestone_dispatched'], 'target_readiness')
            self.assertEqual(set(row['suites'].values()), {'unrun'})
            self.assertFalse((root/'out/rows/evidence.json').exists())

    def test_nonzero_milestone_exit_is_suite_failed_with_its_bounded_failure(self):
        def run(name, args):
            args.out.mkdir(parents=True)
            (args.out/'milestone.json').write_bytes(canonical_json(dict(status='failed', executed_stages=['a'],
                cleanup_verified=True, failure=dict(stage='prepare_target', reason='suite_failed',
                exception_type='RehearsalError', detail='target loader failed'))))
            return 2
        with tempfile.TemporaryDirectory() as tmp, six_suite_plan('target_readiness', *self.refs()):
            root = Path(tmp)
            code, dispatched, _ = self.full(root, run)
            self.assertEqual((code, len(dispatched)), (2, 1))
            row = json.loads((root/'out/proof-row.json').read_bytes())
            self.assertEqual(row['reason'], 'suite_failed')
            self.assertEqual(row['milestone_outcome']['failure']['stage'], 'prepare_target')
            self.assertFalse(row['eligible'])

    def test_full_source_signature_failure_does_not_plan_or_start_a_milestone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, dispatched, _ = self.full(root, lambda name, args: 0, verifier_ok=False)
            self.assertEqual(code, 2)
            self.assertEqual(dispatched, [])
            self.assertFalse((root/'out/milestone-plans').exists())

    def test_full_sink_produces_one_bound_row_after_cleanup(self):
        def run(name, args):
            executor = SyntheticExecutor()
            args.qualification.prepare_reference(executor)
            all_assertions(args.qualification)
            args.qualification.capture(executor)
            executor.cleanup_verified = True
            return 0
        with tempfile.TemporaryDirectory() as tmp, six_suite_plan('backup_integrity', *self.refs()):
            root = Path(tmp)
            code, dispatched, _ = self.full(root, run)
            self.assertEqual(code, 0)
            self.assertEqual([name for name, _ in dispatched], ['backup_integrity'])
            result = json.loads((root/'out/proof-row.json').read_bytes())
            self.assertTrue(result['eligible']); self.assertTrue(result['shipping_executor_available'])
            self.assertEqual(result['milestone_dispatched'], 'backup_integrity')
            self.assertEqual(set(result['row']['reports']), set(SUITES))
            self.assertEqual(len(json.loads((root/'out/rows/evidence.json').read_bytes())['rows']), 1)

    def test_interrupted_driver_retains_only_executed_counts(self):
        def run(name, args):
            executor = SyntheticExecutor()
            args.qualification.prepare_reference(executor)
            args.qualification.check('source_snapshot', 'source_identity', lambda: None)
            raise Interrupted('SIGTERM')
        with tempfile.TemporaryDirectory() as tmp, six_suite_plan('target_readiness', *self.refs()):
            root = Path(tmp)
            code, _, _ = self.full(root, run)
            self.assertEqual(code, 2)
            result = json.loads((root/'out/proof-row.json').read_bytes())
            self.assertEqual(result['executed_assertions']['source_snapshot'], 1)
            self.assertEqual(sum(result['executed_assertions'].values()), 1)
            self.assertEqual(result['refusal_code'], 'operation_interrupted')
            self.assertFalse(result['eligible']); self.assertFalse(result['shipping_executor_available'])


class TransferAndScopeTests(unittest.TestCase):
    def test_each_real_callable_is_timed_once_and_numerator_is_from_sealed_trees(self):
        executor = SyntheticExecutor()
        stamps = iter((10, 11, 11, 12, 12, 13))
        measurements = p.VolumeRoleTransfers(clock=lambda: next(stamps))
        events = []
        for role in p.VOLUME_ROLES:
            self.assertEqual(measurements.measure(role, lambda r=role: events.append(r) or r), role)
        self.assertEqual(events, list(p.VOLUME_ROLES))
        snapshot, members = executor.corpus['source_snapshot_inventory'], executor.corpus['source_snapshot_members']
        records = measurements.finish(snapshot, members)
        corpus = executor.private_evidence()
        corpus['volume_role_transfers'] = {action: copy.deepcopy(records) for action in ('backup', 'restore')}
        p.validate_volume_role_transfers(executor.fields, corpus)
        for role, entry in records['roles'].items():
            self.assertEqual(entry['logical_bytes'], snapshot['archives'][role]['logical_bytes'])
            self.assertEqual(entry['throughput_bytes_per_second'], entry['logical_bytes'])

    def test_absent_failed_or_duplicate_volume_operation_is_not_a_measurement(self):
        sampler = p.VolumeRoleTransfers()
        with self.assertRaisesRegex(p.RehearsalError, 'unrun'):
            sampler.finish({}, {})
        def failure(): raise OSError('SYNTHETIC transfer failure')
        with self.assertRaises(OSError): sampler.measure('app-data', failure)
        with self.assertRaises(p.RehearsalError): sampler.measure('app-data', lambda: None)
        self.assertTrue(sampler.failed)

    def test_every_role_rejects_missing_bytes_interval_rate_and_generation_gaps(self):
        executor = SyntheticExecutor()
        for action in ('backup', 'restore'):
            for role in p.VOLUME_ROLES:
                for change in ('missing', 'bytes', 'time', 'rate', 'nan', 'generation', 'basis'):
                    corpus = executor.private_evidence()
                    record = corpus['volume_role_transfers'][action]
                    item = record['roles'][role]
                    if change == 'missing': del record['roles'][role]
                    elif change == 'bytes': item['logical_bytes'] += 1
                    elif change == 'time': item['completed_monotonic'] = 14
                    elif change == 'rate': item['throughput_bytes_per_second'] += 1
                    elif change == 'nan': item['started_monotonic'] = float('nan')
                    elif change == 'generation': record['snapshot_generation'] = 'f'*64
                    else: record['measurement_basis'] = 'allocated-blocks'
                    with self.subTest(action=action, role=role, change=change), self.assertRaises(p.RehearsalError):
                        p.validate_volume_role_transfers(executor.fields, corpus)

    def test_table_scope_is_bound_to_program_exact_images_inventory_and_prestart_plan(self):
        executor = SyntheticExecutor()
        p.validate_scope_probe(executor.fields, executor.corpus, executor.target)
        for field in ('program_sha256', 'target_image_digest', 'target_inventory_sha256', 'source_applied_sha256'):
            corpus = executor.private_evidence()
            corpus['pending_table_scope_probe'][field] = 'e'*64
            with self.subTest(field=field), self.assertRaises(p.RehearsalError):
                p.validate_scope_probe(executor.fields, corpus, executor.target)
        source = p.target_table_scope_probe()
        compile(source, '<fixed-real-target-loader-scope>', 'exec')
        # The probe's last line carries the loader plan AND the scope, because
        # decode_product_output reads only the final line of the helper.
        self.assertEqual(source.rstrip().splitlines()[-1].count('print('), 0)
        self.assertEqual(p.PENDING_TABLE_SCOPE.count('print('), 1)
        self.assertIn('pending=[dict(app_label=migration.app_label', p.PENDING_TABLE_SCOPE)
        self.assertIn('MigrationLoader(None', source)
        self.assertIn('loader.project_state', source)
        self.assertIn('migration.operations', source)
        self.assertIn('operation.state_forwards', source)
        self.assertIn('SeparateDatabaseAndState', source)
        self.assertNotIn('subprocess', p.PENDING_TABLE_SCOPE)
        self.assertNotIn('database_forwards', p.PENDING_TABLE_SCOPE)

    def test_scope_record_binds_probe_output_to_exact_pending_plan(self):
        executor = SyntheticExecutor()
        inventory = [dict(app_label='tracer', name='0001_initial', source_sha256='1'*64)]
        digest = p.sha(json.dumps(inventory, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                  allow_nan=False).encode())
        output = dict(planned=[['tracer', '0001_initial']], inventory=inventory, inventory_sha256=digest,
            pending=[], database_schemas=dict(postgres='public', clickhouse='default'), pending_migration_tables=[])
        record = p.pending_table_scope_record(output, target_image_digest='sha256:'+'2'*64,
                                              source_applied_sha256='3'*64)
        self.assertEqual(record['program_sha256'], p.sha(p.target_table_scope_probe().encode()))
        self.assertEqual(record['target_inventory_sha256'], digest)
        for change in ('inventory', 'pending', 'missing'):
            broken = copy.deepcopy(output)
            if change == 'inventory': broken['inventory'][0]['source_sha256'] = '4'*64
            elif change == 'pending': broken['pending'] = [dict(app_label='tracer', name='0002_x')]
            else: del broken['database_schemas']
            with self.subTest(change=change), self.assertRaises(p.RehearsalError):
                p.pending_table_scope_record(broken, target_image_digest='sha256:'+'2'*64,
                                             source_applied_sha256='3'*64)
        corpus = executor.private_evidence()
        corpus['pending_table_scope_probe']['database_schemas']['postgres'] = 'x'*64
        with self.assertRaises(p.RehearsalError):
            p.validate_scope_probe(executor.fields, corpus, executor.target)


class LocalPackageTests(unittest.TestCase):
    def test_ephemeral_key_is_generated_only_after_strict_validation_and_never_keyless(self):
        from scripts import generate_update_manifest as generator
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root/'candidate.json'; candidate.write_bytes(canonical_json(default_v2()))
            evidence = root/'evidence'; evidence.mkdir()
            (evidence/'policy.json').write_bytes(b'{}')
            out = root/'package'
            commands, ordering = [], []
            def build(*args, **kwargs):
                ordering.append('validated')
                return b'SYNTHETIC-PROOF', canonical_json(default_v2())
            def command(argv, **kwargs):
                commands.append([str(a) for a in argv])
                ordering.append(argv[1])
                if argv[1] == 'generate-key-pair':
                    prefix = Path(argv[-1])
                    Path(str(prefix)+'.key').write_bytes(b'-----BEGIN PRIVATE KEY-----\nSYNTHETIC-NOT-A-KEY')
                    Path(str(prefix)+'.pub').write_bytes(b'SYNTHETIC-NOT-A-KEY')
                else:
                    self.assertEqual(argv[1], 'sign-blob')
                    Path(argv[argv.index('--bundle')+1]).write_bytes(b'SYNTHETIC-NOT-A-SIGNATURE')
                return b''
            verifier = SimpleNamespace(verify=lambda _: SimpleNamespace(verified=True))
            with mock.patch.object(packager, 'build', side_effect=build), \
                 mock.patch.object(p, '_local_verifier', return_value=verifier), \
                 mock.patch.object(p, 'command', side_effect=command):
                self.assertEqual(p.package(['--evidence', str(evidence), '--target', str(candidate),
                    '--ephemeral-key', '--out', str(out), '--run-id', '42']), 0)
            self.assertEqual(ordering, ['validated', 'generate-key-pair', 'sign-blob'])
            self.assertEqual((out/'local-test-key.key').stat().st_mode & 0o777, 0o600)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            sign = commands[1]
            self.assertIn('--key', sign)
            self.assertIn('--tlog-upload=false', sign)
            self.assertIn('--use-signing-config=false', sign)
            metadata = json.loads((out/'local-test.json').read_bytes())
            self.assertTrue(metadata['ephemeral_key'])
            self.assertEqual(metadata['proof_sha256'], hashlib.sha256(b'SYNTHETIC-PROOF').hexdigest())
            self.assertIn('LOCAL TEST ONLY', metadata['purpose'])

    def test_invalid_or_failed_evidence_never_generates_a_key_or_output(self):
        for failure_marker in (None, 'failure.json', 'manual.json'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                candidate = root/'candidate.json'; candidate.write_bytes(canonical_json(default_v2()))
                evidence = root/'evidence'; evidence.mkdir(); (evidence/'policy.json').write_bytes(b'{}')
                if failure_marker: (evidence/failure_marker).write_bytes(b'{}')
                with mock.patch.object(packager, 'build', side_effect=CompatibilityError()), \
                     mock.patch.object(p, '_local_verifier'), mock.patch.object(p, 'command') as command:
                    self.assertEqual(p.package(['--evidence', str(evidence), '--target', str(candidate),
                        '--ephemeral-key', '--out', str(root/'out'), '--run-id', '42']), 2)
                command.assert_not_called()
                self.assertFalse((root/'out').exists())

    def test_protected_release_workflows_never_generate_sign_or_upload_local_test_keys(self):
        path = p.ROOT/'.github/workflows/update-compatibility-proof.yml'
        workflow, text = load(path), path.read_text()
        self.assertEqual(set(workflow['on']['workflow_call']['inputs']), {'produce'})
        proof = workflow['jobs']['proof']
        self.assertEqual(proof['permissions'], {'contents': 'read'})
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", proof['if'])
        one_step(proof, runs_containing('scripts/rehearse_update_compatibility.py'))
        for forbidden in ('--ephemeral-key', 'generate-key-pair', 'local-test', 'rehearse_update_compatibility.py package'):
            self.assertNotIn(forbidden, text)
        release = load(p.ROOT/'.github/workflows/release-update-manifest.yml')
        self.assertEqual(release['jobs']['rehearse']['with'], {'produce': 'true'})
        signing = one_step(release['jobs']['publish'], runs_containing('cosign sign-blob'))['run']
        self.assertNotIn('--key', signing)
        self.assertNotIn('--ephemeral-key', signing)


def real_cosign():
    """The pinned host verifier CI exports; absent is a hard failure, never a skip."""
    found = os.environ.get('FUTURE_AGI_UPDATE_TEST_COSIGN') or shutil.which('cosign')
    if not found:
        raise RuntimeError('real local-key package test requires FUTURE_AGI_UPDATE_TEST_COSIGN or cosign')
    return Path(found).resolve()


class RealCosignPackageTests(unittest.TestCase):
    """Real cosign key generation, signing and host verification.

    The evidence CONTENT is the labeled SYNTHETIC session fixture above; this
    proves the package/signing boundary only, never product qualification.
    """
    def test_ephemeral_local_test_package_signs_and_verifies_with_real_cosign(self):
        cosign = real_cosign()
        path = str(cosign.parent) + os.pathsep + os.environ.get('PATH', '')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, PATH=path):
            root = Path(tmp)
            env = dict(os.environ, COSIGN_PASSWORD='')
            run = lambda *argv: subprocess.run([str(cosign), *map(str, argv)], env=env, check=True,
                                              capture_output=True, timeout=120)
            run('generate-key-pair', '--output-key-prefix', root/'predecessor')
            executor = SyntheticExecutor()
            raw = root/'predecessor-manifest.json'
            raw.write_bytes(executor.row.release.raw_manifest)
            run('sign-blob', '--yes', '--key', root/'predecessor.key', '--tlog-upload=false',
                '--use-signing-config=false', '--bundle', root/'predecessor.sigstore.json', raw)
            executor.row.release.raw_bundle = (root/'predecessor.sigstore.json').read_bytes()
            session = p.QualificationSession(executor.row, executor.target, root/'rows', run_id=42,
                deadline=time.monotonic()+120, registry=p.load_high_volume_registry())
            session.prepare_reference(executor)
            all_assertions(session)
            session.capture(executor)
            executor.cleanup_verified = True
            session.finish()
            candidate = root/'candidate.json'
            candidate.write_bytes(canonical_json(executor.target))
            out = root/'package'
            self.assertEqual(p.package(['--evidence', str(root/'rows'), '--target', str(candidate),
                '--ephemeral-key', '--public-key', str(root/'predecessor.pub'), '--out', str(out),
                '--run-id', '42']), 0)
            self.assertEqual((out/'local-test-key.key').stat().st_mode & 0o777, 0o600)
            manifest_raw = (out/p.MANIFEST_ASSET).read_bytes()
            manifest = validate_manifest(json.loads(manifest_raw))
            self.assertTrue(manifest['compatibility']['automatic_apply'])
            from deploy.update.compatibility import PROOF_ASSET
            validate_release_proof((out/PROOF_ASSET).read_bytes(), manifest, repository=p.REPOSITORY)
            metadata = json.loads((out/'local-test.json').read_bytes())
            self.assertTrue(metadata['ephemeral_key'])
            self.assertEqual(metadata['manifest_sha256'], p.sha(manifest_raw))
            self.assertEqual(metadata['public_key_sha256'], p.sha((out/'local-test-key.pub').read_bytes()))
            # Independent real verification: the fresh key verifies; the
            # predecessor's key does not, so no existing key was reused.
            verify = ['verify-blob', '--bundle', out/p.BUNDLE_ASSET, '--insecure-ignore-tlog=true',
                      '--private-infrastructure=true']
            run(*verify[:1], '--key', out/'local-test-key.pub', *verify[1:], out/p.MANIFEST_ASSET)
            with self.assertRaises(subprocess.CalledProcessError):
                run(*verify[:1], '--key', root/'predecessor.pub', *verify[1:], out/p.MANIFEST_ASSET)
            self.assertNotIn(b'PRIVATE KEY', (out/'local-test-key.pub').read_bytes())


if __name__ == '__main__':
    unittest.main()
