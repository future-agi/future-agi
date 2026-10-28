"""Daemon-free regression oracles. These fixtures are NOT product qualification."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.tests.test_update_automatic_release import default_v2, fixture, versioned
from deploy.tests.update_e2e import acceptance, real_automatic as real
from deploy.update.manifest import manifest_hash

ROOT = Path(__file__).resolve().parents[3]


class InstalledReleaseOracleTests(unittest.TestCase):
    def setUp(self):
        self.h = real.ProductHarness(Path.home()/'fagi-unit-oracle', ROOT, 'fagi-upd-e2e-real-unit')
        self.h.ports = {'BACKEND_PORT': 12345}
        self.manifest = versioned(default_v2(), 'v1.54.0')
        self.digest = self.manifest['components']['standalone']['digest']
        self.rows = [{'Image': name+'-id', 'State': {'Running': True, 'Health': {'Status':'healthy'}},
                      'Config': {'Labels': {'com.docker.compose.service': name}}}
                     for name in ('app','postgres','clickhouse')]
        self.receipt = dict(kind='apply',status='succeeded',target_version='v1.54.0',run_id='test-run',
                            manifest_hash=manifest_hash(self.manifest),target_image_ids={'app':'app-id'})
        self.migrations = dict(planned=[['app','0002']],applied=[['app','0002']],pending=[],
                               inventory_sha256='a'*64,applied_sha256='b'*64)
        self.evidence = dict(target_inventory_sha256='a'*64,target_applied_set_sha256='b'*64)
        self.image = {'RepoDigests':['docker.io/futureagi/standalone@'+self.digest]}

    def verify(self):
        with patch.object(self.h,'containers',return_value=self.rows), \
             patch.object(self.h,'docker',return_value=SimpleNamespace(stdout=json.dumps([self.image]).encode())), \
             patch.object(self.h,'migration_inventory',return_value=self.migrations), \
             patch.object(self.h,'request',return_value=(200,{})), patch.object(self.h,'record'):
            return self.h.verify_installed_release(self.manifest,self.receipt,self.evidence)

    def test_independent_target_observations_pass_for_each_successor(self):
        for version in ('v1.54.0','v1.55.0'):
            with self.subTest(version=version):
                self.manifest = versioned(self.manifest,version)
                self.receipt.update(target_version=version,manifest_hash=manifest_hash(self.manifest))
                self.assertEqual(self.verify(),self.migrations)

    def test_fabricated_receipt_and_advanced_baseline_cannot_hide_A_image(self):
        self.image['RepoDigests'] = ['docker.io/futureagi/standalone@sha256:'+'c'*64]
        with self.assertRaisesRegex(AssertionError,'signed target image'):
            self.verify()

    def test_receipt_must_bind_manifest_run_and_actual_image(self):
        for field,value in (('manifest_hash','c'*64),('target_image_ids',{'app':'old-A-id'}),
                            ('target_version','v1.53.0'),('kind','restore')):
            with self.subTest(field=field):
                old=copy.deepcopy(self.receipt)
                self.receipt[field]=value
                with self.assertRaisesRegex(AssertionError,'receipt'):
                    self.verify()
                self.receipt=old

    def test_complete_but_wrong_migration_graph_or_sources_cannot_pass(self):
        for field,value in (('inventory_sha256','c'*64),('applied_sha256','c'*64),
                            ('pending',[['app','0003']]),('applied',[['app','0002'],['stale','0001']])):
            with self.subTest(field=field):
                old=copy.deepcopy(self.migrations)
                self.migrations[field]=value
                with self.assertRaisesRegex(AssertionError,'migration inventory'):
                    self.verify()
                self.migrations=old

    def test_unhealthy_actual_target_cannot_pass_on_terminal_receipt(self):
        self.rows[0]['State']['Health']['Status']='unhealthy'
        with self.assertRaisesRegex(AssertionError,'not healthy'):
            self.verify()


class WitnessGenerationTests(unittest.TestCase):
    def test_B_created_records_join_A_inventory_and_must_survive_C(self):
        class Witnesses:
            def __init__(self,driver):
                self.namespaces={}
                self.records={}
            def create(self,label):
                self.namespaces[label]={}
                self.records[label]=[dict(domain=d,identity=label+'-'+d,payload_sha256='a'*64)
                    for d in ('postgres_app','clickhouse','object_store','temporal')]
                return copy.deepcopy(self.records[label])
            def read(self,label):
                return copy.deepcopy(self.records[label])
        h=real.ProductHarness(Path.home()/'fagi-unit-corpus',ROOT,'fagi-upd-e2e-real-unit')
        migrations=dict(planned=[['app','0001']],applied=[['app','0001']],pending=[])
        with patch('scripts.update_rehearsal_shipping.ProductWitnesses',Witnesses), \
             patch.object(h,'record') as record,patch.object(h,'migration_inventory',return_value=migrations):
            check_a=h.seed_and_check('under-a')
            check_b=h.seed_and_check('under-b')
            self.assertEqual(set(h._witness_generations),{'under-a','under-b'})
            self.assertEqual(set(record.call_args.args[1]['generations']),{'under-a','under-b'})
            check_a(); check_b()
            h._witnesses.records['under-b'][0]['payload_sha256']='c'*64
            # Even the original checker must inspect the expanded corpus.
            for check in (check_a,check_b):
                with self.assertRaisesRegex(AssertionError,'under-b'):
                    check()


class CandidateOnlyRestoreTests(unittest.TestCase):
    def test_real_S3_write_is_read_back_and_only_explicit_absence_passes(self):
        import base64
        h=real.ProductHarness(Path.home()/'fagi-unit-loss-window',ROOT,'fagi-upd-e2e-real-unit')
        def created(code,data):
            self.assertIn('put_object',code)
            return dict(identity=data['bucket']+'/'+data['key'],payload_sha256=hashlib.sha256(
                base64.b64decode(data['body'])).hexdigest())
        with patch.object(h,'app_python',side_effect=created),patch.object(h,'record'):
            witness=h.create_candidate_only_witness()
        self.assertEqual(witness['identity'],h._candidate_only['identity'])
        with patch.object(h,'record'),patch.object(h,'app_python',return_value=dict(identity=witness['identity'],absent=True)):
            h.verify_candidate_only_absent()
        with patch.object(h,'record'),patch.object(h,'app_python',return_value=dict(identity=witness['identity'],absent=False)):
            with self.assertRaisesRegex(AssertionError,'survived snapshot'):
                h.verify_candidate_only_absent()
        with patch.object(h,'app_python',side_effect=RuntimeError('S3 service unavailable')):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):
                h.verify_candidate_only_absent()

    def test_unconfirmed_candidate_write_cannot_be_used_as_loss_evidence(self):
        h=real.ProductHarness(Path.home()/'fagi-unit-loss-window',ROOT,'fagi-upd-e2e-real-unit')
        with patch.object(h,'app_python',return_value={'identity':'not-the-created-object','payload_sha256':'a'*64}), \
             patch.object(h,'record') as record:
            with self.assertRaisesRegex(AssertionError,'read back'):
                h.create_candidate_only_witness()
            self.assertFalse(hasattr(h,'_candidate_only'))
            record.assert_not_called()


class BudgetAndInventoryTests(unittest.TestCase):
    def test_enrollment_budget_is_valid_and_each_measured_timeout_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            *_,proof=fixture(Path(temp)/'evidence')
            policy=real.acceptance_policy(proof['predecessors'])
        self.assertEqual(policy.tick_timeout_seconds,policy.required_tick_budget_seconds)
        self.assertLess(policy.tick_timeout_seconds,21600)
        flags=real.policy_cli_arguments(policy)
        self.assertEqual(flags[flags.index('--tick-timeout-seconds')+1],str(policy.required_tick_budget_seconds))
        for key in ('backup','restore','migration','wait','pull','recovery-startup'):
            self.assertIn('--'+key+'-timeout-seconds',flags)
        self.assertEqual(policy.qualification_policy,'standalone-cold-restore-v1')
        self.assertEqual(policy.estimate_safety_factor,2)
        # Exercise the same configuration parser that originally refused the
        # acceptance flags, not just a separate budget arithmetic assertion.
        from deploy.update.enrollment import configuration
        values={flags[i][2:].replace('-','_'):int(flags[i+1]) for i in range(0,len(flags),2)}
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root=Path(temp)
            store=SimpleNamespace(checkout=root,path=root/'state',selection_hash='a'*64)
            options=SimpleNamespace(mode='apply',policy='minor',window='always',scheduler='cron',
                cron_service='cron.service',backup_destination=str(root/'backups'),check_interval=300,**values)
            config,_,_=configuration(store,options,real.CONTEXT)
            self.assertEqual(config.policy.tick_timeout_seconds,policy.tick_timeout_seconds)
            self.assertEqual(config.policy.required_tick_budget_seconds,policy.required_tick_budget_seconds)

    def test_real_installed_and_producer_executor_closures_match_and_bind_shipping_helper(self):
        from deploy.update.host_facts import executor_implementation_sha256
        from scripts.build_update_compatibility_proof import implementation_inventory,inventory_hash
        inventory=implementation_inventory(ROOT)
        self.assertIn('scripts/update_rehearsal_shipping.py',inventory)
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root=Path(temp)
            for name in inventory:
                target=root/name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/name,target)
            expected=inventory_hash(implementation_inventory(root))
            self.assertEqual(executor_implementation_sha256(root),expected)
            helper=root/'scripts/update_rehearsal_shipping.py'
            helper.write_text(helper.read_text()+'\n# changed test helper\n')
            self.assertNotEqual(executor_implementation_sha256(root),expected)
            self.assertEqual(executor_implementation_sha256(root),inventory_hash(implementation_inventory(root)))
            helper.unlink()
            with self.assertRaises((OSError,RuntimeError)):
                executor_implementation_sha256(root)

    def test_unit_CI_exports_real_cosign_from_SHA_pinned_verified_installer(self):
        workflow=(ROOT/'.github/workflows/update-ci.yml').read_text()
        unit=workflow.split('\n  unit:\n',1)[1].split('\n  compose:\n',1)[0]
        self.assertIn('uses: ./.github/actions/setup-helm-tools',unit)
        self.assertIn('fetch-depth: 0',unit,'historical data regressions need the release tags')
        self.assertIn('persist-credentials: false',unit)
        self.assertIn('FUTURE_AGI_UPDATE_TEST_COSIGN=%s',unit)
        self.assertLess(unit.index('FUTURE_AGI_UPDATE_TEST_COSIGN=%s'),unit.index('python -m unittest discover'))
        installer=(ROOT/'.github/actions/setup-helm-tools/action.yml').read_text()
        self.assertRegex(installer,r'sigstore/cosign-installer@[0-9a-f]{40}')
        self.assertRegex(installer,r'cosign-version:\n[^\n]*\n    default: v[3-9]\.')
        self.assertIn('cosign version',installer)


class TransitionRowTests(unittest.TestCase):
    def test_B_to_C_requires_the_exact_B_predecessor_on_retained_A_layout(self):
        from deploy.tests.update_e2e.automatic_publisher import AutomaticPublisher
        from deploy.update.compatibility import PROOF_ASSET
        from deploy.update.manifest import canonical_json
        with tempfile.TemporaryDirectory() as temp:
            a=versioned(default_v2(),'v1.53.0')
            *_,b,bproof=fixture(Path(temp)/'b',previous=a,version='v1.54.0')
            *_,c,cproof=fixture(Path(temp)/'c',previous=b,version='v1.55.0',execution=a['deployment'])
            publisher=AutomaticPublisher(Path(temp)/'publisher',lane='product',versions=tuple(m['version'] for m in (a,b,c)),
                retained_layout=a['version'],candidates={m['version']:m for m in (a,b,c)})
            publisher._prepared={a['version']:dict(manifest=a),
                b['version']:dict(manifest=b,assets={PROOF_ASSET:canonical_json(bproof)}),
                c['version']:dict(manifest=c,assets={PROOF_ASSET:canonical_json(cproof)})}
            row=publisher.transition(b['version'],c['version'])
            self.assertEqual(row['execution_deployment'],a['deployment'])
            self.assertEqual(row['predecessor']['manifest_hash'],manifest_hash(b))
            with self.assertRaisesRegex(RuntimeError,'exact qualified'):
                publisher.transition(a['version'],c['version'])


class HistoricalCandidateTests(unittest.TestCase):
    def test_vendor_backup_plan_is_daemon_free_and_uses_real_opt_in(self):
        result=subprocess.run([__import__('sys').executable,'-I',str(ROOT/'deploy/tests/update_e2e/backup_ci.py'),'--plan'],
            cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        plan=json.loads(result.stdout)
        self.assertFalse(plan['product_qualification'])
        self.assertEqual(plan['opt_in'],'FAGI_UPDATE_BACKUP_E2E=1')
        self.assertNotIn('-I',plan['command'])

    def test_missing_historical_update_assets_are_not_requested_or_fabricated(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root=Path(temp)
            retained=root/'retained'; retained.mkdir()
            (retained/'docker-compose.yml').write_text('historical release data fixture')
            driver=acceptance.NativeAcceptance(root)
            version='v1.53.0'
            metadata=dict(tag_name=version,draft=False,prerelease=False,published_at='2026-10-20T00:00:00Z')
            def registry(ref):
                return 'sha256:'+hashlib.sha256(ref.encode()).hexdigest()
            with patch.object(acceptance,'get_json',return_value=metadata) as get, \
                 patch.object(driver,'command',return_value=b'a'*40+b'\n'), \
                 patch.object(driver,'extract_deployment',return_value=retained), \
                 patch.object(driver,'registry_digest',side_effect=registry) as resolve, \
                 patch.object(driver,'historical_chart',return_value=dict(chart='oci://localhost:12345/futureagi/chart',
                    version='1.53.0',digest='sha256:'+'d'*64)) as chart:
                transport=object()
                manifest,directory=driver.published(version,transport)
            self.assertEqual(directory,retained)
            self.assertEqual(len(get.call_args_list),1)
            self.assertIn('/releases/tags/'+version,get.call_args.args[0])
            self.assertNotIn('/download/',get.call_args.args[0])
            self.assertFalse(manifest['compatibility']['automatic_apply'])
            self.assertIsNone(manifest['compatibility']['validation'])
            self.assertEqual(set(manifest['components']),set(acceptance.COMPONENT_REPOSITORIES))
            self.assertEqual(resolve.call_count,len(acceptance.COMPONENT_REPOSITORIES))
            chart.assert_called_once_with(version,'a'*40,transport)
            self.assertEqual(manifest['deployment']['files'],{'docker-compose.yml':hashlib.sha256(
                (retained/'docker-compose.yml').read_bytes()).hexdigest()})

    def test_historical_chart_is_real_tag_data_on_GET_only_transport(self):
        import io
        import tarfile
        from urllib.request import urlopen,Request
        from urllib.error import HTTPError
        from deploy.tests.update_e2e.oci_registry import OCIRegistry
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp, OCIRegistry() as registry:
            driver=acceptance.NativeAcceptance(Path(temp))
            sha=subprocess.check_output(['git','rev-parse','refs/tags/v1.53.0^{commit}'],cwd=ROOT).decode().strip()
            chart=driver.historical_chart('v1.53.0',sha,registry)
            self.assertTrue(chart['chart'].startswith('oci://localhost:'))
            repository=chart['chart'].split('/',3)[-1]
            base='http://'+registry.host+'/v2/'+repository
            with urlopen(base+'/manifests/1.53.0') as response:
                raw=response.read()
            self.assertEqual('sha256:'+hashlib.sha256(raw).hexdigest(),chart['digest'])
            layer=json.loads(raw)['layers'][0]
            with urlopen(base+'/blobs/'+layer['digest']) as response:
                payload=response.read()
            with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as package:
                data=package.extractfile('futureagi/Chart.yaml').read()
                self.assertEqual(data,subprocess.check_output(['git','show',sha+':deploy/helm/futureagi/Chart.yaml'],cwd=ROOT))
                self.assertFalse(any('/hack/' in m.name or '/ci/' in m.name for m in package))
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base+'/blobs/uploads/',data=b'x',method='POST'))
            self.assertEqual(error.exception.code,501)

    def test_registry_resolution_uses_the_shipping_workflow_JSON_descriptor_contract(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            digest='sha256:'+'a'*64
            with patch.object(driver,'command',return_value=json.dumps({'digest':digest}).encode()) as command:
                self.assertEqual(driver.registry_digest('docker.io/futureagi/standalone:v1.53.0'),digest)
            argv=command.call_args.args[0]
            self.assertEqual(argv[:4],['docker','--context',real.CONTEXT,'buildx'])
            self.assertEqual(argv[-2:],['--format','{{json .Manifest}}'])

    def test_absent_registry_descriptor_fails_without_making_a_candidate(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            with patch.object(driver,'command',return_value=b'<no value>\n'):
                with self.assertRaisesRegex(ValueError,'immutable descriptor'):
                    driver.registry_digest('docker.io/futureagi/standalone:v1.53.0')
            self.assertFalse(list(Path(temp).glob('test-candidate-*')))

    def test_extract_real_historical_deployment_is_data_only_and_tolerates_new_missing_paths(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            sha=subprocess.check_output(['git','rev-parse','refs/tags/v1.53.0^{commit}'],cwd=ROOT).decode().strip()
            retained=driver.extract_deployment('v1.53.0',sha,
                ['docker-compose.yml','agentcc-gateway/config.example.yaml','deploy/docker-compose.host-status.standalone.yml'])
            self.assertTrue((retained/'docker-compose.yml').is_file())
            self.assertTrue((retained/'agentcc-gateway/config.example.yaml').is_file())
            self.assertFalse((retained/'deploy/docker-compose.host-status.standalone.yml').exists())
            self.assertFalse((retained/'bin/update').exists())
            self.assertEqual((retained/'docker-compose.yml').read_bytes(),subprocess.check_output(
                ['git','show',sha+':docker-compose.yml'],cwd=ROOT))


if __name__ == '__main__':
    unittest.main()
