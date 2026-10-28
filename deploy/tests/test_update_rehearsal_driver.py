"""Daemon-free driver safety, explicitly NOT product-image qualification."""
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock

from scripts import rehearse_update_compatibility as rehearsal
from scripts import update_rehearsal_shipping as shipping
from deploy.update.manifest import canonical_json, hash_path
from deploy.tests.test_update_compatibility import default_v2, restore_release


class RehearsalDriverTests(unittest.TestCase):
    def test_ci_interface_help_is_available_without_a_daemon(self):
        for subcommand, options in (
            ('qualify', ('--source-ref','--target-ref','--platform','--deployment-dir',
                         '--reference-scale','--boot-timeout','--out')),
            ('package', ('--rows','--key','--target','--public-key','--out'))):
            command=[sys.executable,'-I',str(rehearsal.ROOT/'scripts/rehearse_update_compatibility.py'),subcommand,'--help']
            result=subprocess.run(command,capture_output=True,text=True,timeout=30,cwd=str(rehearsal.ROOT))
            with self.subTest(command=subcommand):
                self.assertEqual(result.returncode,0,result.stderr)
                for option in options: self.assertIn(option,result.stdout)

    def test_incomplete_factory_cannot_provision_or_attest_to_an_updater_transaction(self):
        self.assertTrue(callable(shipping.create_disposable_rehearsal))
        self.assertEqual(shipping.REHEARSAL_CONTRACT,rehearsal.SHIPPING_REHEARSAL_CONTRACT)
        with mock.patch.object(shipping,'SHIPPING_TRANSACTION_INTEGRATED',False), \
                mock.patch.object(shipping.ShippingRehearsal,'__init__') as constructor:
            with self.assertRaisesRegex(rehearsal.ManualRelease,'shipping_executor_unavailable'):
                rehearsal.shipping_factory()
            with self.assertRaisesRegex(rehearsal.ManualRelease,'shipping_executor_unavailable'):
                shipping.create_disposable_rehearsal()
        constructor.assert_not_called()

    def test_missing_product_inputs_exit_nonzero_and_write_no_affirmative_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            out=root/'out'
            with mock.patch.object(rehearsal,'_validate_context'), mock.patch.object(rehearsal,'_local_verifier') as verifier, \
                    mock.patch.object(shipping,'create_disposable_rehearsal') as factory:
                code=rehearsal.main(['qualify','--source-ref','example/source@sha256:'+'a'*64,
                    '--target-ref','example/target@sha256:'+'b'*64,'--platform','linux/amd64',
                    '--deployment-dir',str(root/'absent'),'--reference-scale','registered-100k','--boot-timeout','4200','--out',str(out)])
            self.assertEqual(code,2)
            row=json.loads((out/'proof-row.json').read_bytes())
            self.assertFalse(row['eligible'])
            self.assertEqual(row['completed_rows'],0)
            self.assertEqual(set(row['suites'].values()),{'unrun'})
            self.assertEqual(sum(row['executed_assertions'].values()),0)
            self.assertFalse((out/'rows/evidence.json').exists())
            verifier.assert_not_called(); factory.assert_not_called()

    def test_explicit_digest_mismatch_refuses_before_source_verification_or_docker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            _,old,_,_=restore_release(0)
            (root/'source-manifest.json').write_bytes(old)
            (root/'target-manifest.json').write_bytes(canonical_json(default_v2()))
            with mock.patch.object(rehearsal,'_validate_context'), mock.patch.object(rehearsal,'_local_verifier') as verifier:
                code=rehearsal.qualify(['--source-ref','futureagi/standalone@sha256:'+'f'*64,
                    '--target-ref','futureagi/standalone@sha256:'+'e'*64,'--platform','linux/amd64',
                    '--deployment-dir',str(root),'--reference-scale','registered-100k','--out',str(root/'out')])
            self.assertEqual(code,2)
            self.assertFalse(json.loads((root/'out/proof-row.json').read_bytes())['eligible'])
            verifier.assert_not_called()

    def test_certified_source_selects_authenticated_A_layout_not_published_B_layout(self):
        from deploy.tests.test_update_compatibility import bind_proof
        from deploy.update.manifest import manifest_hash
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            (root/'docker-compose.yml').write_bytes(b'SYNTHETIC-RETAINED-A-DATA')
            source,_,proof,_=restore_release(0)
            execution={'files':{'docker-compose.yml':hash_path(root,'docker-compose.yml')}}
            proof['predecessors'][0]['execution_deployment']=copy.deepcopy(execution)
            proof['predecessors'][0]['predecessor']['execution_deployment_hash']=manifest_hash(execution)
            source['compatibility']['predecessors'][0]=copy.deepcopy(proof['predecessors'][0]['predecessor'])
            proof_raw=bind_proof(source,proof)
            selected,dependencies=rehearsal.select_retained_layout(source,proof_raw,root,'linux/amd64')
            self.assertEqual(selected,execution)
            self.assertNotEqual(selected,source['deployment'])
            self.assertEqual(dependencies,tuple(proof['predecessors'][0]['dependencies']))
            (root/'docker-compose.yml').write_bytes(b'SYNTHETIC-OPERATOR-REPLACEMENT')
            with self.assertRaises(rehearsal.RehearsalError):
                rehearsal.select_retained_layout(source,proof_raw,root,'linux/amd64')

    def test_source_manifest_bound_proof_is_required_for_retained_layout_selection(self):
        source,_,_,raw=restore_release(0)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            for altered in (None,b'{}',raw+b' '):
                with self.subTest(proof=altered is None),self.assertRaises((ValueError,rehearsal.RehearsalError)):
                    rehearsal.select_retained_layout(source,altered,root,'linux/amd64')

    def test_unknown_scale_never_downsizes_registered_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            with self.assertRaises(SystemExit) as stopped:
                rehearsal.qualify(['--source-ref','x','--target-ref','y','--platform','linux/amd64',
                    '--deployment-dir',str(root),'--reference-scale','tiny','--out',str(root/'out')])
            self.assertEqual(stopped.exception.code,2)
            self.assertFalse((root/'out').exists())

    def test_failed_or_foreign_rows_cannot_reach_test_key_signing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            (root/'proof-row.json').write_bytes(canonical_json(dict(eligible=False,run_id=1)))
            with mock.patch.object(rehearsal,'command') as command, mock.patch.object(rehearsal,'_local_verifier') as verifier:
                code=rehearsal.package(['--rows',str(root),'--target',str(root/'absent'),
                    '--key',str(root/'test-key.pem'),'--public-key',str(root/'test-public.pem'),
                    '--out',str(root/'package'),'--run-id','1'])
            self.assertEqual(code,2)
            self.assertFalse((root/'package').exists())
            command.assert_not_called(); verifier.assert_not_called()

    def test_deployment_copy_checks_exact_hashes_and_never_copies_private_env(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve(); source=root/'source'; source.mkdir(); target=root/'target'; target.mkdir()
            (source/'docker-compose.yml').write_bytes(b'SYNTHETIC-DEPLOYMENT-DATA')
            (source/'.env').write_bytes(b'PRIVATE=not-a-deployment-file\n')
            expected={'files':{'docker-compose.yml':hash_path(source,'docker-compose.yml')}}
            shipping.copy_retained_files(source,target,expected)
            self.assertEqual((target/'docker-compose.yml').read_bytes(),(source/'docker-compose.yml').read_bytes())
            self.assertFalse((target/'.env').exists())
            for name in ('.env','../escaped','/absolute'):
                with self.subTest(name=name),self.assertRaises(rehearsal.RehearsalError):
                    shipping.copy_retained_files(source,target,{'files':{name:'a'*64}})
            with self.assertRaises(rehearsal.RehearsalError):
                shipping.copy_retained_files(source,target,{'files':{'docker-compose.yml':'a'*64}})

    def test_deployment_copy_refuses_links_inside_a_hashed_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve(); source=root/'source'; source.mkdir(); target=root/'target'; target.mkdir()
            directory=source/'deploy'; directory.mkdir(); (directory/'config').symlink_to('/etc/passwd')
            with self.assertRaises((rehearsal.RehearsalError,ValueError)):
                shipping.copy_retained_files(source,target,{'files':{'deploy':hash_path(source,'deploy')}})
            self.assertFalse((target/'deploy/config').exists())

    def test_wire_retains_immutable_observation_values_without_pickle_or_paths_as_code(self):
        @dataclass(frozen=True)
        class Observation:
            values: object
            at: datetime
            path: Path
        value=Observation(MappingProxyType({'rows':(1,2)}),datetime(2026,10,28,tzinfo=timezone.utc),Path('/not/executed.py'))
        self.assertEqual(shipping.wire(value),{'values':{'rows':[1,2]},'at':'2026-10-28T00:00:00+00:00','path':'/not/executed.py'})

    def test_every_container_operation_payload_compiles_without_executing_on_host(self):
        for name in ('S3_OPERATION','TEMPORAL_OPERATION','SPOOL_OPERATION','REFERENCE_SEED'):
            with self.subTest(name=name): compile(getattr(shipping,name),'<fixed-product-operation>','exec')

    def test_missing_reference_workload_cannot_seed_arbitrary_defaults(self):
        driver=object.__new__(shipping.ShippingRehearsal)
        driver.app_python=mock.Mock()
        registry=rehearsal.load_high_volume_registry()
        unavailable=[r for r in registry if r['database']=='clickhouse']
        self.assertTrue(unavailable)
        for entry in unavailable:
            with self.subTest(table=entry['table']),self.assertRaisesRegex(rehearsal.ManualRelease,'high_volume_reference_unavailable'):
                driver.seed_reference_table(entry,100000)
        driver.app_python.assert_not_called()

    def test_factory_never_claims_unrun_suite_callbacks(self):
        driver=object.__new__(shipping.ShippingRehearsal)
        for suite in rehearsal.SUITES:
            if suite=='source_snapshot':
                callbacks=driver.suite_assertions(suite)
                self.assertEqual(set(callbacks),rehearsal.required_cases(suite,rehearsal.topology_profile('standalone',())))
                self.assertTrue(all(callable(f) for f in callbacks.values()))
            else:
                with self.subTest(suite=suite),self.assertRaises(rehearsal.ManualRelease):
                    driver.suite_assertions(suite)

    def test_new_driver_is_in_complete_executor_inventory(self):
        from scripts.build_update_compatibility_proof import implementation_inventory
        inventory=implementation_inventory()
        self.assertEqual(inventory['scripts/update_rehearsal_shipping.py'],hashlib.sha256(Path(shipping.__file__).read_bytes()).hexdigest())

    def test_failure_counts_are_only_completed_assertions_not_an_adapter_verdict(self):
        class IncompleteExecutor:
            def suite_assertions(self,suite):
                if suite!='source_snapshot': raise rehearsal.ManualRelease('suite_unrun')
                result={}
                for name in sorted(rehearsal.required_cases(suite,rehearsal.topology_profile('standalone',()))):
                    def assertion(n=name):
                        if n=='source_identity': raise RuntimeError('synthetic executed assertion failure')
                        rehearsal.equal(n,n,'executed equality failed')
                    result[name]=assertion
                return result
        executor=IncompleteExecutor()
        with self.assertRaises(RuntimeError): rehearsal.exercise(executor,deadline=time_limit())
        executed=executor.executed_cases
        self.assertEqual(executed.failed,('source_snapshot','source_identity'))
        self.assertNotIn('source_identity',executed.cases['source_snapshot'])
        self.assertEqual(sum(len(executed.cases[s]) for s in rehearsal.SUITES),
            len([n for n in rehearsal.required_cases('source_snapshot',rehearsal.topology_profile('standalone',())) if n<'source_identity']))


def time_limit():
    import time
    return time.monotonic()+30
