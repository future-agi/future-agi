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
        for name in ('S3_OPERATION','TEMPORAL_OPERATION','SPOOL_OPERATION','REFERENCE_SEED','REDIS_OPERATION'):
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
            if suite in ('source_snapshot','target_readiness'):
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


class Wave4bDriverRegressionTests(unittest.TestCase):
    """Daemon-free refusals and diagnostic plumbing, NOT product qualification."""
    def test_A9_selects_only_touched_registered_tables_per_engine(self):
        registry = ({'database':'postgres','table':'large'}, {'database':'clickhouse','table':'large'},
                    {'database':'postgres','table':'untouched'})
        plan = [dict(app_label='a',name='0001_change'), dict(app_label='a',name='0002_other')]
        scope = [dict(plan[0],tables=[dict(engine='postgres',table='public.large')]),
                 dict(plan[1],tables=[dict(engine='clickhouse',table='default.small')])]
        selected = rehearsal.select_reference_registry(registry,scope,plan=plan)
        self.assertEqual(selected,(registry[0],))
        self.assertEqual(rehearsal.select_reference_registry(registry,[],plan=[]),())
        calls=[]
        self.assertEqual(rehearsal.seed_reference_tables((),lambda *a:calls.append(a),lambda *a:calls.append(a)),[])
        self.assertEqual(calls,[])
        for invalid in (None,scope[::-1],scope+[scope[0]],
                        [dict(scope[0],tables=[dict(engine='postgres',table='public.large;DROP')]),scope[1]]):
            with self.subTest(scope=invalid),self.assertRaises((rehearsal.RehearsalError,ValueError)):
                rehearsal.select_reference_registry(registry,invalid,plan=plan)

    def test_A9_reference_requires_exact_selected_scope_including_positive_zero(self):
        registry = rehearsal.load_high_volume_registry()
        entry = next(r for r in registry if r['database']=='postgres')
        identity=dict(app_label='a',name='0001')
        scope=[dict(identity,tables=[dict(engine='postgres',table='public.'+entry['table'])])]
        reference=dict(snapshot_generation='a'*64,
            registry_sha256=hashlib.sha256((rehearsal.ROOT/'deploy/update/high-volume-tables.json').read_bytes()).hexdigest(),
            reference_rows=100000,tables=[dict(engine='postgres',table='public.'+entry['table'],rows=100000,bytes=1000000)])
        self.assertEqual(len(rehearsal.validate_reference_inventory(reference,pending_migration_tables=scope,plan=[identity])),1)
        for tables in ([],reference['tables']*2,[dict(reference['tables'][0],rows=99999)],
                       [dict(reference['tables'][0],table='wrong.'+entry['table'])]):
            with self.subTest(tables=tables),self.assertRaises(rehearsal.RehearsalError):
                rehearsal.validate_reference_inventory(dict(reference,tables=tables),pending_migration_tables=scope,plan=[identity])
        empty=dict(reference,tables=[])
        self.assertEqual(rehearsal.validate_reference_inventory(empty,pending_migration_tables=[],plan=[]),{})
        with self.assertRaises(rehearsal.RehearsalError):
            rehearsal.validate_reference_inventory(empty)
        with self.assertRaises(rehearsal.RehearsalError):
            rehearsal.validate_reference_inventory(reference,pending_migration_tables=[],plan=[])

    def test_protected_product_entrypoint_reports_missing_executor_after_discovery(self):
        with tempfile.TemporaryDirectory(prefix='fagi-release-refusal-',dir=str(Path.home())) as temporary:
            root=Path(temporary).resolve()
            candidate=default_v2(); path=root/'candidate.json'; path.write_bytes(canonical_json(candidate))
            env=dict(GITHUB_REPOSITORY=rehearsal.REPOSITORY,GITHUB_RUN_ID='42',
                     GITHUB_REF='refs/tags/'+candidate['version'],GITHUB_SHA=candidate['source_sha'],
                     GITHUB_EVENT_NAME='workflow_call')
            # Product dispatch selects an exact authenticated pair before it can
            # determine whether a six-suite plan exists. These mocked trust and
            # discovery boundaries are a CLI unit test, never qualification.
            with mock.patch.dict(os.environ,env),mock.patch.object(rehearsal,'_validate_context'), \
                 mock.patch.object(rehearsal,'Verifier') as verifier, \
                 mock.patch.object(rehearsal,'ReleaseDiscovery') as discovery, \
                 mock.patch.object(rehearsal,'select_predecessors',return_value=[]) as select, \
                 mock.patch.object(rehearsal,'produce',side_effect=rehearsal.ManualRelease('shipping_executor_unavailable')) as produce, \
                 mock.patch.object(rehearsal,'shipping_factory') as legacy, \
                 mock.patch('sys.stderr') as stderr:
                code=rehearsal.main(['--target',str(path),'--workspace',str(root/'work'),
                    '--output',str(root/'out'),'--run-id','42','--docker-context','colima-fagi-update'])
            self.assertEqual(code,2)
            verifier.return_value.require_host.assert_called_once_with()
            discovery.assert_called_once()
            select.assert_called_once_with(candidate,discovery.return_value,platform='linux/amd64')
            produce.assert_called_once(); legacy.assert_not_called()
            self.assertEqual(produce.call_args.kwargs['purpose'],'product')
            self.assertIn('release rehearsal refused: shipping_executor_unavailable',
                          ''.join(str(call.args[0]) for call in stderr.write.call_args_list))
            data=json.loads((root/'out/failure.json').read_bytes())
            self.assertFalse(data['eligible'])
            self.assertEqual(data['reason'],'shipping_executor_unavailable')
            self.assertEqual(set(data['suites'].values()),{'unrun'})
            self.assertEqual(sum(data['executed_assertions'].values()),0)
            self.assertTrue((root/'out/diagnostics.json').is_file())
            self.assertFalse((root/'out/evidence.json').exists())

    def test_protected_workflow_rejects_a_false_success_with_manual_output(self):
        from deploy.tests.test_update_release_static import load
        workflow=load(rehearsal.ROOT/'.github/workflows/update-compatibility-proof.yml')
        step=next(s for s in workflow['jobs']['proof']['steps'] if s.get('id')=='package')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); tools=root/'tools'; tools.mkdir()
            fake=tools/'python3'
            fake.write_text('#!'+sys.executable+'\nimport os,sys,json\nfrom pathlib import Path\n'
                'p=Path(os.environ["RUNNER_TEMP"])\n'
                'if "rehearse_update_compatibility.py" in sys.argv[2]:\n'
                ' d=p/"evidence"; d.mkdir(); (d/"manual.json").write_text(json.dumps({"reason":"suite_unrun"}))\n'
                'else:\n (p/"packager-was-called").touch()\n')
            fake.chmod(0o700)
            env=dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],RUNNER_TEMP=str(root),RUNNER_ARCH='X64',
                     GITHUB_RUN_ID='42',GITHUB_REPOSITORY=rehearsal.REPOSITORY,GITHUB_OUTPUT=str(root/'github-output'))
            result=subprocess.run(['bash','-c',step['run']],env=env,cwd=str(rehearsal.ROOT),capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,2,result.stdout+result.stderr)
            self.assertIn('Product qualification refused',result.stderr)
            self.assertFalse((root/'packager-was-called').exists())

    def test_diagnostics_keep_typed_refusal_and_real_failed_timing_not_secret_errors(self):
        from deploy.update.automatic_runtime import RuntimeRefused
        from deploy.update.contracts import ReasonCode
        executor=SimpleNamespace(executed_cases=rehearsal.ExecutedCases())
        executor.executed_cases.active_suite='target_readiness'
        with self.assertRaises(RuntimeRefused):
            executor.executed_cases.check('target_readiness','health',
                lambda:(_ for _ in ()).throw(RuntimeRefused(ReasonCode.DISK_WATERMARK_ABORT)))
        data=rehearsal.failure_diagnostics(RuntimeRefused(ReasonCode.DISK_WATERMARK_ABORT),executor)
        self.assertEqual(data['refusal_code'],'disk_watermark_abort')
        self.assertEqual(data['suites']['target_readiness'],'failed')
        self.assertEqual(data['assertion_timings'][0]['outcome'],'failed')
        self.assertGreaterEqual(data['assertion_timings'][0]['duration_seconds'],0)
        self.assertEqual(sum(data['executed_assertions'].values()),0)
        self.assertNotIn('PRIVATE',json.dumps(rehearsal.failure_diagnostics(RuntimeError('PRIVATE PASSWORD'))))

    def test_diagnostics_preserve_only_bounded_typed_backup_and_recovery_codes(self):
        from deploy.update.recovery import RecoveryError
        from deploy.update.standalone_backup import BackupError

        for error in (BackupError('backup_clock_skew'), RecoveryError('recovery_required')):
            with self.subTest(error=type(error).__name__):
                data = rehearsal.failure_diagnostics(error)
                self.assertEqual(data['refusal_code'], error.code)
                self.assertFalse(data['eligible'])
        for error in (BackupError('PRIVATE PASSWORD'), RecoveryError('a' * 81)):
            with self.subTest(error=type(error).__name__):
                data = rehearsal.failure_diagnostics(error)
                self.assertEqual(data['refusal_code'], 'suite_failed')
                self.assertNotIn('PRIVATE', json.dumps(data))
        arbitrary = RuntimeError('PRIVATE PASSWORD')
        arbitrary.code = 'looks_safe'
        self.assertEqual(rehearsal.failure_diagnostics(arbitrary)['refusal_code'], 'suite_failed')

    def test_failed_or_unrun_suites_always_exit_nonzero_with_an_explicit_reason(self):
        candidate=default_v2()
        for reason in ('suite_failed','suite_unrun','suite_timed_out','migration_measurement_unavailable','witness_operation_unavailable'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                root=Path(temporary).resolve(); path=root/'candidate.json'; path.write_bytes(canonical_json(candidate))
                env=dict(GITHUB_REPOSITORY=rehearsal.REPOSITORY,GITHUB_RUN_ID='42',
                         GITHUB_REF='refs/tags/'+candidate['version'],GITHUB_SHA=candidate['source_sha'],
                         GITHUB_EVENT_NAME='workflow_call')
                with mock.patch.dict(os.environ,env),mock.patch.object(rehearsal,'_validate_context'), \
                     mock.patch.object(rehearsal,'Verifier') as verifier, \
                     mock.patch.object(rehearsal,'ReleaseDiscovery') as discovery, \
                     mock.patch.object(rehearsal,'select_predecessors',return_value=[]), \
                     mock.patch.object(rehearsal,'produce',side_effect=rehearsal.ManualRelease(reason)), \
                     mock.patch('sys.stderr') as stderr:
                    code=rehearsal.main(['--target',str(path),'--workspace',str(root/'work'),
                                         '--output',str(root/'out'),'--run-id','42'])
                self.assertEqual(code,2)
                self.assertIn(reason,''.join(str(call.args[0]) for call in stderr.write.call_args_list))
                verifier.return_value.require_host.assert_called_once_with()
                discovery.assert_called_once()
                failure=json.loads((root/'out/failure.json').read_bytes())
                self.assertEqual(failure['reason'],reason)
                self.assertFalse((root/'out/evidence.json').exists())

    def test_first_boot_enter_failure_retains_diagnostics_after_cleanup(self):
        candidate=default_v2(); old,raw,_,_=restore_release(0)
        row=rehearsal.Row(SimpleNamespace(manifest=old,raw_manifest=raw,raw_bundle=b'SYNTHETIC'),old['deployment'],old['source_sha'],())
        class FailedBoot:
            def __enter__(self): raise rehearsal.ManualRelease('suite_timed_out')
            def __exit__(self,*args): return None
            def diagnostics(self):
                return dict(active_phase='source_first_boot',first_boot_budget_seconds=4200,
                            resources=[{'bootstrap':{'phase':'postgres_schema_bootstrap'}}],
                            product_logs={'app':{'stdout':'Applying synthetic.0001'}},cleanup_verified=True)
        executor=FailedBoot()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with mock.patch.object(rehearsal,'_validate_context'), \
                 mock.patch.object(rehearsal,'shipping_factory',return_value=lambda *a,**k:executor):
                with self.assertRaisesRegex(rehearsal.ManualRelease,'suite_timed_out'):
                    rehearsal.produce(candidate,[row],root,root/'out',run_id=42,purpose='pr-validation')
            diagnostics=json.loads((root/'out/diagnostics.json').read_bytes())
            self.assertEqual(diagnostics['product']['active_phase'],'source_first_boot')
            self.assertEqual(diagnostics['product']['first_boot_budget_seconds'],4200)
            self.assertEqual(diagnostics['product']['product_logs']['app']['stdout'],'Applying synthetic.0001')
            self.assertEqual(diagnostics['failure']['reason'],'suite_timed_out')
            self.assertFalse((root/'out/evidence.json').exists())

    def test_log_tails_redact_generated_secrets_tokens_and_url_credentials(self):
        driver=object.__new__(shipping.ShippingRehearsal)
        driver._secret_values={'generated-secret-value'}
        raw=b'Applying app.0001 OK\npassword=hidden bearer opaque.token/value https://user:credential@service/path generated-secret-value'
        tail=driver._redact(raw)
        self.assertIn('Applying app.0001 OK',tail)
        for secret in ('hidden','opaque.token/value','credential','generated-secret-value'):
            self.assertNotIn(secret,tail)
        self.assertLessEqual(len(driver._redact('x'*100000)),8192)
        # Redact BEFORE taking the bounded tail, so a cut cannot leak a suffix.
        self.assertNotIn('secret-value',driver._redact('generated-secret-value'+'x'*8180))

    def test_driver_diagnostics_record_failed_phase_without_claiming_cleanup_or_proof(self):
        driver=object.__new__(shipping.ShippingRehearsal)
        driver._diagnostic_data={'phase_timings':[]}; driver.cleanup_verified=False
        with self.assertRaises(rehearsal.ManualRelease):
            driver._phase('source_first_boot',lambda:(_ for _ in ()).throw(rehearsal.ManualRelease('suite_timed_out')))
        result=driver.diagnostics()
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(result['phase_timings'][0]['phase'],'source_first_boot')
        self.assertEqual(result['phase_timings'][0]['refusal'],'suite_timed_out')
        self.assertGreaterEqual(result['phase_timings'][0]['duration_seconds'],0)


class Wave4cDriverRegressionTests(unittest.TestCase):
    """Executed stdlib regressions, never proof that a product release qualified."""
    def test_subprocess_refusal_reaches_ci_without_raw_stderr_or_secret_argv(self):
        stderr = b'PRIVATE credentials\nqualification refused: shipping_executor_unavailable\n'
        completed = subprocess.CompletedProcess([], 2, stdout=b'', stderr=stderr)
        with mock.patch.object(rehearsal.subprocess, 'run', return_value=completed):
            with self.assertRaises(rehearsal.CommandError) as raised:
                rehearsal.command(['/private/python3', '--password', 'PRIVATE'])
        self.assertIn('shipping_executor_unavailable', str(raised.exception))
        self.assertIn('exit 2', str(raised.exception))
        self.assertNotIn('PRIVATE', str(raised.exception))
        data = rehearsal.failure_diagnostics(raised.exception)
        self.assertEqual(data['refusal_code'], 'shipping_executor_unavailable')
        self.assertEqual(data['command'], dict(executable='python3', exit_code=2))
        self.assertNotIn('credentials', json.dumps(data))
        self.assertEqual(sum(data['executed_assertions'].values()), 0)

    def test_subprocess_only_accepts_closed_refusal_lines_and_keeps_failure_nonzero(self):
        for stderr in (b'qualification refused: arbitrary_private_reason\n',
                       b'qualification refused: suite_unrun PRIVATE\n',
                       b'raw password=PRIVATE\n', b''):
            with self.subTest(stderr=stderr):
                error = rehearsal.CommandError('/private/python3', -9, stderr)
                self.assertEqual(error.refusal_code, 'suite_failed')
                self.assertNotIn('PRIVATE', str(error))
                self.assertFalse(rehearsal.failure_diagnostics(error)['eligible'])
        with mock.patch.object(rehearsal.subprocess, 'run', side_effect=subprocess.TimeoutExpired([], 1)):
            with self.assertRaisesRegex(rehearsal.ManualRelease, 'suite_timed_out'):
                rehearsal.command(['python3'])

    def test_qualify_missing_inputs_retains_exact_phase_and_exception_without_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with mock.patch.object(rehearsal, '_validate_context'):
                code = rehearsal.qualify(['--source-ref', 'absent', '--target-ref', 'absent',
                    '--platform', 'linux/amd64', '--deployment-dir', str(root/'private-absent'),
                    '--reference-scale', 'registered-100k', '--out', str(root/'out')])
            self.assertEqual(code, 2)
            failure = json.loads((root/'out/failure.json').read_bytes())
            self.assertEqual(failure['phase'], 'read_manifest_inputs')
            self.assertEqual(failure['exception_type'], 'FileNotFoundError')
            self.assertNotIn(str(root), json.dumps(failure))
            self.assertEqual(set(failure['suites'].values()), {'unrun'})

    def test_qualify_reaches_explicit_executor_refusal_after_authenticated_inputs(self):
        # Synthetic trust stub tests entrypoint sequencing only; never product
        # evidence or permission to package this row.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, old_raw, _, _ = restore_release(0)
            old = json.loads(old_raw)
            target = default_v2()
            (root/'source-manifest.json').write_bytes(old_raw)
            (root/'target-manifest.json').write_bytes(canonical_json(target))
            (root/'source-manifest.sigstore.json').write_bytes(b'SYNTHETIC-TRUST-STUB')
            verified = SimpleNamespace(verified=True, method='cosign-key', signer='', issuer='',
                                      tool='cosign', public_key_sha256='a'*64)
            verifier = SimpleNamespace(verify=mock.Mock(return_value=verified))
            from scripts.update_rehearsal_milestone import MILESTONES, plan as milestone_plan
            def dispatch(name, args):
                if args.dry_run:
                    args.out.mkdir(mode=0o700)
                    planned = milestone_plan(args.boot_timeout, name if name != 'qualification' else 'source_snapshot')
                    planned.update(milestone=name,source_image=args.source_ref,target_image=args.target_ref,
                                   platform=args.platform)
                    planned['qualification_suites'] = {suite:('planned' if name==MILESTONES[0] else 'unrun')
                        for suite in rehearsal.SUITES}
                    (args.out/'job-plan.json').write_bytes(canonical_json(planned))
                    return 0
                raise rehearsal.ManualRelease('shipping_executor_unavailable')
            with mock.patch.object(rehearsal, '_validate_context'), \
                 mock.patch.object(rehearsal, 'select_retained_layout', return_value=(old['deployment'], ())), \
                 mock.patch.object(rehearsal, '_local_verifier', return_value=verifier), \
                 mock.patch.object(shipping, 'SHIPPING_TRANSACTION_INTEGRATED', False), \
                 mock.patch('scripts.update_rehearsal_milestone.run_milestone',
                     side_effect=dispatch) as milestone_entry, \
                 mock.patch.object(shipping.ShippingRehearsal, '__init__') as provision:
                code = rehearsal.qualify(['--source-ref', old['components']['standalone']['image']+'@'+old['components']['standalone']['digest'],
                    '--target-ref', target['components']['standalone']['image']+'@'+target['components']['standalone']['digest'],
                    '--platform', 'linux/amd64', '--deployment-dir', str(root),
                    '--reference-scale', 'registered-100k', '--out', str(root/'out')])
            self.assertEqual(code, 2)
            verifier.verify.assert_called_once()
            provision.assert_not_called()
            self.assertEqual(milestone_entry.call_count,len(MILESTONES)+1)
            plan_calls=milestone_entry.call_args_list[:len(MILESTONES)]
            self.assertEqual([call.args[0] for call in plan_calls],list(MILESTONES))
            self.assertTrue(all(call.args[1].dry_run for call in plan_calls))
            actual_call=milestone_entry.call_args_list[-1]
            self.assertEqual(actual_call.args[0],MILESTONES[0])
            self.assertFalse(actual_call.args[1].dry_run)
            failure = json.loads((root/'out/proof-row.json').read_bytes())
            self.assertEqual(failure['phase'], 'shipping_product_rehearsal')
            self.assertEqual(failure['refusal_code'], 'shipping_executor_unavailable')
            self.assertEqual(set(failure['suites'].values()), {'unrun'})
            self.assertFalse((root/'out/rows/evidence.json').exists())

    def test_local_package_requires_host_verification_of_its_own_signature(self):
        # No signing occurs: this is an explicitly stubbed packaging contract.
        # A successful sign command alone cannot attest the requested public key.
        for accepted in (False, True):
            with self.subTest(verified=accepted), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                candidate = default_v2()
                (root/'proof-row.json').write_bytes(canonical_json(dict(eligible=True, rows_directory='rows',
                    run_id=42, candidate_sha256=rehearsal.sha(canonical_json(candidate)))))
                (root/'candidate.json').write_bytes(canonical_json(candidate))
                (root/'test-private.pem').write_bytes(b'SYNTHETIC PRIVATE KEY NOT A REAL KEY')
                (root/'test-public.pem').write_bytes(b'SYNTHETIC PUBLIC KEY NOT A REAL KEY')
                (root/'rows').mkdir()
                (root/'rows/policy.json').write_bytes(b'{}')
                verifier = SimpleNamespace(verify=mock.Mock(return_value=SimpleNamespace(verified=accepted)))
                with mock.patch('scripts.build_update_compatibility_proof.build', return_value=(b'{}',canonical_json(candidate))) as build, \
                     mock.patch.object(rehearsal, '_local_verifier', return_value=verifier), \
                     mock.patch.object(rehearsal, 'command', return_value=b'') as sign:
                    code = rehearsal.package(['--rows', str(root), '--target', str(root/'candidate.json'),
                        '--key', str(root/'test-private.pem'), '--public-key', str(root/'test-public.pem'),
                        '--out', str(root/'package'), '--run-id', '42'])
                self.assertEqual(code, 0 if accepted else 2)
                build.assert_called_once()
                sign.assert_called_once()
                argv = sign.call_args.args[0]
                self.assertIn('--tlog-upload=false', argv)
                self.assertIn('--use-signing-config=false', argv)
                self.assertEqual(argv[0:3], ['cosign', 'sign-blob', '--yes'])
                verifier.verify.assert_called_once()
                fetched = verifier.verify.call_args.args[0]
                self.assertEqual(fetched.raw, canonical_json(candidate))
                self.assertEqual(fetched.bundle_path, root/'package/future-agi-update-manifest.sigstore.json')
                failure = root/'package/failure.json'
                if accepted:
                    self.assertFalse(failure.exists())
                else:
                    data = json.loads(failure.read_bytes())
                    self.assertEqual(data['phase'], 'verify_local_test_package')
                    self.assertEqual(data['assertion_detail'], 'local test package signature rejected')
                    self.assertFalse(data['eligible'])

    def test_product_json_list_is_not_mistaken_for_a_manifest_object(self):
        rows = [dict(identity='/data/collector/real-file', payload_sha256='a'*64)]
        raw = b'product startup log\n'+canonical_json({'rows': rows})+b'\n'
        self.assertEqual(shipping.decode_product_output(raw), {'rows': rows})
        self.assertEqual(shipping.decode_product_output(b'product startup log\n'+json.dumps(rows).encode()+b'\n'), rows)
        self.assertEqual(shipping.decode_product_output(b'[]\n'), [])
        for raw in (b'', b'[]\n\n', b'{"k":1,"k":2}\n', b'[NaN]\n', b'[]\nnot-json\n'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, rehearsal.RehearsalError)):
                shipping.decode_product_output(raw)

    def test_shipping_app_operation_crosses_real_supervisor_and_sends_json_stdin(self):
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.processes import ProcessSupervisor
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            # Only Docker is a fixture executable. The real supervisor must
            # accept the driver argv, forward stdin and execute isolated Python.
            docker = root/'docker'
            docker.write_text('#!'+sys.executable+'\nimport os,sys\na=sys.argv[1:]\n'
                'assert a[:8]==["--context","colima-fagi-update","exec","-i","app-id","python","-I","-B"]\n'
                'os.execv(sys.executable,[sys.executable,*a[6:]])\n')
            docker.chmod(0o700)
            driver = object.__new__(shipping.ShippingRehearsal)
            driver.root = root
            driver.installation = SimpleNamespace(containers=lambda: [dict(id='app-id', service='app')],
                docker=[str(docker), '--context', 'colima-fagi-update'], subprocess_env=lambda: {})
            driver.runner = ProcessSupervisor(AutomationPolicy(), docker_context='colima-fagi-update',
                environment={}, grace_seconds=.1)
            source = ('import json,sys\nassert sys.flags.isolated and sys.dont_write_bytecode\n'
                      'assert json.load(sys.stdin)=={"paths":None}\nprint("[]")\n')
            self.assertEqual(driver.app_python(source, {'paths': None}), [])
            self.assertFalse(driver.runner.uncertain)

    def test_redis_reset_requires_a_positive_source_seed_and_live_absence(self):
        driver = object.__new__(shipping.ShippingRehearsal)
        driver.corpus = {}
        driver.app_python = mock.Mock(return_value={'absent': True})
        with self.assertRaisesRegex(rehearsal.RehearsalError, 'not seeded'):
            driver._redis_ephemeral()
        driver.app_python.assert_not_called()
        driver.corpus['redis_source_seeded'] = True
        driver.redis_key = 'controlled-redis-key'
        driver.app_python.return_value = {'absent': False}
        with self.assertRaisesRegex(rehearsal.RehearsalError, 'persisted'):
            driver._redis_ephemeral()
        self.assertNotIn('redis_target_reset', driver.corpus)
        driver.app_python.return_value = {'absent': True}
        self.assertIsNone(driver._redis_ephemeral())
        driver.app_python.assert_called_with(shipping.REDIS_OPERATION,
            dict(action='verify_reset', key='controlled-redis-key'))
        self.assertEqual(driver.corpus['redis_target_reset'], {'absent': True})

    def test_all_target_readiness_cases_are_live_assertions_not_precomputed_verdicts(self):
        driver = object.__new__(shipping.ShippingRehearsal)
        driver.corpus = {}
        callbacks = driver.suite_assertions('target_readiness')
        self.assertEqual(set(callbacks), rehearsal.required_cases('target_readiness', rehearsal.topology_profile('standalone', ())))
        cases = rehearsal.ExecutedCases()
        # An uninitialized driver has no real target roster or model. Merely
        # exposing the callbacks cannot render an affirmative target suite.
        for name, assertion in callbacks.items():
            with self.subTest(case=name), self.assertRaises((AttributeError, KeyError, rehearsal.RehearsalError)):
                cases.check('target_readiness', name, assertion)
        self.assertEqual(cases.cases['target_readiness'], {})
        with self.assertRaises(rehearsal.RehearsalError):
            cases.render('target_readiness', rehearsal.topology_profile('standalone', ()), {})

    def test_clickhouse_witness_reader_uses_the_installation_credentials(self):
        driver = object.__new__(shipping.ShippingRehearsal)
        driver.installation = SimpleNamespace(containers=lambda: [dict(id='ch-id', service='clickhouse')],
            env={'CH_PASSWORD': 'not-a-real-secret', 'CH_USERNAME': 'fixture-user'})
        driver._docker = mock.Mock(return_value='{"id":"span"}\n')
        self.assertEqual(driver.clickhouse('SELECT 1', params={'trace': 'controlled-id'}), [{'id': 'span'}])
        self.assertEqual(driver._docker.call_args.args,
            ('exec', 'ch-id', 'clickhouse-client', '--user', 'fixture-user', '--password',
             'not-a-real-secret', '--query', 'SELECT 1', '--param_trace=controlled-id'))


class SourceSnapshotMilestoneTests(unittest.TestCase):
    """Milestone plumbing/refusals only; these tests are NOT product evidence."""
    def test_cli_exposes_bounded_mode_without_loosening_full_qualification(self):
        result = subprocess.run([sys.executable, '-I', str(rehearsal.ROOT/'scripts/rehearse_update_compatibility.py'),
            'qualify', '--help'], capture_output=True, text=True, timeout=30, cwd=str(rehearsal.ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        from scripts.update_rehearsal_milestone import BOUNDED_MILESTONES
        self.assertIn('--through {'+','.join(BOUNDED_MILESTONES)+'}', result.stdout)
        self.assertIn('--dry-run', result.stdout)
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'absent'
            for flags in ([], ['--dry-run'], ['--through','migration_forward'],
                          ['--through','source_snapshot','--source-ref','untrusted'],
                          ['--through','source_snapshot','--public-key','/not-a-key'],
                          ['--through','source_snapshot','--platform','linux/arm64'],
                          ['--through','source_snapshot','--boot-timeout','4201']):
                with self.subTest(flags=flags), self.assertRaises(SystemExit) as error:
                    rehearsal.qualify([*flags,'--out',str(out)])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(out.exists())
        with self.assertRaisesRegex(rehearsal.ManualRelease, 'shipping_executor_unavailable'):
            rehearsal.shipping_factory()

    def test_dry_run_runs_without_daemon_trust_or_product_inputs_and_cannot_package(self):
        from scripts import update_rehearsal_milestone as milestone
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'plan'
            with mock.patch.object(milestone,'SnapshotMilestone') as driver, \
                 mock.patch.object(rehearsal,'command') as command, \
                 mock.patch.object(rehearsal,'_local_verifier') as verifier:
                code = rehearsal.main(['qualify','--through','source_snapshot','--dry-run',
                                       '--out',str(out)])
            self.assertEqual(code, 0)
            driver.assert_not_called(); command.assert_not_called(); verifier.assert_not_called()
            result = json.loads((out/'milestone.json').read_bytes())
            plan = json.loads((out/'job-plan.json').read_bytes())
            for doc in (result, plan):
                self.assertFalse(doc['eligible']); self.assertFalse(doc['qualification_evidence'])
                self.assertEqual(doc['completed_rows'], 0)
                self.assertEqual(doc['milestone'], 'source_snapshot')
            self.assertEqual(result['executed_stages'], [])
            self.assertEqual(plan['source_image'], 'futureagi/standalone:v1.53.0@'+milestone.DIGEST)
            self.assertEqual(plan['boot_ceiling_seconds'], 4200)
            self.assertEqual(plan['backup_api'], 'StandaloneBackup.run_quiesced')
            self.assertEqual(set(plan['qualification_suites'].values()), {'unrun'})
            for name in ('proof-row.json','evidence.json','policy.json'):
                self.assertFalse((out/name).exists())
            self.assertEqual(rehearsal.package(['--rows',str(out),'--target','absent','--key','absent',
                '--public-key','absent','--out',str(out/'package'),'--run-id','1']), 2)
            self.assertFalse((out/'package').exists())

    def test_dry_run_entrypoint_isolated_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'plan'
            result = subprocess.run([sys.executable,'-I',str(rehearsal.ROOT/'scripts/rehearse_update_compatibility.py'),
                'qualify','--through','source_snapshot','--dry-run','--out',str(out)],
                capture_output=True,text=True,timeout=30,cwd=str(rehearsal.ROOT))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((out/'milestone.json').read_bytes())['status'], 'planned')

    def test_local_execution_refuses_before_git_or_docker_with_stage_reason(self):
        from scripts import update_rehearsal_milestone as milestone
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'evidence'
            with mock.patch.dict(os.environ, {'GITHUB_ACTIONS':'false'}), \
                 mock.patch.object(milestone,'SnapshotMilestone') as driver:
                code = rehearsal.main(['qualify','--through','source_snapshot','--out',str(out)])
            self.assertEqual(code, 2); driver.assert_not_called()
            failure = json.loads((out/'failure.json').read_bytes())
            self.assertEqual(failure['failure']['stage'], 'context_validation')
            self.assertIn('native amd64 Actions', failure['failure']['detail'])
            self.assertFalse(failure['eligible'])
            self.assertEqual(set(failure['qualification_suites'].values()), {'unrun'})
            self.assertFalse((out/'proof-row.json').exists())

    def test_outputs_are_create_only_including_dangling_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'existing'; out.mkdir(); keep = out/'keep'; keep.write_text('unchanged')
            link = Path(temporary)/'link'; link.symlink_to(Path(temporary)/'absent')
            for path in (out, link):
                self.assertEqual(rehearsal.main(['qualify','--through','source_snapshot','--dry-run','--out',str(path)]),2)
            self.assertEqual(keep.read_text(),'unchanged')
            self.assertEqual(list(out.iterdir()),[keep])

    def test_real_tag_archive_plus_current_updater_preserves_retained_A_bytes(self):
        from scripts import update_rehearsal_milestone as milestone
        from deploy.update.standalone_backup import CONFIG_FILES
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            driver = milestone.SnapshotMilestone(root, context='default', boot_timeout=4200)
            driver.assemble()  # git archive and local copies only; no Docker.
            self.assertEqual(set(driver.execution['files']),set(CONFIG_FILES[1:]))
            for name,digest in driver.execution['files'].items():
                self.assertEqual(hash_path(driver.root,name),digest)
            self.assertEqual((driver.root/'deploy/update/standalone_backup.py').read_bytes(),
                             (rehearsal.ROOT/'deploy/update/standalone_backup.py').read_bytes())
            self.assertEqual((driver.root/'bin/update').read_bytes(),(rehearsal.ROOT/'bin/update').read_bytes())
            self.assertFalse((driver.root/'.env').exists())
            self.assertFalse((driver.root/'futureagi/manage.py').exists())
            self.assertIn('not-release-manifest',driver.descriptor['kind'])
            self.assertFalse(driver.descriptor['eligible'])
            self.assertTrue(driver.project.startswith('fagi-upd-e2e-driver-m1-'))

    def test_archive_refuses_links_traversal_extra_files_and_duplicates(self):
        import io
        import tarfile
        from scripts import update_rehearsal_milestone as milestone
        for names, link in ((['../outside'],False),(['/outside'],False),(['unexpected.py'],False),
                            (['docker-compose.yml'],True),(['docker-compose.yml']*2,False)):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w') as archive:
                for name in names:
                    member=tarfile.TarInfo(name)
                    if link: member.type=tarfile.SYMTYPE; member.linkname='/etc/passwd'
                    archive.addfile(member)
            with tempfile.TemporaryDirectory() as temporary, \
                 mock.patch.object(rehearsal,'command',side_effect=[b'a'*40,buffer.getvalue()]):
                with self.subTest(names=names,link=link),self.assertRaises(rehearsal.RehearsalError):
                    milestone.archive_deployment(Path(temporary)/'retained')

    def test_milestone_stage_failure_is_nonzero_labeled_and_cleanup_is_required(self):
        from scripts import update_rehearsal_milestone as milestone
        for failed_stage in milestone.STAGES:
            with self.subTest(stage=failed_stage), tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                out=Path(temporary)/'evidence'
                driver=SimpleNamespace(deadline=time_limit(),cleanup_verified=False,
                    _collect_diagnostics=mock.Mock(),diagnostics=lambda:{'qualification_evidence':False})
                for method in ('assemble','install_and_boot','seed','observe_source','take_snapshot','verify_snapshot'):
                    setattr(driver,method,mock.Mock())
                def phase(name,operation):
                    if name==failed_stage: raise rehearsal.ManualRelease('suite_timed_out')
                    operation()
                driver._phase=phase
                def cleanup(): driver.cleanup_verified=True
                driver.cleanup=cleanup
                args=SimpleNamespace(out=out,boot_timeout=4200,docker_context='colima-fagi-update',dry_run=False)
                with mock.patch.object(milestone.sys,'platform','linux'), \
                     mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ARCH':'X64'}), \
                     mock.patch.object(milestone,'SnapshotMilestone',return_value=driver):
                    self.assertEqual(milestone.run(args),2)
                result=json.loads((out/'milestone.json').read_bytes())
                self.assertEqual(result['failure']['stage'],failed_stage)
                self.assertEqual(result['failure']['reason'],'suite_timed_out')
                self.assertTrue(result['cleanup_verified'])
                self.assertEqual(result['executed_stages'],list(milestone.STAGES[:milestone.STAGES.index(failed_stage)]))
                self.assertFalse((out/'proof-row.json').exists())

    def test_sigterm_finalizes_failed_stage_and_reaps_real_supervised_child(self):
        import signal
        from deploy.update.processes import ProcessSupervisor
        from scripts import update_rehearsal_milestone as milestone
        for supervised in (True, False):
            with self.subTest(supervised=supervised), tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                root = Path(temporary).resolve()
                out = root/'evidence'
                driver = milestone.SnapshotMilestone(root, context='colima-fagi-update', boot_timeout=4200)
                driver.runner = ProcessSupervisor(driver.policy, docker_context=driver.context,
                    environment={}, grace_seconds=.1)
                pidfile = root/'child.pid'
                def interrupted_stage():
                    if not supervised:
                        signal.raise_signal(signal.SIGTERM)
                        return
                    # The child signals only after the real supervisor installs
                    # its handler; no timer race and no daemon/product process.
                    driver.runner([sys.executable, '-I', '-B', '-c',
                        'import os,signal,sys,time\nfrom pathlib import Path\n'
                        'Path(sys.argv[1]).write_text(str(os.getpid()))\n'
                        'os.kill(os.getppid(),signal.SIGTERM)\ntime.sleep(30)\n', str(pidfile)], timeout=5)
                for method in ('assemble','seed','observe_source','take_snapshot','verify_snapshot'):
                    setattr(driver, method, mock.Mock())
                driver.install_and_boot = interrupted_stage
                def cleanup(): driver.cleanup_verified = True
                driver.cleanup = mock.Mock(side_effect=cleanup)
                args = SimpleNamespace(out=out, boot_timeout=4200, docker_context=driver.context, dry_run=False)
                previous = signal.getsignal(signal.SIGTERM)
                try:
                    with mock.patch.object(milestone.sys, 'platform', 'linux'), \
                         mock.patch.dict(os.environ, {'GITHUB_ACTIONS':'true','RUNNER_ARCH':'X64'}), \
                         mock.patch.object(milestone, 'SnapshotMilestone', return_value=driver):
                        code = milestone.run(args)
                finally:
                    self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
                self.assertEqual(code, 2)
                driver.cleanup.assert_called_once()
                result = json.loads((out/'milestone.json').read_bytes())
                self.assertEqual(result, json.loads((out/'failure.json').read_bytes()))
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['failure'], dict(stage='install_and_boot_a',
                    reason='operation_interrupted', exception_type='Interrupted'))
                self.assertEqual(result['executed_stages'], ['assemble_retained_a'])
                self.assertTrue(result['cleanup_verified'])
                self.assertFalse(result['eligible'])
                self.assertEqual(set(result['qualification_suites'].values()), {'unrun'})
                self.assertEqual(driver.diagnostics()['phase_timings'][-1]['refusal'], 'operation_interrupted')
                if supervised:
                    self.assertTrue(driver.runner.uncertain)
                    with self.assertRaises(ProcessLookupError): os.kill(int(pidfile.read_text()), 0)
                for name in ('proof-row.json','evidence.json','policy.json'):
                    self.assertFalse((out/name).exists())

    def test_actual_milestone_policy_constructs_real_cold_backup(self):
        from deploy.update.install import Installation
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.standalone_backup import BackupBinding, StandaloneBackup
        from scripts import update_rehearsal_milestone as milestone
        with tempfile.TemporaryDirectory() as temporary:
            driver = milestone.SnapshotMilestone(Path(temporary).resolve(),
                context='colima-fagi-update', boot_timeout=4200)
            driver.root.mkdir()
            (driver.root/'.env').write_text('COMPOSE_PROJECT_NAME='+driver.project+'\n')
            runner = ProcessSupervisor(driver.policy, docker_context=driver.context, environment={})
            installation = Installation(driver.root, runner=runner, environ={},
                docker=['docker','--context',driver.context], compose=['docker','compose'])
            # Constructor contract only: this is not a captured runtime binding
            # and does not claim that a backup or a product operation ran.
            binding = BackupBinding(canonical_json(dict(schema_version=1,
                root=str(driver.root), context=driver.context)))
            backup = StandaloneBackup(installation, binding, driver.directory/'backups')
            self.assertIs(backup.installation.runner.policy, driver.policy)
            self.assertEqual(driver.policy.probe_timeout_seconds, 90)

    def test_stubbed_milestone_success_is_not_qualification_and_cleanup_failure_is_red(self):
        from scripts import update_rehearsal_milestone as milestone
        for cleanup_ok in (True,False):
            with self.subTest(cleanup=cleanup_ok),tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                out=Path(temporary)/'evidence'
                driver=SimpleNamespace(deadline=time_limit(),cleanup_verified=False,
                    _collect_diagnostics=mock.Mock(),diagnostics=lambda:{'qualification_evidence':False})
                for method in ('assemble','install_and_boot','seed','observe_source','take_snapshot','verify_snapshot'):
                    setattr(driver,method,mock.Mock())
                driver._phase=lambda name,operation:operation()
                def cleanup():
                    if not cleanup_ok: raise RuntimeError('PRIVATE synthetic failure')
                    driver.cleanup_verified=True
                driver.cleanup=cleanup
                args=SimpleNamespace(out=out,boot_timeout=4200,docker_context='colima-fagi-update',dry_run=False)
                with mock.patch.object(milestone.sys,'platform','linux'), \
                     mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ARCH':'X64'}), \
                     mock.patch.object(milestone,'SnapshotMilestone',return_value=driver):
                    self.assertEqual(milestone.run(args),0 if cleanup_ok else 2)
                result=json.loads((out/'milestone.json').read_bytes())
                self.assertFalse(result['eligible']); self.assertEqual(result['completed_rows'],0)
                self.assertEqual(set(result['qualification_suites'].values()),{'unrun'})
                self.assertEqual(result['executed_stages'],list(milestone.STAGES))
                self.assertNotIn('PRIVATE',json.dumps(result))
                for name in ('proof-row.json','evidence.json','policy.json'):
                    self.assertFalse((out/name).exists())

    def test_workflow_job_is_native_bounded_read_only_and_always_uploads(self):
        from deploy.tests.test_update_release_static import load
        path=rehearsal.ROOT/'.github/workflows/update-rehearsal-milestones.yml'
        workflow=load(path)
        self.assertIn('workflow_dispatch',workflow['on'])
        self.assertIn('deploy/update/**',workflow['on']['pull_request']['paths'])
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        job=workflow['jobs']['source-snapshot']
        self.assertEqual(job['runs-on'],'ubuntu-24.04'); self.assertEqual(int(job['timeout-minutes']),150)
        self.assertEqual(job['permissions'],{'contents':'read'})
        checkout=next(s for s in job['steps'] if s.get('uses','').startswith('actions/checkout@'))
        self.assertEqual(int(checkout['with']['fetch-depth']),0)
        upload=next(s for s in job['steps'] if s.get('uses','').startswith('actions/upload-artifact@'))
        self.assertEqual(upload['if'],'always()')
        self.assertNotIn('workspace',upload['with']['path'])
        text=path.read_text()
        for unsafe in ('secrets.', 'id-token:', 'packages:', 'pull_request_target:', 'sign-blob', '--docker-context default'):
            self.assertNotIn(unsafe,text)
        self.assertIn('--through source_snapshot',text)
        self.assertIn('--boot-timeout 4200',text)
        self.assertIn('--docker-context colima-fagi-update',text)
        self.assertIn('docker --config "$dir" context create colima-fagi-update',text)

    def test_supported_signup_body_and_enveloped_tokens_are_not_guessed(self):
        from urllib.request import OpenerDirector
        driver=SimpleNamespace(ports={'BACKEND_PORT':1234},_secret_values=set())
        witnesses=shipping.ProductWitnesses(driver)
        response=mock.MagicMock()
        response.__enter__.return_value=response
        response.read.return_value=b'{"status":1,"result":{"access":"not-a-real-token"}}'
        opener=SimpleNamespace(open=mock.Mock(return_value=response))
        with mock.patch('urllib.request.build_opener',return_value=opener):
            self.assertEqual(witnesses._http('/accounts/token/',payload={'email':'test@example.test'}),
                             {'access':'not-a-real-token'})
        request=opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data),{'email':'test@example.test'})


class SourceSnapshotOperationTests(unittest.TestCase):
    """No daemon: execute driver control flow with explicitly stubbed I/O."""
    def test_boot_records_real_endpoint_samples_and_never_extends_its_ceiling(self):
        from scripts import update_rehearsal_milestone as milestone
        for ready in (True,False):
            with self.subTest(ready=ready),tempfile.TemporaryDirectory() as temporary:
                driver=milestone.SnapshotMilestone(Path(temporary).resolve(),context='colima-fagi-update',boot_timeout=10)
                clock=[0.0]
                driver.deadline=100
                driver.installation=SimpleNamespace(compose_run=mock.Mock(),run=mock.Mock(),
                    compose_args=lambda *args:['compose',*args],
                    containers=lambda:[{'id':str(i)} for i in range(3)])
                def inspection(*args):
                    healthy=ready and clock[0]>=5
                    return [dict(State={'Running':True,'Status':'running','OOMKilled':False,
                        'Health':{'Status':'healthy' if healthy else 'starting'}},RestartCount=0)]
                driver._json=inspection
                driver._collect_diagnostics=mock.Mock()
                driver.ports={'FRONTEND_PORT':3000,'BACKEND_PORT':8000}
                driver._secret_values={'generated-fixture-secret'}
                def request(self,port,path,**kwargs):
                    if path=='/health/': return 200,{}
                    return 200,dict(phase='migrating' if clock[0]>=5 else 'waiting',message='generated-fixture-secret')
                with mock.patch.object(milestone.time,'monotonic',side_effect=lambda:clock[0]), \
                     mock.patch.object(milestone.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)), \
                     mock.patch.object(milestone.ProductHarness,'request',request):
                    if ready:
                        driver._wait_healthy()
                    else:
                        with self.assertRaisesRegex(rehearsal.ManualRelease,'suite_timed_out'):
                            driver._wait_healthy()
                timing=json.loads((Path(temporary)/'phase-timings.json').read_bytes())
                self.assertEqual(timing['measurement'],'sampled-product-starting-status-endpoint')
                self.assertEqual([r['progress']['phase'] for r in timing['events']],['waiting','migrating'])
                self.assertLessEqual(timing['elapsed_seconds'],10)
                self.assertTrue(timing['intervals'])
                self.assertNotIn('generated-fixture-secret',json.dumps(timing))
                self.assertFalse(timing['eligible'])
                # The first start is the installer's: no updater ownership claim
                # exists yet, so the ownership-checked compose_run is not used.
                driver.installation.run.assert_called_once()
                self.assertEqual(driver.installation.run.call_args.args[0][:2], ['compose','up'])
                driver.installation.compose_run.assert_not_called()
                driver._collect_diagnostics.assert_called()

    def test_boot_restart_is_a_failed_source_not_a_silent_retry(self):
        from scripts import update_rehearsal_milestone as milestone
        with tempfile.TemporaryDirectory() as temporary:
            driver=milestone.SnapshotMilestone(Path(temporary).resolve(),context='colima-fagi-update',boot_timeout=4200)
            driver.ports={'FRONTEND_PORT':3000,'BACKEND_PORT':8000}
            driver.installation=SimpleNamespace(compose_run=mock.Mock(),run=mock.Mock(),
                compose_args=lambda *args:['compose',*args],containers=lambda:[{'id':'source'}])
            driver._json=lambda *args:[dict(State={'Running':True,'Status':'running'},RestartCount=1)]
            driver._collect_diagnostics=mock.Mock()
            with mock.patch.object(milestone.ProductHarness,'request',return_value=(200,{'phase':'retrying'})):
                with self.assertRaisesRegex(rehearsal.RehearsalError,'source boot failed'):
                    driver._wait_healthy()
            driver.installation.run.assert_called_once()
            driver.installation.compose_run.assert_not_called()

    def test_cold_snapshot_calls_shipping_quiesced_path_under_locks_without_legacy_restart(self):
        from scripts import update_rehearsal_milestone as milestone
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.standalone_backup import BackupError
        with tempfile.TemporaryDirectory() as temporary:
            driver=milestone.SnapshotMilestone(Path(temporary).resolve(),context='colima-fagi-update',boot_timeout=4200)
            driver.root.mkdir()
            driver.registration=SimpleNamespace(binding=SimpleNamespace(installation_fingerprint='a'*64))
            driver.daemon='fixture-daemon'; driver.descriptor={'eligible':False,'fixture':'not-product'}
            driver.source_observation=SimpleNamespace(binding_sha256='b'*64)
            driver.binding=SimpleNamespace(raw=b'not-used')
            driver.execution={'files':{'docker-compose.yml':'c'*64}}
            driver.image_inspections={'app':{'Id':'app-id'},'postgres':{'Id':'pg-id'},'clickhouse':{'Id':'ch-id'}}
            driver.installation=SimpleNamespace(root=driver.root,runner=SimpleNamespace(policy=AutomationPolicy()),
                config=lambda **kw:{'name':'synthetic-source-model'})
            backup=mock.Mock()
            def quiesced(expected,ledger,observation,*,proof_directory_fd,proof_filename):
                self.assertEqual(proof_filename,expected.attempt_id+'.json')
                self.assertEqual(expected.target_manifest_hash,rehearsal.manifest_hash(driver.intent))
                self.assertEqual(ledger.snapshot['direction'],'forward_pending')
                self.assertEqual(ledger.snapshot['phase'],'attempt_reserved')
                self.assertTrue((driver.directory/'runner.lock').is_file())
                self.assertTrue(os.fstat(proof_directory_fd).st_mode)
                self.assertIs(observation,driver.source_observation)
                raise BackupError('synthetic_test_failure')
            backup.run_quiesced.side_effect=quiesced
            with mock.patch('deploy.update.standalone_backup.capture_binding',return_value=driver.binding), \
                 mock.patch('deploy.update.standalone_backup.StandaloneBackup',return_value=backup):
                with self.assertRaisesRegex(BackupError,'synthetic_test_failure'):
                    driver.take_snapshot()
            backup.run_quiesced.assert_called_once(); backup.run.assert_not_called()
            self.assertFalse((Path(temporary)/'proof-row.json').exists())
            intent=json.loads((Path(temporary)/'snapshot-intent.json').read_bytes())
            self.assertFalse(intent['eligible'])
            self.assertEqual(intent['kind'],'snapshot-only-intent-not-target-manifest')

    def test_disposable_cleanup_never_removes_an_unidentified_helper(self):
        from scripts import update_rehearsal_milestone as milestone
        from deploy.update.contracts import AutomationPolicy
        with tempfile.TemporaryDirectory() as temporary:
            driver=milestone.SnapshotMilestone(Path(temporary).resolve(),context='colima-fagi-update',boot_timeout=4200)
            driver.policy=AutomationPolicy()
            driver.runner=SimpleNamespace(environment={})
            driver.installation=SimpleNamespace(runner=driver.runner)
            driver.ledger=SimpleNamespace(snapshot={'helpers':{},'attempt_id':'a'*32})
            driver._docker=mock.Mock(return_value='foreign-id')
            driver._json=mock.Mock(return_value=[{}])
            with self.assertRaisesRegex(rehearsal.RehearsalError,'cleanup helper identity unconfirmed'):
                driver.cleanup()
            self.assertFalse(any(call.args[:1]==('rm',) for call in driver._docker.call_args_list))
            self.assertFalse(driver.cleanup_verified)

    def test_real_operation_adapter_enumerates_core_domains_and_only_honest_spool_gaps(self):
        import base64
        driver=SimpleNamespace(ports={},_secret_values=set(),corpus={})
        witnesses=shipping.ProductWitnesses(driver)
        calls=[]
        # v1.53.0 UserSerializer returns `name`; `full_name` is signup-only.
        info={'id':'user-id','email':'fixture@example.test','name':'Fixture',
              'organization':{'id':'org-id','name':'Fixture'},'default_workspace_id':'ws-id',
              'default_workspace_name':'default','default_workspace_display_name':'Default'}
        def http(path,**kwargs):
            calls.append((path,kwargs))
            if path=='/accounts/signup/': info['email']=kwargs['payload']['email']
            if path=='/accounts/token/': return {'access':'synthetic-token'}
            if path=='/accounts/user-info/': return info
            if path=='/accounts/keys/': return {'data':{'api_key':'fixture-api','secret_key':'fixture-secret'}}
            return {}
        witnesses._http=http
        def operation(source,arg):
            if source==shipping.SPOOL_OPERATION: return []
            if source==shipping.S3_OPERATION:
                return {'identity':arg['bucket']+'/'+arg['key'],
                        'payload_sha256':rehearsal.sha(base64.b64decode(arg['body']))}
            self.assertEqual(source,shipping.TEMPORAL_OPERATION)
            return {'identity':arg['id']+'/completed-run','run_id':'completed-run','payload_sha256':'e'*64}
        driver.app_python=operation
        driver.clickhouse=lambda sql,params:[{'id':params['span'],'trace_id':params['trace'],'name':'Fixture'}]
        with self.assertRaisesRegex(rehearsal.ManualRelease,'witness_operation_unavailable'):
            witnesses.create('source')
        signup=next(kwargs['payload'] for path,kwargs in calls if path=='/accounts/signup/')
        self.assertTrue(signup['company_name'])
        rows=witnesses.read('source')
        self.assertEqual({r['domain'] for r in rows},{'postgres_app','clickhouse','object_store','temporal'})
        self.assertEqual(len([r for r in rows if r['domain']=='postgres_app']),3)
        self.assertEqual({r['identity'] for r in rows if r['domain']=='postgres_app'},
                         {'user/user-id','organization/org-id','workspace/ws-id'})
        self.assertEqual({g['family'] for g in driver.corpus['witness_gaps']},{'collector','telemetry'})
        self.assertTrue(all(g['reason']=='no_attributable_naturally_produced_spool' for g in driver.corpus['witness_gaps']))
        self.assertTrue(all(len(r['payload_sha256'])==64 for r in rows))
        user = next(r for r in rows if r['identity']=='user/user-id')
        self.assertEqual(user['payload_sha256'], rehearsal.manifest_hash(
            {k:info[k] for k in ('id','email','name')}))
        info['name'] = 'Changed seeded name'
        changed = witnesses.read('source')
        self.assertNotEqual(user['payload_sha256'], next(
            r['payload_sha256'] for r in changed if r['identity']=='user/user-id'))
        self.assertEqual([r for r in rows if r['identity']!='user/user-id'],
                         [r for r in changed if r['identity']!='user/user-id'])

    def test_product_api_failures_keep_http_reason_without_credentials_or_error_body(self):
        from urllib.error import HTTPError
        driver=SimpleNamespace(ports={'BACKEND_PORT':1234})
        witnesses=shipping.ProductWitnesses(driver)
        opener=SimpleNamespace(open=mock.Mock(side_effect=HTTPError('private-url',400,'PRIVATE',{},None)))
        with mock.patch('urllib.request.build_opener',return_value=opener):
            with self.assertRaisesRegex(rehearsal.RehearsalError,'product witness HTTP 400 on /accounts/signup/') as error:
                witnesses._http('/accounts/signup/',payload={'password':'PRIVATE'})
        self.assertNotIn('PRIVATE',str(error.exception))

    def test_workflow_shell_steps_parse_without_execution(self):
        from deploy.tests.test_update_release_static import load
        workflow=load(rehearsal.ROOT/'.github/workflows/update-rehearsal-milestones.yml')
        count=0
        for step in workflow['jobs']['source-snapshot']['steps']:
            if 'run' not in step: continue
            result=subprocess.run(['bash','-n'],input=step['run'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            count+=1
        self.assertEqual(count,4)


class TargetReadinessMilestoneTests(unittest.TestCase):
    """Daemon-free plumbing and real engine component tests, NOT release evidence."""
    def test_target_dry_run_is_fixed_digest_bounded_and_never_packagable(self):
        from scripts import update_rehearsal_milestone as m
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'plan'
            with mock.patch.object(m,'TargetMilestone') as construct, mock.patch.object(rehearsal,'command') as command:
                self.assertEqual(rehearsal.main(['qualify','--through','target_readiness','--dry-run','--out',str(out)]),0)
            construct.assert_not_called(); command.assert_not_called()
            data = json.loads((out/'job-plan.json').read_bytes())
            self.assertEqual(data['target_image'],m.TARGET_IMAGE)
            self.assertEqual(data['stages'],list(m.TARGET_STAGES))
            self.assertEqual(data['resources_prefix'],'fagi-upd-e2e-m2-')
            self.assertEqual(data['stability_period_seconds'],15)
            self.assertIn('migration_stall',data['guards'])
            for doc in (data,json.loads((out/'milestone.json').read_bytes())):
                self.assertEqual(doc['milestone'],'target_readiness')
                self.assertFalse(doc['eligible']); self.assertFalse(doc['qualification_evidence'])
                self.assertEqual(doc['completed_rows'],0)
            self.assertEqual(set(data['qualification_suites'].values()),{'unrun'})
            for leaf in ('proof-row.json','policy.json','evidence.json'): self.assertFalse((out/leaf).exists())
            with self.assertRaisesRegex(rehearsal.ManualRelease,'shipping_executor_unavailable'):
                rehearsal.shipping_factory()

    def test_m2_retains_m1_policy_but_uses_own_prefix_and_active_runtime_guards(self):
        from scripts import update_rehearsal_milestone as m
        with tempfile.TemporaryDirectory() as temporary:
            driver=m.TargetMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
            self.assertTrue(driver.project.startswith(m.TARGET_PREFIX))
            self.assertEqual(driver.policy.probe_timeout_seconds,90)
            self.assertEqual(driver.policy.migration_timeout_seconds,1200)
            self.assertEqual(driver.policy.migration_stall_timeout_seconds,300)
            self.assertGreaterEqual(driver.policy.disk_watermark_reserve_bytes,1024**3)
            self.assertEqual(driver.policy.stability_period_seconds,15)
            driver.record('sample.json',m.label(status='not-product-evidence'))
            self.assertEqual(json.loads((Path(temporary)/'sample.json').read_bytes())['milestone'],'target_readiness')

    def test_m2_executes_both_milestones_on_same_driver_and_fails_each_target_stage(self):
        from scripts import update_rehearsal_milestone as m
        for failure in (None,'prepare_target','target_readiness'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                out=Path(temporary)/'evidence'
                calls=[]
                driver=SimpleNamespace(deadline=time_limit(),cleanup_verified=False,
                    _collect_diagnostics=lambda:None,diagnostics=lambda:{'qualification_evidence':False})
                for name in ('assemble','install_and_boot','seed','observe_source','take_snapshot','verify_snapshot',
                             'prefetch_target','prepare_target','target_readiness'):
                    setattr(driver,name,mock.Mock())
                def phase(name,operation):
                    calls.append(name)
                    if name==failure: raise rehearsal.ManualRelease('migration_measurement_unavailable')
                    operation()
                driver._phase=phase
                driver.cleanup=lambda:setattr(driver,'cleanup_verified',True)
                args=SimpleNamespace(out=out,boot_timeout=4200,docker_context='colima-fagi-update',
                                     dry_run=False,through='target_readiness')
                with mock.patch.object(m.sys,'platform','linux'), \
                     mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ARCH':'X64'}), \
                     mock.patch.object(m,'TargetMilestone',return_value=driver) as construct:
                    self.assertEqual(m.run(args),0 if failure is None else 2)
                construct.assert_called_once()
                result=json.loads((out/'milestone.json').read_bytes())
                self.assertTrue(result['cleanup_verified']); self.assertFalse(result['eligible'])
                self.assertEqual(result['milestone'],'target_readiness')
                stop=len(m.TARGET_STAGES) if failure is None else m.TARGET_STAGES.index(failure)
                self.assertEqual(result['executed_stages'],list(m.TARGET_STAGES[:stop]))
                if failure: self.assertEqual(result['failure']['stage'],failure)
                self.assertFalse((out/'proof-row.json').exists())

    def test_real_shipping_scope_calls_public_start_under_lock_with_native_envcommit(self):
        from deploy.tests.test_update_automation_boundaries import RuntimeComponentWorld
        from deploy.update import envfile
        world=RuntimeComponentWorld(self)
        try:
            entry=world.engine.start_automatic_target_locked
            calls=[]
            from contextlib import contextmanager
            @contextmanager
            def checked(**kwargs):
                self.assertIsNotNone(world.engine.state.lock_holder(automation=True))
                self.assertIsNotNone(kwargs['ledger'].backup_seal)
                with entry(**kwargs) as result:
                    self.assertEqual(envfile.check_commit(world.engine._env_path(),
                        world.engine.active_record['env_recovery'],durable=True),'accepted')
                    calls.append(result[1])
                    yield result
            world.engine.start_automatic_target_locked=checked
            world.run()
            self.assertEqual(len(calls),1)
            self.assertEqual(world.creates,['v1.1.0'])
            self.assertEqual(world.ledger.direction,'success_decided')
            self.assertEqual(world.engine.automatic_outcome,'succeeded')
        finally: world.close()

    def test_public_start_refuses_without_lock_before_env_or_container_mutations(self):
        from deploy.update.apply import Updater
        from deploy.update.orchestrator import GuardRefused
        engine=object.__new__(Updater)
        engine.state=SimpleNamespace(lock_holder=lambda **kwargs:None)
        engine.automation_guard=None
        with self.assertRaisesRegex(GuardRefused,'recovery_required'):
            with engine.start_automatic_target_locked(ledger=None,plan={},estimate=None,observe=None):
                self.fail('unlocked entry yielded')

    def test_runtime_observer_injection_keeps_real_runtime_and_default_unchanged(self):
        from deploy.update.apply import Updater
        from deploy.update.automatic_runtime import AutomaticRuntime,HostRuntimeObserver
        engine=object.__new__(Updater)
        engine.installation=SimpleNamespace()
        engine.settings=SimpleNamespace(automation_policy=SimpleNamespace(probe_timeout_seconds=90))
        lifecycle=SimpleNamespace(registration=object())
        backup=SimpleNamespace(b={})
        for supplied in (None,object()):
            engine.runtime_observer=supplied
            runtime=engine._automatic_runtime(lifecycle,object(),backup)
            self.assertIsInstance(runtime,AutomaticRuntime)
            if supplied is None: self.assertIsInstance(runtime.observer,HostRuntimeObserver)
            else: self.assertIs(runtime.observer,supplied)

    def test_capacity_helpers_follow_binding_project_and_cleanup_even_after_failed_create(self):
        from deploy.update import docker_host as host
        from deploy.update.install import CommandResult
        class CreateFailure(RuntimeError): pass
        runner=mock.Mock(side_effect=CreateFailure)
        inst=SimpleNamespace(docker=['docker','--context','colima-fagi-update'],root=Path('/not-run'),
            subprocess_env=lambda:{},runner=runner,
            docker_run=mock.Mock(return_value=SimpleNamespace(returncode=0,stdout='',stderr='')))
        binding=SimpleNamespace(compose_project='fagi-upd-e2e-m2-test')
        with mock.patch.object(host,'checked_daemon'), \
             mock.patch.object(host,'docker_json',return_value=[dict(Id='sha256:'+'a'*64,Os='linux',Config={})]), \
             mock.patch.object(host,'bounded_installation',return_value=inst):
            with self.assertRaises(CreateFailure):
                host.run_readonly_helper(inst,binding,image_id='sha256:'+'a'*64,source='print(1)')
        argv=runner.call_args.args[0]
        self.assertTrue(argv[argv.index('--name')+1].startswith(binding.compose_project+'-observe-'))
        self.assertEqual(inst.docker_run.call_count,2)

    def test_offline_loader_compiles_and_reads_real_django_executor_not_hardcoded_zero(self):
        compile(shipping.TARGET_LOADER_PLAN,'<fixed-target-loader>','exec')
        self.assertIn('MigrationLoader(None',shipping.TARGET_LOADER_PLAN)
        self.assertIn('executor.migration_plan(loader.graph.leaf_nodes())',shipping.TARGET_LOADER_PLAN)
        self.assertIn("a['applied']",shipping.TARGET_LOADER_PLAN)
        self.assertIn('migration.replaces',shipping.TARGET_LOADER_PLAN)
        # Django's executor copies applied_migrations with dict(...): a set of
        # two-string nodes would silently become app -> name and replay all.
        import ast
        assignment = next(node for node in ast.parse(shipping.TARGET_LOADER_PLAN).body
            if isinstance(node,ast.Assign) and any(isinstance(target,ast.Attribute) and
                target.attr=='applied_migrations' for target in node.targets))
        applied = eval(compile(ast.Expression(assignment.value),'<fixed-applied-expression>','eval'),
                       {'a':{'applied':[['a','0001'],['a','0002']]}})
        self.assertEqual(dict(applied),{('a','0001'):None,('a','0002'):None})

    def test_live_target_oracle_rejects_changed_inventory_witnesses_and_unmeasured_nonzero_plan(self):
        from scripts import update_rehearsal_milestone as m
        for altered in ('none','inventory','witness','pending'):
            with self.subTest(altered=altered),tempfile.TemporaryDirectory() as temporary:
                driver=m.TargetMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
                nodes=[['a','0001']]
                migration=dict(planned=nodes,applied=nodes,pending=[],inventory_sha256='a'*64)
                driver.target_plan=dict(planned=nodes,inventory_sha256='a'*64,pending=[])
                if altered=='inventory': migration['inventory_sha256']='b'*64
                if altered=='pending': driver.target_plan['pending']=[dict(app_label='a',name='0001')]
                driver.source_migrations={'applied':nodes}
                rows=[dict(domain='postgres_app',identity='user/id',payload_sha256='c'*64)]
                project={'id':'p','name':'n','organization_id':'o','workspace_id':'w'}
                driver.project_witnesses=[project]
                driver.corpus={'source_witnesses':sorted(rows+[dict(domain='postgres_app',origin='product_api',
                    identity='project/p',payload_sha256=rehearsal.manifest_hash(project))],key=lambda r:(r['domain'],r['identity']))}
                driver.witnesses=SimpleNamespace(read=mock.Mock(return_value=rows if altered!='witness' else []),
                    namespaces={'source':dict(namespace='n',headers={'X-Organization-Id':'o','X-Workspace-Id':'w'})})
                driver.app_python=mock.Mock(return_value=[project])
                driver.ports={'BACKEND_PORT':1,'FRONTEND_PORT':2}
                with mock.patch.object(m.ProductHarness,'migration_inventory',return_value=migration), \
                     mock.patch.object(m.ProductHarness,'request',return_value=(200,{})):
                    if altered=='none': driver.verify_target()
                    else:
                        with self.assertRaises((rehearsal.RehearsalError,rehearsal.ManualRelease)):
                            driver.verify_target()
                data=json.loads((Path(temporary)/'target-migrations.json').read_bytes())
                self.assertFalse(data['eligible'])
                self.assertEqual(data['pending_count'],1 if altered=='pending' else 0)
                if altered=='pending': self.assertIsNone(data['per_migration_execution'])

    def test_target_image_loader_is_networkless_readonly_and_exactly_cleaned_on_success_or_oom(self):
        from scripts import update_rehearsal_milestone as m
        for oom in (False,True):
            with self.subTest(oom=oom),tempfile.TemporaryDirectory() as temporary:
                driver=m.TargetMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
                driver.root.mkdir()
                driver.source_migrations={'applied':[['a','0001']]}
                driver.target_inspection={'Id':'sha256:'+'b'*64,'Config':{'Volumes':{'/data':{}}}}
                driver.installation=SimpleNamespace(docker=['docker','--context','colima-fagi-update'],
                    subprocess_env=lambda:{},config=lambda **kw:{'services':{'app':{'environment':{'ENV_TYPE':'local'}}}})
                state={}
                def runner(argv,**kwargs):
                    if 'create' in argv:
                        self.assertTrue(kwargs['text'])
                        self.assertEqual(argv[argv.index('--network')+1],'none')
                        self.assertIn('--read-only',argv); self.assertNotIn('--mount',argv)
                        self.assertEqual(argv[argv.index('--restart')+1],'no')
                        self.assertIn('/data:rw,noexec,nosuid,nodev,size=1m',argv)
                        name=argv[argv.index('--name')+1]
                        token=argv[argv.index('--label')+1].split('=',1)[1]
                        state['row']=dict(Id='c'*64,Name='/'+name,Image=driver.target_inspection['Id'],
                            Config={'Labels':{'futureagi.rehearsal.measurement':token}},
                            HostConfig={'ReadonlyRootfs':True,'NetworkMode':'none','RestartPolicy':{'Name':'no'}},
                            Mounts=[{'Type':'tmpfs'}],State={'Running':False,'Status':'created'})
                        return SimpleNamespace(returncode=0,stdout='c'*64+'\n',stderr='')
                    self.assertEqual(argv[-4:],['start','-a','-i','c'*64])
                    self.assertEqual(json.loads(kwargs['input']),{'applied':[['a','0001']]})
                    state['row']['State'].update(ExitCode=137 if oom else 0,OOMKilled=oom)
                    return SimpleNamespace(returncode=137 if oom else 0,stdout=b'{"pending":[]}\n',stderr=b'')
                driver.runner=runner
                driver._json=lambda *args:[state['row']]
                def docker_run(*args,**kwargs):
                    if args[0]=='ps': value='c'*64 if 'row' in state else ''
                    elif args[0]=='inspect': value=json.dumps([state['row']])
                    else:
                        self.assertEqual(args,('rm','-f','c'*64)); del state['row']; value=''
                    return SimpleNamespace(stdout=value)
                cleanup=SimpleNamespace(docker_run=docker_run)
                with mock.patch('deploy.update.docker_host.bounded_installation',return_value=cleanup):
                    if oom:
                        with self.assertRaisesRegex(rehearsal.RehearsalError,'target loader failed'): driver.image_plan()
                    else: self.assertEqual(driver.image_plan(),{'pending':[]})
                self.assertTrue(driver.loader_cleanup_verified)
                self.assertNotIn('row',state)

    def test_target_loader_uses_real_supervisor_routing_stdin_and_attached_timeout_path(self):
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.processes import ProcessSupervisor
        from scripts import update_rehearsal_milestone as milestone
        from scripts import update_rehearsal_shipping as shipping
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve(); state_path=root/'container.json'; command_path=root/'python-argv.json'
            image_id='sha256:'+'b'*64; container_id='c'*64
            docker=root/'docker'
            fake='#!'+sys.executable+'\n'+'''import json, subprocess, sys
from pathlib import Path
args=sys.argv[1:]
assert args[:2]==['--context','colima-fagi-update']
state_path=Path(%r); command_path=Path(%r); image_id=%r; container_id=%r
if args[2]=='create':
    assert args[args.index('--entrypoint')+1]=='/usr/local/bin/python3'
    assert args[args.index('--network')+1]=='none'
    assert '--read-only' in args and args[args.index('--restart')+1]=='no'
    index=args.index(image_id)
    python_argv=args[index+1:]
    assert python_argv[:2]==['-I','-B'] and python_argv[2].startswith('-c')
    assert json.dumps({'applied':[['a','0001']]}) not in args
    command_path.write_text(json.dumps(python_argv))
    name=args[args.index('--name')+1]; token=args[args.index('--label')+1].split('=',1)[1]
    row={'Id':container_id,'Name':'/'+name,'Image':image_id,
         'Config':{'Labels':{'futureagi.rehearsal.measurement':token}},
         'HostConfig':{'ReadonlyRootfs':True,'NetworkMode':'none','RestartPolicy':{'Name':'no'}},
         'Mounts':[{'Type':'tmpfs'}], 'State':{'Running':False,'Status':'created'}}
    state_path.write_text(json.dumps(row)); print(container_id)
elif args[2]=='start':
    assert args[3:5]==['-a','-i'] and args[5]==container_id
    row=json.loads(state_path.read_text()); python_argv=json.loads(command_path.read_text())
    child=subprocess.run([sys.executable,*python_argv],input=sys.stdin.buffer.read(),capture_output=True)
    row['State'].update(ExitCode=child.returncode,OOMKilled=False,Running=False,Status='exited')
    state_path.write_text(json.dumps(row)); sys.stdout.buffer.write(child.stdout); sys.stderr.buffer.write(child.stderr)
    raise SystemExit(child.returncode)
else: raise SystemExit(91)
'''%(str(state_path),str(command_path),image_id,container_id)
            docker.write_text(fake); docker.chmod(0o700)
            driver=milestone.RestoreMilestone(root,context='colima-fagi-update',boot_timeout=4200)
            driver.root=root; driver.source_migrations={'applied':[['a','0001']]}
            driver.target_inspection={'Id':image_id,'Config':{'Volumes':{'/data':{}}}}
            driver.installation=SimpleNamespace(docker=[str(docker),'--context','colima-fagi-update'],
                subprocess_env=lambda:{},config=lambda **kw:{'services':{'app':{'environment':{'ENV_TYPE':'local'}}}})
            driver.runner=ProcessSupervisor(AutomationPolicy(),docker_context='colima-fagi-update',environment={})
            self.assertEqual(driver.runner._budget([*driver.installation.docker,'create'],30),30)
            self.assertEqual(driver.runner._budget([*driver.installation.docker,'start','-a','-i',container_id],180),180)
            driver._json=lambda *args:[json.loads(state_path.read_text())]
            def cleanup_run(*args,**kwargs):
                if args[0]=='ps': value=container_id if state_path.exists() else ''
                elif args[0]=='inspect': value=json.dumps([json.loads(state_path.read_text())])
                else:
                    self.assertEqual(args,('rm','-f',container_id)); state_path.unlink(); value=''
                return SimpleNamespace(stdout=value)
            cleanup=SimpleNamespace(docker_run=cleanup_run)
            source=('import json,sys\nassert sys.flags.isolated and sys.dont_write_bytecode\n'
                    'value=json.load(sys.stdin)\nprint(json.dumps({"pending":[]} if value["applied"]==[["a","0001"]] else {"pending":["mismatch"]}))\n')
            self.assertIn('settings.LOGGING_CONFIG=None',shipping.TARGET_LOADER_PLAN)
            self.assertIn('django.setup()',shipping.TARGET_LOADER_PLAN)
            with mock.patch.object(shipping,'TARGET_LOADER_PLAN',source), \
                    mock.patch('deploy.update.docker_host.bounded_installation',return_value=cleanup):
                self.assertEqual(driver.image_plan(),{'pending':[]})
            self.assertTrue(driver.loader_cleanup_verified)
            self.assertFalse(driver.runner.uncertain)
            self.assertFalse(state_path.exists())
            self.assertEqual(json.loads(command_path.read_text())[:2],['-I','-B'])

    def test_public_entry_refuses_missing_preimage_and_watermark_before_env_write(self):
        from deploy.tests.test_update_automation_boundaries import RuntimeComponentWorld
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.contracts import AutomaticUpdateEstimate
        from contextlib import contextmanager
        for broken in ('preimage','reserve'):
            with self.subTest(broken=broken):
                world=RuntimeComponentWorld(self)
                try:
                    entry=world.engine.start_automatic_target_locked
                    @contextmanager
                    def invalid(**kwargs):
                        if broken=='preimage': world.guard.approved_env_sha256=None
                        else:
                            from dataclasses import replace
                            kwargs['estimate']=replace(kwargs['estimate'],protected_restore_reserve_bytes=0)
                        with entry(**kwargs) as result: yield result
                    world.engine.start_automatic_target_locked=invalid
                    with self.assertRaises(GuardRefused): world.run()
                    self.assertNotIn('v1.1.0',world.creates)
                    self.assertEqual(world.engine.automatic_outcome,'restored')
                finally: world.close()

    def test_m2_workflow_is_same_session_native_read_only_bounded_and_always_uploads(self):
        from deploy.tests.test_update_release_static import load
        workflow=load(rehearsal.ROOT/'.github/workflows/update-rehearsal-milestones.yml')
        job=workflow['jobs']['target-readiness']
        self.assertEqual(job['runs-on'],'ubuntu-24.04')
        self.assertLessEqual(int(job['timeout-minutes']),150)
        self.assertEqual(job['permissions'],{'contents':'read'})
        commands=[s['run'] for s in job['steps'] if 'run' in s]
        actual=[c for c in commands if '--through target_readiness' in c and '--dry-run' not in c]
        self.assertEqual(len(actual),1)
        self.assertIn('timeout --signal=TERM --kill-after=180s 135m',actual[0])
        for command in commands:
            result=subprocess.run(['bash','-n'],input=command,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
        upload=next(s for s in job['steps'] if s.get('uses','').startswith('actions/upload-artifact@'))
        self.assertEqual(upload['if'],'always()')
        self.assertIn('/*.json',upload['with']['path'])
        self.assertNotIn('workspace',upload['with']['path'])


def time_limit():
    import time
    return time.monotonic()+30


class MilestoneImagePlatformTests(unittest.TestCase):
    # Leaf manifests of the published multi-arch indexes, recorded from the
    # registry. The first native M2 run failed because the target was pinned
    # to the arm64 leaf while the runner and the source are amd64.
    LEAVES = {
        'v1.53.0': {'linux/amd64': 'sha256:1bcaf9deccd6ff8b999a66e8ebd44657811d91419aeb5339a6d6327d1cbef7fd',
                    'linux/arm64': 'sha256:c88acf35821b4b78d1406ec03c86c072381738429ffeaaa2175103c5c23fd863'},
        'v1.54.0': {'linux/amd64': 'sha256:c2b0f344b4e9f60435c6ce503764fbc5be793c9d44776cb6ff12b7c92bdd3492',
                    'linux/arm64': 'sha256:22be1ed65d5e67f361ac9e99dc4c49873bbf7daf4a193c906d1689aa1249c056'},
    }

    def test_source_and_target_are_pinned_to_the_runner_platform_leaf(self):
        from scripts import update_rehearsal_milestone as milestone
        self.assertEqual(milestone.DIGEST, self.LEAVES[milestone.VERSION][milestone.PLATFORM])
        self.assertEqual(milestone.TARGET_DIGEST, self.LEAVES[milestone.TARGET_VERSION][milestone.PLATFORM])
        self.assertTrue(milestone.TARGET_IMAGE.endswith('@' + milestone.TARGET_DIGEST))
class RestoreMilestoneTests(unittest.TestCase):
    """Daemon-free protocol regressions, NEVER product qualification evidence."""
    def test_all_milestones_plan_and_cli_are_nonqualifying_and_use_fixed_native_leaf(self):
        from scripts import update_rehearsal_milestone as m
        self.assertEqual(m.RESTORE_DIGEST,'sha256:eab0d007debaf55ca2e45bcd936a143cbc95d2f61fd0324eaf8b4be7e0f5ccc9')
        self.assertEqual(m.BOUNDED_MILESTONES,('source_snapshot','target_readiness','failure_restore',
                                      'interruption_restore','backup_integrity'))
        for name in m.BOUNDED_MILESTONES[2:]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                out=Path(temporary)/'out'
                with mock.patch.object(m,'RestoreMilestone') as construct, mock.patch.object(rehearsal,'command') as command:
                    self.assertEqual(rehearsal.main(['qualify','--through',name,'--dry-run','--out',str(out)]),0)
                construct.assert_not_called(); command.assert_not_called()
                plan=json.loads((out/'job-plan.json').read_bytes())
                self.assertEqual(plan['target_image'],m.RESTORE_IMAGE)
                self.assertEqual(plan['resources_prefix'],'fagi-upd-e2e-m3-')
                self.assertEqual(plan['stages'][-1],name)
                self.assertFalse(plan['enrolled_baseline_commit_tested'])
                for file in out.glob('*.json'):
                    doc=json.loads(file.read_bytes())
                    self.assertFalse(doc['eligible']); self.assertFalse(doc['qualification_evidence'])
                    self.assertEqual(doc['completed_rows'],0)
                self.assertEqual(set(plan['qualification_suites'].values()),{'unrun'})
                self.assertFalse((out/'proof-row.json').exists())

    def test_offline_django_setup_disables_file_logging_without_skipping_setup(self):
        import io, logging.config
        from types import ModuleType
        settings=SimpleNamespace(LOGGING_CONFIG='logging.config.dictConfig',LOGGING={
            'version':1,'handlers':{'file':{'class':'logging.FileHandler','filename':'/never/a/writable/file'}}})
        django=ModuleType('django'); conf=ModuleType('django.conf'); conf.settings=settings
        called=[]
        def setup():
            if settings.LOGGING_CONFIG is not None: logging.config.dictConfig(settings.LOGGING)
            called.append(True)
        django.setup=setup
        with mock.patch.dict(sys.modules,{'django':django,'django.conf':conf}), \
             mock.patch.object(sys,'path',list(sys.path)), mock.patch.object(sys,'stdin',io.StringIO('{}')), \
             mock.patch.dict(os.environ):
            exec(compile(shipping.OFFLINE_DJANGO_PREFIX,'<offline-setup>','exec'),{})
        self.assertEqual(called,[True]); self.assertIsNone(settings.LOGGING_CONFIG)
        self.assertIn('TARGET_LOADER_PLAN',vars(shipping))
        self.assertIn('MigrationLoader(None',shipping.TARGET_LOADER_PLAN)

    def test_restore_policy_reserves_fit_job_without_waiving_runtime_guards(self):
        from scripts import update_rehearsal_milestone as m
        with tempfile.TemporaryDirectory() as temporary:
            driver=m.RestoreMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
            self.assertTrue(driver.project.startswith('fagi-upd-e2e-m3-'))
            self.assertLess(driver.policy.required_cold_budget_seconds,m.TOTAL_SECONDS-600)
            self.assertEqual(driver.policy.probe_timeout_seconds,90)
            self.assertGreaterEqual(driver.policy.disk_watermark_reserve_bytes,1024**3)
            self.assertGreater(driver.policy.stability_period_seconds,0)
            self.assertLessEqual(driver.policy.migration_stall_timeout_seconds,driver.policy.migration_timeout_seconds)
            self.assertEqual(driver.snapshot_timeout,driver.policy.backup_timeout_seconds)

    def test_interruption_plan_budgets_every_boundary_and_leaves_runner_tail(self):
        from scripts import update_rehearsal_milestone as m
        plan=m.plan(4200,'interruption_restore')
        policy=m.restore_policy()
        self.assertEqual(plan['interruption_boundaries'],list(m.INTERRUPTION_BOUNDARIES))
        self.assertEqual(plan['boundary_budget_seconds'],policy.required_cold_budget_seconds)
        self.assertEqual(plan['all_boundaries_budget_seconds'],
                         len(m.INTERRUPTION_BOUNDARIES)*policy.required_cold_budget_seconds)
        self.assertEqual(plan['minimum_total_seconds'],4200+m.INTERRUPTION_SETUP_RESERVE_SECONDS+
                         plan['all_boundaries_budget_seconds'])
        self.assertLessEqual(plan['minimum_total_seconds'],plan['total_ceiling_seconds'])
        self.assertEqual(plan['total_ceiling_seconds'],m.INTERRUPTION_TOTAL_SECONDS)
        self.assertEqual(plan['job_timeout_minutes'],360)
        self.assertEqual(plan['command_timeout_minutes'],354)
        self.assertLess(plan['command_timeout_minutes'],plan['job_timeout_minutes'])

    def test_interruption_success_requires_complete_restored_matrix(self):
        from scripts import update_rehearsal_milestone as m
        full=[dict(boundary=boundary,outcome='restored') for boundary in m.INTERRUPTION_BOUNDARIES]
        self.assertIsNone(m.validate_interruption_results(full))
        with self.assertRaisesRegex(rehearsal.RehearsalError,'interruption_boundaries_incomplete'):
            m.validate_interruption_results(full[:1])
        blocked=[dict(boundary=m.INTERRUPTION_BOUNDARIES[0],outcome='recovery_required',
                      retained_evidence=True)]
        with self.assertRaisesRegex(rehearsal.RehearsalError,'interruption_recovery_required'):
            m.validate_interruption_results(blocked)
        blocked[0]['retained_evidence']=False
        with self.assertRaisesRegex(rehearsal.RehearsalError,'interruption_recovery_evidence_missing'):
            m.validate_interruption_results(blocked)

    def test_restore_milestones_execute_prerequisites_on_one_driver(self):
        from scripts import update_rehearsal_milestone as m
        for name in m.BOUNDED_MILESTONES[2:]:
            with self.subTest(name=name),tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
                calls=[]
                driver=SimpleNamespace(deadline=time_limit(),cleanup_verified=False,
                    _collect_diagnostics=lambda:None,diagnostics=lambda:{'qualification_evidence':False})
                for method in ('assemble','install_and_boot','seed','observe_source','take_snapshot','verify_snapshot',
                               'prefetch_target','prepare_target','failure_restore','interruption_restore','backup_integrity'):
                    setattr(driver,method,mock.Mock())
                driver._phase=lambda name,op:(calls.append(name),op())
                driver.cleanup=lambda:setattr(driver,'cleanup_verified',True)
                args=SimpleNamespace(out=Path(temporary)/'evidence',boot_timeout=4200,
                    docker_context='colima-fagi-update',dry_run=False)
                with mock.patch.object(m.sys,'platform','linux'), \
                     mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ARCH':'X64'}), \
                     mock.patch.object(m,'RestoreMilestone',return_value=driver) as construct:
                    self.assertEqual(m.run_milestone(name,args),0)
                construct.assert_called_once()
                self.assertEqual(calls,m.plan(4200,name)['stages'])
                if name=='interruption_restore':
                    driver.take_snapshot.assert_not_called()
                    self.assertEqual(construct.call_args.kwargs['total_seconds'],m.INTERRUPTION_TOTAL_SECONDS)
                doc=json.loads((args.out/'milestone.json').read_bytes())
                self.assertTrue(doc['cleanup_verified']); self.assertFalse(doc['eligible'])
                self.assertEqual(doc['milestone'],name)

    def test_target_fault_requires_executed_pending_migration_and_real_api_record(self):
        from scripts import update_rehearsal_milestone as m
        for executed in (False,True):
            with self.subTest(executed=executed),tempfile.TemporaryDirectory() as temporary:
                driver=m.RestoreMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
                driver.source_migrations={'applied':[['a','0001']]}
                driver.target_plan={'pending':[dict(app_label='a',name='0002')]}
                migration={'pending':[],'applied':[['a','0001']]+([['a','0002']] if executed else [])}
                state={'Running':True,'Health':{'Status':'healthy'}}
                docker=mock.Mock(side_effect=lambda *args:state.update(Running=False))
                driver.installation=SimpleNamespace(docker_run=docker)
                driver.witnesses=SimpleNamespace(_http=mock.Mock())
                driver.app_python=mock.Mock(return_value={'exists':True})
                runtime=SimpleNamespace(_exact=lambda r:{'State':state})
                with mock.patch.object(m.ProductHarness,'migration_inventory',return_value=migration):
                    if executed: driver.target_ready(runtime,{'app':{'Id':'a'*64}})
                    else:
                        with self.assertRaisesRegex(rehearsal.RehearsalError,'did not execute'):
                            driver.target_ready(runtime,{'app':{'Id':'a'*64}})
                self.assertEqual(driver.fault_injected,executed)
                self.assertEqual(driver.target_only_created,executed)
                if executed:
                    docker.assert_called_once_with('stop','--time','60','a'*64)
                    driver.witnesses._http.assert_called_once()
                    evidence=json.loads((Path(temporary)/'target-only.private.json').read_bytes())
                    self.assertTrue(evidence['independently_read']); self.assertFalse(evidence['eligible'])
                else: docker.assert_not_called(); driver.witnesses._http.assert_not_called()

    def test_any_post_backup_failure_restores_but_only_deliberate_fault_is_green(self):
        from scripts import update_rehearsal_milestone as m
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.contracts import ReasonCode
        from deploy.update.receipt import StateDir
        from contextlib import contextmanager
        for expected in (False,True):
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as temporary:
                driver=m.RestoreMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
                driver.root.mkdir(); driver.runner=SimpleNamespace(deadline=driver.deadline)
                original=driver.runner.deadline
                driver._remember_resources=mock.Mock()
                @contextmanager
                def fail():
                    self.assertLessEqual(driver.runner.deadline,original-driver.policy.required_recovery_budget_seconds)
                    self.assertIsNotNone(StateDir(driver.root).lock_holder(automation=True))
                    driver.fault_injected=expected
                    if expected: raise GuardRefused(ReasonCode.READINESS_FAILED)
                    raise rehearsal.RehearsalError('unexpected product failure')
                    yield
                driver.running_target=fail
                driver.recover=mock.Mock(side_effect=lambda failure:self.assertEqual(driver.runner.deadline,original))
                if expected: driver.failure_restore()
                else:
                    with self.assertRaisesRegex(rehearsal.RehearsalError,'unexpected product failure'):
                        driver.failure_restore()
                driver.recover.assert_called_once()
                self.assertIsNotNone(driver.recover.call_args.args[0])
                self.assertEqual(driver.runner.deadline,original)

    @staticmethod
    def registered_store(root, target, now):
        from deploy.update.runner_state import RunnerStateStore, Registration, LauncherConfig
        from deploy.update.contracts import InstallationBinding
        store=RunnerStateStore(root,root/'.env')
        binding=InstallationBinding('a'*32,store.selection_hash,'b'*64,root,root/'.env',root/'.env',
            'standalone',False,'synthetic-milestone', (root/'docker-compose.yml',),(),
            'colima-fagi-update','synthetic-daemon',os.geteuid())
        registration=Registration(1,binding,LauncherConfig(Path(sys.executable).resolve(),Path.home(),os.defpath,'tester'),now)
        store.register(registration)
        return store,registration,store.reserve_attempt(target,'always',now)

    def test_real_engine_resumption_uses_milestone_lifecycle_receipts_and_durable_latch(self):
        from dataclasses import replace
        from deploy.tests.test_update_automation_boundaries import RuntimeComponentWorld, RuntimeCrashCut
        from deploy.update.apply import Updater
        from deploy.update.runner_state import TargetIdentity
        from scripts import update_rehearsal_milestone as m
        world=RuntimeComponentWorld(self)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                driver=m.RestoreMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
                driver.root=world.case.root.resolve(); driver.installation=world.case.inst
                driver.policy=world.policy; driver.backup=world.backup
                driver.store,driver.registration,driver.attempt=self.registered_store(driver.root,
                    TargetIdentity('v1.1.0','f'*64),world.now)
                world.expected=replace(world.expected,attempt_id=driver.attempt.attempt_id)
                world.guard.expectation=world.expected
                original=world.event
                def cut(label):
                    original(label)
                    if label=='backup_after': raise RuntimeCrashCut(label)
                world.event=cut
                with self.assertRaises(RuntimeCrashCut): world.run()
                world.event=original
                driver.ledger=world.ledger; driver.descriptor={'unsigned':True}; driver.target_version='v1.1.0'
                driver.verify_restored_source=mock.Mock(return_value=world.lifecycle._observation())
                driver.record_genuine_tree=mock.Mock()  # Synthetic Docker/storage, not product evidence.
                with mock.patch.object(Updater,'_automatic_runtime',side_effect=lambda life,ledger,backup:
                    world.runtime(world.case.inst,ledger,backup.b)):
                    with world.case.state.lock(automation=True): result=driver.recover()
                    self.assertEqual(result.outcome,'restored')
                    generation=driver.store.read_state().generation
                    with world.case.state.lock(automation=True): driver.recover()
                self.assertEqual(driver.ledger.direction,'restore_decided')
                self.assertEqual(driver.ledger.phase,'restored')
                self.assertEqual(driver.store.read_state().generation,generation)
                self.assertEqual(world.extractions,1)
                self.assertNotIn('v1.1.0',world.creates)
                self.assertEqual(driver.store.read_state().failed_target_latch,driver.attempt.target)
                driver.verify_restored_source.assert_called()
        finally: world.close()

    def test_known_failure_uses_updater_failure_handler_and_persists_noninterrupted_reason(self):
        from dataclasses import replace
        from deploy.tests.test_update_automation_boundaries import RuntimeComponentWorld, RuntimeCrashCut
        from deploy.update.apply import Updater
        from deploy.update.automatic_runtime import RuntimeRefused
        from deploy.update.contracts import ReasonCode
        from deploy.update.runner_state import TargetIdentity
        from scripts import update_rehearsal_milestone as m
        world=RuntimeComponentWorld(self)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                driver=m.RestoreMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
                driver.root=world.case.root.resolve(); driver.installation=world.case.inst
                driver.policy=world.policy; driver.backup=world.backup
                driver.store,driver.registration,driver.attempt=self.registered_store(driver.root,
                    TargetIdentity('v1.1.0','f'*64),world.now)
                world.expected=replace(world.expected,attempt_id=driver.attempt.attempt_id)
                world.guard.expectation=world.expected
                original=world.event
                def cut(label):
                    original(label)
                    if label=='backup_after': raise RuntimeCrashCut(label)
                world.event=cut
                with self.assertRaises(RuntimeCrashCut): world.run()
                world.event=original
                driver.ledger=world.ledger; driver.descriptor={'unsigned':True}; driver.target_version='v1.1.0'
                driver.verify_restored_source=mock.Mock(return_value=world.lifecycle._observation())
                driver.record_genuine_tree=mock.Mock()
                with mock.patch.object(Updater,'_automatic_runtime',side_effect=lambda life,ledger,backup:
                    world.runtime(world.case.inst,ledger,backup.b)):
                    failure=RuntimeRefused(ReasonCode.READINESS_FAILED)
                    with world.case.state.lock(automation=True): result=driver.recover(failure)
                self.assertEqual(result,{'outcome':'restored'})
                self.assertEqual(driver.ledger.snapshot['forward_failure']['reason_code'],'readiness_failed')
                self.assertFalse(driver.ledger.snapshot['forward_failure']['interrupted'])
                self.assertEqual(driver.ledger.direction,'restore_decided')
                self.assertEqual(driver.ledger.phase,'restored')
                self.assertEqual(world.extractions,1)
                self.assertEqual(driver.store.read_state().failed_target_latch,driver.attempt.target)
        finally: world.close()

    def test_actual_sigkill_happens_after_each_real_durable_ledger_boundary(self):
        from deploy.update.backup_proof import BackupExpectation
        from deploy.update.contracts import BackupSeal
        from deploy.update.recovery import RecoveryLedger
        from scripts import update_rehearsal_milestone as m
        for boundary in m.INTERRUPTION_BOUNDARIES:
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve(); output=root/'evidence'; output.mkdir(mode=0o700)
                driver=m.RestoreMilestone(output,context='colima-fagi-update',boot_timeout=4200)
                destination=root/'backups'; destination.mkdir(mode=0o700)
                now=datetime.now(timezone.utc)
                expected=BackupExpectation('a'*32,'b'*64,'c'*64,m.VERSION,now,('postgres_app',))
                ledger=RecoveryLedger.reserve(destination,expected,operation_token='token',run_id='run',binding_sha256='d'*64)
                seal=BackupSeal(expected.attempt_id,'token','run','d'*64,'c'*64,m.VERSION,
                    'e'*64,'f'*64,'1'*64,'2'*64,now,now)
                if boundary!='backup_sealed': ledger.seal_backup(seal)
                if boundary=='extract_intent':
                    ledger.decide_restore(); ledger.volume_phase('app-data','clear_intent'); ledger.volume_phase('app-data','empty')
                def killed():
                    with driver.kill_at(boundary):
                        if boundary=='backup_sealed': ledger.seal_backup(seal)
                        elif boundary=='env_selected': ledger.checkpoint('env_selected')
                        elif boundary=='target_started': ledger.checkpoint('target_verifying')
                        else: ledger.volume_phase('app-data','extract_intent')
                pid=driver.child_process(killed,killed=True)
                reopened=RecoveryLedger.open(ledger.directory,expected=expected)
                self.assertEqual(reopened.backup_seal,seal)
                evidence=json.loads((output/'kill-boundary.json').read_bytes())
                self.assertEqual(evidence['pid'],pid); self.assertEqual(evidence['boundary'],boundary)
                self.assertTrue(evidence['actual_durable_write_completed'])
                def recover_direction():
                    fresh=RecoveryLedger.open(ledger.directory,expected=expected)
                    self.assertEqual(fresh.resume_direction(),'restore_decided')
                recovery_pid=driver.child_process(recover_direction,result_name='fresh-test-process.json')
                self.assertNotEqual(recovery_pid,pid)
                self.assertEqual(RecoveryLedger.open(ledger.directory).direction,'restore_decided')
                self.assertFalse(evidence['qualification_evidence'])

    def test_no_interruption_case_is_claimed_when_job_budget_is_insufficient(self):
        from scripts import update_rehearsal_milestone as m
        with tempfile.TemporaryDirectory() as temporary:
            driver=m.RestoreMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
            driver.deadline=0
            driver.child_process=mock.Mock()
            with self.assertRaisesRegex(rehearsal.RehearsalError,'interruption_job_budget_insufficient'):
                driver.interruption_restore()
            driver.child_process.assert_not_called()
            doc=json.loads((Path(temporary)/'interruption-summary.json').read_bytes())
            self.assertEqual(doc['boundaries'],[]); self.assertEqual(doc['unrun'],list(m.INTERRUPTION_BOUNDARIES))

    def test_every_workflow_upload_file_excludes_generated_fixture_credentials(self):
        import glob
        from scripts import update_rehearsal_milestone as m
        from deploy.tests.test_update_release_static import load
        workflow=load(rehearsal.ROOT/'.github/workflows/update-rehearsal-milestones.yml')
        secrets={key:'generated-'+key.lower()+'-fixture-value' for key in
                 ('SECRET_KEY','PG_PASSWORD','CH_PASSWORD','MINIO_ROOT_PASSWORD')}
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            ledger=root/'private-ledger'; ledger.mkdir(mode=0o700)
            (ledger/'recovery.json').write_bytes(canonical_json({'source_model':{
                'services':{'app':{'environment':secrets}}}}))
            for name in m.AUTHORITY_EVIDENCE_LEAVES | set(m.SNAPSHOT_EVIDENCE_LEAVES):
                (ledger/name).write_bytes(canonical_json({'safe_projection':name}))
            from deploy.update.standalone_backup import _identity
            identity=_identity(dict(Id='a'*64,Image='sha256:'+'b'*64,Created='fixture',
                Config={'Env':[key+'='+value for key,value in secrets.items()]},
                HostConfig={},Mounts=[]))
            operation=dict(kind='stop',identity=identity)
            private_files={
                'inventory.json':canonical_json({'containers':{'app':identity}}),
                'op-0001-intent.json':canonical_json(operation),
                'op-0001-result.json':canonical_json({'operation':operation,'result':{'stopped':True}}),
            }
            for name,raw in private_files.items():
                (ledger/name).write_bytes(raw)
                self.assertTrue(all(value.encode() in raw for value in secrets.values()))
            self.assertTrue(all(value.encode() in (ledger/'recovery.json').read_bytes()
                                for value in secrets.values()))

            output_names={
                'source-snapshot':'fagi-upd-e2e-driver-m1-42-1',
                'target-readiness':'fagi-upd-e2e-m2-42-1',
                'failure-restore':'fagi-upd-e2e-m3-failure-42-1',
                'interruption-restore':'fagi-upd-e2e-m3-interruption-42-1',
                'backup-integrity':'fagi-upd-e2e-m3-integrity-42-1',
            }
            for job_name, output_name in output_names.items():
                output=root/output_name; output.mkdir(mode=0o700)
                (output/'milestone.json').write_bytes(canonical_json({'safe':True}))
                m.copy_snapshot_evidence(ledger,output)
                (output/'snapshot-app-data.members.jsonl').write_text('{"safe":true}\\n')
                if job_name in ('failure-restore','interruption-restore','backup-integrity'):
                    authority=output/'authority'; authority.mkdir(mode=0o700)
                    m.copy_authority_evidence(ledger,authority)
                    (output/'authority-inventory.json').write_bytes(canonical_json({'safe':True}))
                if job_name=='interruption-restore':
                    nested=output/'interruption-backup_sealed'; nested.mkdir(mode=0o700)
                    (nested/'milestone.json').write_bytes(canonical_json({'safe':True}))
                    m.copy_snapshot_evidence(ledger,nested)
                    (nested/'snapshot-app-data.members.jsonl').write_text('{"safe":true}\\n')
                    nested_authority=nested/'authority'; nested_authority.mkdir(mode=0o700)
                    m.copy_authority_evidence(ledger,nested_authority)
                    (nested/'authority-inventory.json').write_bytes(canonical_json({'safe':True}))

            # Drive the real backup_integrity producer against a synthetic
            # ledger containing the same credential-bearing model as recovery.json.
            from contextlib import nullcontext
            from types import SimpleNamespace
            from deploy.update.standalone_backup import BackupError
            integrity_output=root/output_names['backup-integrity']
            workspace=integrity_output/'workspace'; workspace.mkdir(mode=0o700)
            driver=object.__new__(m.RestoreMilestone)
            driver.output=integrity_output; driver.directory=workspace
            driver.ledger=SimpleNamespace(directory=ledger)
            driver.expected=SimpleNamespace(attempt_id='synthetic-integrity-attempt')
            driver.policy=SimpleNamespace(disk_watermark_reserve_bytes=1)
            driver.backup=SimpleNamespace(policy=object())
            driver.installation=object(); driver.binding=object()
            driver.locks=lambda:nullcontext()
            driver.recover=mock.Mock(); driver._remember_resources=mock.Mock()
            private_checkpoint=dict(phase='restore_decided',direction='restore_decided',pending=None,
                backup_seal={'sealed':True},volumes={},source_model={'services':{'app':{'environment':secrets}}},
                target_model={'services':{'app':{'environment':secrets}}})
            fake_ledger=SimpleNamespace(snapshot=private_checkpoint,decide_restore=mock.Mock())
            with mock.patch.object(m.RestoreMilestone,'corrupt_copy'), \
                 mock.patch('deploy.update.recovery.RecoveryLedger.open',return_value=fake_ledger), \
                 mock.patch('deploy.update.standalone_restore.StandaloneRestore') as adapter_class, \
                 mock.patch.object(m.shutil,'disk_usage',return_value=SimpleNamespace(free=10**15)):
                adapter_class.return_value.resume.side_effect=BackupError('archive_invalid')
                driver.backup_integrity()
            integrity_doc=json.loads((integrity_output/'integrity-truncated_archive.json').read_bytes())
            self.assertEqual(integrity_doc['checkpoint'],dict(direction_is_restore=True,
                operation_pending=False,backup_sealed=True,completed_volume_count=0))
            self.assertNotIn('source_model',json.dumps(integrity_doc))
            self.assertNotIn('target_model',json.dumps(integrity_doc))

            for job_name in output_names:
                upload=next(step for step in workflow['jobs'][job_name]['steps']
                            if step.get('uses','').startswith('actions/upload-artifact@'))
                path_lines=[line.strip() for line in upload['with']['path'].splitlines() if line.strip()]
                includes=[line for line in path_lines if not line.startswith('!')]
                excludes=[line[1:] for line in path_lines if line.startswith('!')]
                self.assertIn('**/recovery.json',excludes)
                self.assertIn('**/snapshot-recovery.json',excludes)
                self.assertNotIn('members.jsonl',upload['with']['path'])
                resolve=lambda pattern: pattern.replace('${{ github.run_id }}','42').replace(
                    '${{ github.run_attempt }}','1').replace('~',str(root),1).replace(
                    'interruption-*','interruption-backup_sealed')
                for pattern in includes:
                    resolved=resolve(pattern)
                    parent,filename=resolved.rsplit('/',1)
                    directory=Path(parent); directory.mkdir(mode=0o700,parents=True,exist_ok=True)
                    if filename=='*.json':
                        sample='evidence.json' if directory.name!='authority' else 'proof.json'
                    elif filename=='snapshot-*.members.jsonl':
                        sample='snapshot-clickhouse-data.members.jsonl'
                    else:
                        continue
                    path=directory/sample
                    if not path.exists(): path.write_bytes(canonical_json({'safe':True}))
                uploaded=set()
                for pattern in includes:
                    uploaded.update(Path(path) for path in glob.glob(resolve(pattern),recursive=True) if Path(path).is_file())
                excluded=set()
                for pattern in excludes:
                    excluded.update(Path(path) for path in glob.glob(resolve(pattern),recursive=True) if Path(path).is_file())
                for path in uploaded-excluded:
                    with self.subTest(job=job_name,path=path.relative_to(root)):
                        raw=path.read_bytes()
                        for value in secrets.values(): self.assertNotIn(value.encode(),raw)
                self.assertFalse((root/output_names[job_name]/'snapshot-recovery.json').exists())
                self.assertFalse((root/output_names[job_name]/'snapshot-inventory.json').exists())
                if job_name in ('failure-restore','interruption-restore','backup-integrity'):
                    authority=root/output_names[job_name]/'authority'
                    self.assertFalse((authority/'recovery.json').exists())
                    for name in private_files:
                        self.assertFalse((authority/name).exists())
            for name,raw in private_files.items():
                self.assertEqual((ledger/name).read_bytes(),raw)

    def test_corrupt_copies_are_rejected_by_real_restore_input_verifier_before_any_clear(self):
        import shutil
        from deploy.tests.test_update_standalone_restore import RestoreTests, LocalHelpers
        from deploy.update.standalone_restore import StandaloneRestore
        from deploy.update.standalone_backup import BackupError
        from deploy.update.recovery import RecoveryLedger,RecoveryError
        from scripts import update_rehearsal_milestone as m
        fixture=RestoreTests('runTest'); fixture.setUp()
        try:
            original=fixture.ledger.directory
            hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in original.iterdir() if f.is_file()}
            for case in m.INTEGRITY_CASES:
                with self.subTest(case=case),tempfile.TemporaryDirectory() as temporary:
                    destination=Path(temporary).resolve(); destination.chmod(0o700)
                    copied=destination/fixture.expected.attempt_id; shutil.copytree(original,copied)
                    if case=='substituted_archive':
                        # Model a DIFFERENT archive; this synthetic daemon uses
                        # identical tar fixtures in all three roles by default.
                        with (copied/'clickhouse-data.tar').open('ab') as f: f.write(b'\0'*512)
                    m.RestoreMilestone.corrupt_copy(copied,case)
                    ledger=RecoveryLedger.open(copied,expected=fixture.expected)
                    adapter=StandaloneRestore(fixture.fixture.install,fixture.fixture.binding,destination,
                        policy=fixture.fixture.adapter.policy,clock=lambda:fixture.fixture.adapter.clock())
                    LocalHelpers.calls=[]
                    try:
                        with self.assertRaises((RecoveryError,BackupError)): adapter.resume(fixture.expected,ledger)
                    finally: adapter.close()
                    self.assertEqual(ledger.snapshot['volumes'],{})
                    self.assertFalse(any(purpose in ('clear','extract') for _,purpose in LocalHelpers.calls))
            self.assertEqual({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in original.iterdir() if f.is_file()},hashes)
        finally: fixture.doCleanups()

    def test_genuine_tree_comparison_rejects_missing_or_modified_extracted_inventory(self):
        from scripts import update_rehearsal_milestone as m
        for invalid in (None,'missing','modified'):
            with self.subTest(invalid=invalid),tempfile.TemporaryDirectory() as temporary:
                driver=m.RestoreMilestone(Path(temporary),context='colima-fagi-update',boot_timeout=4200)
                directory=driver.directory/'sealed'; directory.mkdir(mode=0o700)
                raw=b'{"synthetic_tree":true}\n'
                inventory={'archives':{role:dict(members_sha256=hashlib.sha256(raw).hexdigest(),members_bytes=len(raw))
                    for role in rehearsal.VOLUME_ROLES}}
                (directory/'inventory.json').write_bytes(canonical_json(inventory))
                for role in rehearsal.VOLUME_ROLES:
                    for index in range(2 if invalid!='missing' else 1):
                        (directory/('verified-tree-'+role+'-'+str(index)+'.jsonl')).write_bytes(
                            raw if invalid!='modified' else b'{"synthetic_tree":false}\n')
                driver.ledger=SimpleNamespace(directory=directory)
                if invalid is None: driver.record_genuine_tree()
                else:
                    with self.assertRaises(rehearsal.RehearsalError): driver.record_genuine_tree()

    def test_all_restore_workflow_jobs_are_parallel_native_read_only_and_always_upload(self):
        from deploy.tests.test_update_release_static import load
        from scripts import update_rehearsal_milestone as m
        workflow=load(rehearsal.ROOT/'.github/workflows/update-rehearsal-milestones.yml')
        for name in m.BOUNDED_MILESTONES[2:]:
            with self.subTest(name=name):
                job=workflow['jobs'][name.replace('_','-')]
                self.assertNotIn('needs',job)
                self.assertEqual(job['runs-on'],'ubuntu-24.04')
                expected_timeout=360 if name=='interruption_restore' else 150
                self.assertEqual(int(job['timeout-minutes']),expected_timeout)
                self.assertEqual(job['permissions'],{'contents':'read'})
                commands=[s['run'] for s in job['steps'] if 'run' in s]
                actual=[c for c in commands if '--through '+name in c and '--dry-run' not in c]
                self.assertEqual(len(actual),1)
                for command in commands:
                    result=subprocess.run(['bash','-n'],input=command,text=True,capture_output=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertNotIn('secrets.',command)
                upload=next(s for s in job['steps'] if s.get('uses','').startswith('actions/upload-artifact@'))
                self.assertEqual(upload['if'],'always()'); self.assertNotIn('workspace',upload['with']['path'])
                self.assertIn('!**/recovery.json',upload['with']['path'])
                self.assertIn('!**/snapshot-recovery.json',upload['with']['path'])
                if name=='interruption_restore':
                    self.assertIn('/interruption-*/*.json',upload['with']['path'])
                    self.assertIn('timeout --signal=TERM --kill-after=180s 354m',''.join(commands))
