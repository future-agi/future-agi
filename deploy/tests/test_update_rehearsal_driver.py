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

    def test_protected_real_entrypoint_missing_executor_is_red_and_diagnosable(self):
        with tempfile.TemporaryDirectory(prefix='fagi-release-refusal-',dir=str(Path.home())) as temporary:
            root=Path(temporary).resolve()
            candidate=default_v2(); path=root/'candidate.json'; path.write_bytes(canonical_json(candidate))
            env=dict(os.environ,GITHUB_REPOSITORY=rehearsal.REPOSITORY,GITHUB_RUN_ID='42',
                     GITHUB_REF='refs/tags/'+candidate['version'],GITHUB_SHA=candidate['source_sha'],
                     GITHUB_EVENT_NAME='workflow_call')
            result=subprocess.run([sys.executable,'-I',str(rehearsal.ROOT/'scripts/rehearse_update_compatibility.py'),
                '--target',str(path),'--workspace',str(root/'work'),'--output',str(root/'out'),'--run-id','42',
                '--docker-context','colima-fagi-update'],cwd=str(rehearsal.ROOT),env=env,
                capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,2,result.stdout+result.stderr)
            self.assertIn('release rehearsal refused: shipping_executor_unavailable',result.stderr)
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

    def test_failed_or_unrun_suites_always_exit_nonzero_with_an_explicit_reason(self):
        candidate=default_v2()
        for reason in ('suite_failed','suite_unrun','suite_timed_out','migration_measurement_unavailable','witness_operation_unavailable'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary); path=root/'candidate.json'; path.write_bytes(canonical_json(candidate))
                env=dict(GITHUB_REPOSITORY=rehearsal.REPOSITORY,GITHUB_RUN_ID='42',
                         GITHUB_REF='refs/tags/'+candidate['version'],GITHUB_SHA=candidate['source_sha'],
                         GITHUB_EVENT_NAME='workflow_call')
                with mock.patch.dict(os.environ,env),mock.patch.object(rehearsal,'_validate_context'), \
                     mock.patch.object(rehearsal,'shipping_factory',side_effect=rehearsal.ManualRelease(reason)), \
                     mock.patch.object(rehearsal,'ReleaseDiscovery') as discovery, \
                     mock.patch('sys.stderr') as stderr:
                    code=rehearsal.main(['--target',str(path),'--workspace',str(root/'work'),
                                         '--output',str(root/'out'),'--run-id','42'])
                self.assertEqual(code,2)
                self.assertIn(reason,''.join(str(call.args[0]) for call in stderr.write.call_args_list))
                discovery.assert_not_called()
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
            with mock.patch.object(rehearsal, '_validate_context'), \
                 mock.patch.object(rehearsal, 'select_retained_layout', return_value=(old['deployment'], ())), \
                 mock.patch.object(rehearsal, '_local_verifier', return_value=verifier), \
                 mock.patch.object(shipping, 'SHIPPING_TRANSACTION_INTEGRATED', False), \
                 mock.patch.object(shipping.ShippingRehearsal, '__init__') as provision:
                code = rehearsal.qualify(['--source-ref', old['components']['standalone']['image']+'@'+old['components']['standalone']['digest'],
                    '--target-ref', target['components']['standalone']['image']+'@'+target['components']['standalone']['digest'],
                    '--platform', 'linux/amd64', '--deployment-dir', str(root),
                    '--reference-scale', 'registered-100k', '--out', str(root/'out')])
            self.assertEqual(code, 2)
            verifier.verify.assert_called_once()
            provision.assert_not_called()
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
        self.assertIn('--through {source_snapshot}', result.stdout)
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
            driver.installation=SimpleNamespace(root=driver.root,runner=SimpleNamespace(policy=AutomationPolicy()))
            backup=mock.Mock()
            def quiesced(expected,ledger,observation,*,proof_directory_fd):
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


def time_limit():
    import time
    return time.monotonic()+30
