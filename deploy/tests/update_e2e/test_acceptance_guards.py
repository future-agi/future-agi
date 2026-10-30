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

    def test_registry_resolution_selects_native_leaf_not_first_leaf_or_index(self):
        media='application/vnd.oci.image.manifest.v1+json'
        arm=dict(mediaType=media,digest='sha256:'+'a'*64,platform={'os':'linux','architecture':'arm64'})
        native=dict(mediaType=media,digest='sha256:'+'b'*64,platform={'os':'linux','architecture':'amd64'})
        unknown=dict(mediaType=media,digest='sha256:'+'c'*64,platform={'os':'unknown','architecture':'unknown'})
        index=dict(schemaVersion=2,manifests=[arm,unknown,native])
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            with patch.object(driver,'command',return_value=json.dumps(index).encode()) as command:
                self.assertEqual(driver.registry_digest('docker.io/futureagi/standalone:v1.55.0'),native['digest'])
            self.assertEqual(command.call_args.args[0],['docker','--context',real.CONTEXT,'manifest','inspect',
                'docker.io/futureagi/standalone:v1.55.0'])
            observed=json.loads((Path(temp)/'platform-images.json').read_bytes())
            self.assertEqual(observed['docker.io/futureagi/standalone:v1.55.0']['descriptor'],native)
            for bad in ([arm], [native,native], [dict(native,mediaType='application/vnd.oci.image.index.v1+json')]):
                with self.subTest(bad=bad), patch.object(driver,'command',return_value=json.dumps({'manifests':bad}).encode()):
                    with self.assertRaisesRegex(ValueError,'native'):
                        driver.registry_digest('docker.io/futureagi/standalone:v1.55.0')

    def test_single_manifest_requires_real_verbose_native_descriptor(self):
        descriptor=dict(mediaType='application/vnd.docker.distribution.manifest.v2+json',
            digest='sha256:'+'b'*64,platform={'os':'linux','architecture':'amd64'})
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            with patch.object(driver,'command',side_effect=[b'{"schemaVersion":2}',
                    json.dumps({'Descriptor':descriptor}).encode()]) as command:
                self.assertEqual(driver.registry_digest('docker.io/futureagi/standalone:v1.55.0'),descriptor['digest'])
            self.assertIn('--verbose',command.call_args.args[0])

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


class AcceptanceSequenceTests(unittest.TestCase):
    def test_fixed_historical_graph_is_qualified_once_then_reused_for_fresh_scenarios(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        from deploy.tests import test_update_e2e_automatic as automatic
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            versions = ('v1.53.0', 'v1.54.0', 'v1.55.0')
            inputs = dict(mode='a-b-c', versions=versions, base=temp, candidates={v: {} for v in versions},
                          deployment_dirs={}, run_id=123, deadline_monotonic=9999999999)
            path = Path(temp)/'inputs.json'
            path.write_text(json.dumps(inputs))
            publisher = Mock()
            publisher.transition.side_effect = ['measured-A-B', 'measured-B-C']
            harness = Mock()
            harness.automatic_publisher.return_value = nullcontext(publisher)
            case = automatic.AutomaticLifecycleE2E('test_one_opt_in_scheduled_A_B_C')
            with patch.dict(os.environ, {'FAGI_UPDATE_ACCEPTANCE_INPUTS': str(path)}), \
                 patch.object(automatic.e2e, 'Harness', return_value=harness), \
                 patch.object(automatic, 'acceptance_policy', return_value='measured-policy') as policy, \
                 patch.object(case, 'exercise') as exercise:
                case.test_one_opt_in_scheduled_A_B_C()
            self.assertEqual([c.args for c in publisher.prepare.call_args_list], [(v,) for v in versions])
            self.assertEqual(publisher.hide_releases.call_count, 1)
            self.assertEqual([c.args[1] for c in exercise.call_args_list], [versions])
            self.assertEqual([c.kwargs['restore'] for c in exercise.call_args_list], [False])
            self.assertEqual(harness.automatic_publisher.call_args.kwargs['deadline'],inputs['deadline_monotonic'])
            self.assertEqual(harness.automatic_publisher.call_args.kwargs['acceptance_reserve_seconds'],
                             real.acceptance_reserve_seconds('a-b-c'))
            policy.assert_called_once_with(['measured-A-B', 'measured-B-C'])

    def test_actual_forward_acceptance_checks_B_then_C_and_preserves_B_generation(self):
        """Finite orchestration fixture only, not product qualification evidence."""
        self._exercise_forward_fixture()
        with self.assertRaisesRegex(AssertionError, 'signed target image'):
            self._exercise_forward_fixture(leave_running_A=True)

    def _exercise_forward_fixture(self, *, leave_running_A=False):
        import time
        from unittest.mock import Mock
        from deploy.tests import test_update_e2e_automatic as automatic
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp)
            versions = ('v1.53.0', 'v1.54.0', 'v1.55.0')
            manifests, inventories, rows = {}, {}, {}
            for index, version in enumerate(versions, 1):
                manifest = versioned(default_v2(), version)
                manifest['components']['standalone']['digest'] = 'sha256:'+str(index)*64
                manifests[version] = manifest
                count = 1 if index < 3 else 2
                names = [['app', '000'+str(n)] for n in range(1, count+1)]
                inventories[version] = dict(planned=names, applied=names, pending=[],
                    inventory_sha256=str(index)*64, applied_sha256=str(index+3)*64)
                if index > 1:
                    before = inventories[versions[index-2]]
                    rows[version] = dict(migration_evidence=dict(source_inventory_sha256=before['inventory_sha256'],
                        source_applied_set_sha256=before['applied_sha256'], target_inventory_sha256=str(index)*64,
                        target_applied_set_sha256=str(index+3)*64, target_pending_plan_count=0 if index == 2 else 1),
                        migration_measurements=[] if index == 2 else [dict(app_label='app', name='0002')])
            current = [versions[0]]
            receipts, attempts = [], {}
            state_path = root/'state'
            (state_path/'attempts').mkdir(parents=True)
            h = real.ProductHarness(root, ROOT, 'fagi-upd-e2e-real-unit')
            h.ports = {'BACKEND_PORT': 1234}
            h._witness_generations = {}
            h.corpus = {}
            verified_generations = []
            def seed(generation='acceptance-a'):
                h._witness_generations[generation] = []
                def verify():
                    verified_generations.append((current[0], set(h._witness_generations)))
                verify()
                return verify
            def containers():
                return [dict(Image='app-'+(versions[0] if leave_running_A else current[0]) if name == 'app' else name+'-id',
                    State={'Running': True, 'Health': {'Status': 'healthy'}},
                    Config={'Labels': {'com.docker.compose.service': name}}) for name in ('app', 'postgres', 'clickhouse')]
            def image(*args, **kwargs):
                version = versions[0] if leave_running_A else current[0]
                return SimpleNamespace(stdout=json.dumps([{'RepoDigests': ['docker.io/futureagi/standalone@'+
                    manifests[version]['components']['standalone']['digest']]}]).encode())
            def publish(version):
                current[0] = version
                if version == versions[0]:
                    return
                attempt = 'attempt-'+version
                proof = root/'backups'/attempt/'proof.json'
                proof.parent.mkdir(parents=True)
                proof.write_text('synthetic component backup identity')
                (state_path/'attempts'/(attempt+'.json')).write_text('{}')
                attempts[attempt] = SimpleNamespace(attempt_id=attempt, target=SimpleNamespace(version=version), outcome='succeeded',
                    backup_proof_identity='a'*64)
                receipts.append(dict(kind='apply', status='succeeded', target_version=version, run_id='run-'+version,
                    manifest_hash=manifest_hash(manifests[version]), target_image_ids={'app': 'app-'+version}))
            h.install, h.record, h.collect_logs, h.collect_diagnostics, h.cleanup = (Mock() for _ in range(5))
            h.install.return_value = {'seconds': 280}
            h.bin_update = Mock(return_value=SimpleNamespace(stdout=automatic.AUTOMATIC_UPDATE_DISCLOSURE.encode()))
            h.containers, h.docker, h.seed_and_check = containers, image, seed
            h.migration_inventory = lambda: inventories[current[0]]
            h.request = lambda *a, **kw: (200, {})
            h.receipts = lambda: receipts
            publisher = Mock()
            publisher.publish.side_effect = publish
            publisher.new_checkout.return_value = h
            publisher.seed_and_check.side_effect = lambda checkout: checkout.seed_and_check()
            publisher._prepared = {v: {'manifest': m} for v, m in manifests.items()}
            store = Mock(path=state_path)
            store.read_registration.return_value = 'unchanged-registration'
            store.read_state.return_value = SimpleNamespace(active_attempt_id=None)
            store.read_attempt.side_effect = lambda key: attempts[key]
            baseline = Mock()
            baseline.current.side_effect = lambda: SimpleNamespace(release=SimpleNamespace(version=current[0]))
            config = SimpleNamespace(policy='unchanged-policy', scheduler_options={})
            case = automatic.AutomaticLifecycleE2E('test_one_opt_in_scheduled_A_B_C')
            with patch.object(automatic, 'selected_store', return_value=store), \
                 patch.object(automatic, 'BaselineStore', return_value=baseline), \
                 patch.object(automatic, 'load_setup_configuration', return_value=config), \
                 patch.object(automatic, 'inspect_activation', return_value=SimpleNamespace(active=True)), \
                 patch.object(automatic, 'read_attempt_ledger', side_effect=lambda path: (
                     {'run_id': 'run-'+current[0], 'direction': 'success_decided'}, {'success-decision.json'})), \
                 patch.object(automatic, 'acceptance_policy', return_value=SimpleNamespace(
                     tick_timeout_seconds=1200, check_interval_seconds=300, required_tick_budget_seconds=1200)), \
                 patch.object(automatic, 'policy_cli_arguments', return_value=()), \
                 patch.object(h, 'verify_installed_release', wraps=h.verify_installed_release) as installed:
                case.exercise(dict(mode='a-b-c', deadline_monotonic=time.monotonic()+100000), versions, publisher, rows,
                    SimpleNamespace(tick_timeout_seconds=1200, check_interval_seconds=300, required_tick_budget_seconds=1200),
                    restore=False)
            self.assertEqual([c.args[0]['version'] for c in installed.call_args_list], list(versions[1:]))
            self.assertIn((versions[2], {'acceptance-a', 'acceptance-'+versions[1]}), verified_generations)
            self.assertEqual(h.bin_update.call_count, 2, 'exactly one setup plus deactivation, no driven ticks')
            h.cleanup.assert_called_once()

    def test_ledger_oracle_never_runs_recovery_or_reconciles_a_stale_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            directory = Path(temp)/('a'*32)
            directory.mkdir(mode=0o700)
            checkpoint = dict(schema_version=2, attempt_id=directory.name, direction='forward_pending')
            for name, value in (('recovery.json', checkpoint), ('success-decision.json', {'immutable': 'decision'})):
                path = directory/name
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            with patch('deploy.update.recovery.RecoveryLedger.open', side_effect=AssertionError('test must not recover')):
                data, leaves = real.read_attempt_ledger(directory)
            self.assertEqual(data, checkpoint)
            self.assertIn('success-decision.json', leaves)
            self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})

    def test_forward_fault_must_still_belong_to_the_exact_pre_success_transaction(self):
        from unittest.mock import Mock
        h = real.ProductHarness(Path.home()/'fagi-unit-fault', ROOT, 'fagi-upd-e2e-real-unit')
        manifest = versioned(default_v2(), 'v1.54.0')
        attempt = SimpleNamespace(attempt_id='a'*32, operation_token='b'*32,
                                  target=SimpleNamespace(manifest_hash=manifest_hash(manifest)))
        store = Mock()
        store.read_state.return_value = SimpleNamespace(active_attempt_id=attempt.attempt_id)
        store.read_attempt.return_value = attempt
        ledger = dict(direction='forward_pending', run_id='run', operation_token=attempt.operation_token,
                      expectation={'target_manifest_hash': manifest_hash(manifest)})
        leaves = set()
        with patch('deploy.update.host_facts.selected_store', return_value=store), \
             patch('deploy.update.enrollment.load_setup_configuration', return_value=SimpleNamespace(backup_destination=Path.home()/'backups')), \
             patch.object(real, 'read_attempt_ledger', return_value=(ledger, leaves)):
            self.assertEqual(h.pending_forward_attempt(manifest), 'run')
            for direction in ('success_decided', 'restore_decided'):
                ledger['direction'] = direction
                with self.assertRaisesRegex(AssertionError, 'exact pending target'):
                    h.pending_forward_attempt(manifest)
            ledger['direction'] = 'forward_pending'
            for decision in ('success-decision.json', 'restore-decision.json'):
                leaves.add(decision)
                with self.assertRaisesRegex(AssertionError, 'exact pending target'):
                    h.pending_forward_attempt(manifest)
                leaves.clear()
            ledger['operation_token'] = 'wrong-attempt'
            with self.assertRaisesRegex(AssertionError, 'exact pending target'):
                h.pending_forward_attempt(manifest)
            store.read_state.return_value.active_attempt_id = None
            with self.assertRaisesRegex(AssertionError, 'no active pre-success'):
                h.pending_forward_attempt(manifest)


class NativeFixturePermissionTests(unittest.TestCase):
    def test_real_retained_product_binds_are_native_readable_without_changing_release_bytes(self):
        from deploy.update import standalone_backup as backup
        from deploy.update.manifest import hash_path
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp)
            source = root/'source'
            source.mkdir(mode=0o700)
            for name in backup.CONFIG_FILES[1:]:
                path = source/name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(ROOT/name, path)
                path.chmod(0o600)  # the historical-data extractor's private modes
            manifest = default_v2()
            manifest['deployment'] = {'files': {name: hash_path(source, name) for name in backup.CONFIG_FILES[1:]}}
            evidence = root/'evidence'
            evidence.mkdir(mode=0o700)
            h = real.ProductHarness(evidence, ROOT, 'fagi-upd-e2e-real-unit')
            h.retain_deployment(source, manifest)
            for name in backup.BINDS['clickhouse'].values():
                self.assertEqual((h.root/name).stat().st_mode & 0o777, 0o644)
            self.assertFalse((h.root/'.env').exists(), 'retention must never copy source credentials')
            self.assertEqual(evidence.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(hash_path(h.root, name) == digest
                                for name, digest in manifest['deployment']['files'].items()))

    def test_shipped_bind_bytes_are_container_readable_but_env_and_evidence_stay_private(self):
        from deploy.tests.test_update_e2e_backup import prepare_fixture_configuration
        from deploy.update import standalone_backup as backup
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp)
            prepare_fixture_configuration(root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            for name in backup.CONFIG_FILES:
                path = root/name
                self.assertEqual(path.stat().st_mode & 0o777, 0o600 if name == '.env' else 0o644)
                if name not in ('.env', 'docker-compose.yml'):
                    self.assertEqual(path.read_bytes(), (ROOT/name).read_bytes())
            # The shipping configuration guard still authenticates every byte;
            # no permissive backup implementation or root vendor user is needed.
            self.assertEqual(set(backup._files(root)), set(backup.CONFIG_FILES))


class CIDiagnosticTests(unittest.TestCase):
    def test_failed_native_build_retains_bounded_redacted_errors_not_private_argv(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp, \
             patch.dict(os.environ, {'EXAMPLE_API_KEY': 'generated-secret-for-test'}):
            driver = acceptance.NativeAcceptance(Path(temp))
            def fail(argv, **kwargs):
                kwargs['stderr'].write(b'ERROR build failed generated-secret-for-test Bearer abc.def.secret\n')
                return subprocess.CompletedProcess(argv, 7, b'private stdout not for upload')
            with patch.object(acceptance.subprocess, 'run', side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, 'command 1 failed'):
                    driver.command(['docker', '--context', real.CONTEXT, 'buildx', 'build', '--secret', 'private-argv'])
            path = Path(temp)/'command-diagnostics.json'
            data = json.loads(path.read_bytes())
            row = data['commands'][0]
            self.assertEqual((row['operation'], row['exit_code']), ('build', 7))
            self.assertIn('ERROR build failed', row['stderr_tail'])
            self.assertNotIn('generated-secret-for-test', path.read_text())
            self.assertNotIn('abc.def.secret', path.read_text())
            self.assertNotIn('private-argv', path.read_text())
            self.assertNotIn('private stdout', path.read_text())
            self.assertFalse(data['qualification_evidence'])

    def test_product_diagnostics_are_bounded_read_only_owned_and_scrub_generated_credentials(self):
        h = real.ProductHarness(Path.home()/'fagi-unit-diagnostics', ROOT, 'fagi-upd-e2e-real-unit')
        h._secret_values.add('actual-generated-secret')
        cid = 'a'*64
        row = dict(Config={'Env': ['SECRET_KEY=actual-generated-secret'], 'Labels': {
            'com.docker.compose.project': h.project, 'com.docker.compose.service': 'app'}},
            State=dict(Status='running', Health={'Status': 'unhealthy'}, ExitCode=0, OOMKilled=False), RestartCount=2)
        def read(argv, **kwargs):
            command = argv[3:]
            if command[0] == 'ps':
                stdout = cid.encode()
            elif command[0] == 'inspect':
                stdout = json.dumps([row]).encode()
            else:
                self.assertEqual(command[:2], ['logs', '--timestamps'])
                return subprocess.CompletedProcess(argv, 0, b'failure actual-generated-secret', b'Bearer api.token.value')
            return subprocess.CompletedProcess(argv, 0, stdout, b'')
        with patch('deploy.update.processes.ProcessSupervisor') as supervisor, patch.object(h, 'record') as record:
            supervisor.return_value.side_effect = read
            data = h.collect_diagnostics(RuntimeError('must not upload arbitrary exception text'))
        encoded = json.dumps(data)
        self.assertNotIn('actual-generated-secret', encoded)
        self.assertNotIn('api.token.value', encoded)
        self.assertNotIn('SECRET_KEY', encoded)
        self.assertNotIn('arbitrary exception', encoded)
        self.assertEqual(data['services']['app']['restart_count'], 2)
        self.assertEqual(data['exception_type'], 'RuntimeError')
        self.assertFalse(data['qualification_evidence'])
        self.assertEqual(supervisor.call_args.kwargs['output_limit'], 65536)
        self.assertEqual(record.call_args.args[0], 'acceptance-diagnostics.json')

    def test_artifact_paths_are_an_explicit_safe_diagnostic_allowlist(self):
        text = (ROOT/'.github/workflows/update-ci.yml').read_text()
        for mode in ('upgrade', 'abc'):
            paths = [line.strip() for line in text.splitlines() if line.strip().startswith('~/fagi-upd-e2e-real-'+mode)]
            self.assertTrue(any('/qualification/diagnostics.json' in path for path in paths))
            self.assertTrue(any('/installed-v*.json' in path for path in paths))
            for path in paths:
                self.assertFalse(path.endswith(('/', '**', '*', '.env', '.key', '.tar', '.private.json')), path)
                self.assertNotIn('witness-', path)
        unit = text.split('\n  unit:\n', 1)[1].split('\n  compose:\n', 1)[0]
        self.assertIn('timeout-minutes: 30', unit)


class RestoreSequenceTests(unittest.TestCase):
    """Restore branch of exercise() against REAL runner/contract record shapes.

    Finite orchestration fixture only, not product qualification evidence. The
    scheduler is simulated with the exact records the shipped restore path
    writes (apply.py restore receipt, baseline.commit_restore latch/attempt,
    runner._finish event), never a synthetic acceptance shortcut.
    """

    def _exercise_restore_fixture(self, *, latch_event=True, extra_proof=False, latch=True):
        import time
        from datetime import datetime, timedelta, timezone
        from unittest.mock import Mock
        from deploy.tests import test_update_e2e_automatic as automatic
        from deploy.tests.update_e2e import harness as e2e
        from deploy.update.contracts import InstallationBinding, OperationEvent, ReasonCode
        from deploy.update.runner_state import AttemptRecord, EventRecord, RunnerState, RunnerStateStore, TargetIdentity
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp)
            versions = ('v1.55.0', 'v1.55.1')
            manifests, inventories = {}, {}
            for index, version in enumerate(versions, 1):
                manifest = versioned(default_v2(), version)
                manifest['components']['standalone']['digest'] = 'sha256:'+str(index)*64
                manifests[version] = manifest
                names = [['app', '000'+str(n)] for n in range(1, index+1)]
                inventories[version] = dict(planned=names, applied=names, pending=[],
                    inventory_sha256=str(index)*64, applied_sha256=str(index+3)*64)
            rows = {versions[1]: dict(migration_evidence=dict(
                source_inventory_sha256=inventories[versions[0]]['inventory_sha256'],
                source_applied_set_sha256=inventories[versions[0]]['applied_sha256'],
                target_inventory_sha256=inventories[versions[1]]['inventory_sha256'],
                target_applied_set_sha256=inventories[versions[1]]['applied_sha256'], target_pending_plan_count=1),
                migration_measurements=[dict(app_label='app', name='0002')])}
            target = TargetIdentity(versions[1], manifest_hash(manifests[versions[1]]))
            binding = InstallationBinding('1'*32, 'a'*64, '2'*64, root, root/'.env', root/'.env', 'standalone',
                False, 'fagi-upd-e2e-real-unit', (root/'docker-compose.yml',), (), real.CONTEXT, 'daemon', os.geteuid())
            state_path = root/'state'
            (state_path/'attempts').mkdir(parents=True)
            state, receipts, attempts = [RunnerState()], [], {}
            reserved = datetime(2026, 10, 29, 22, tzinfo=timezone.utc)
            finished = reserved+timedelta(minutes=14)
            h = real.ProductHarness(root, ROOT, 'fagi-upd-e2e-real-unit')
            h.ports = {'BACKEND_PORT': 1234}
            h._witness_generations = {}
            h.corpus = {'witness_gaps': [{'domain': 'shared_files'}]}
            verified = []
            def seed(generation='acceptance-a'):
                h._witness_generations[generation] = [{'domain': 'postgres_app'}]
                def verify():
                    verified.append(sorted(h._witness_generations))
                verify()
                return verify
            def containers():
                return [dict(Image=name+'-'+versions[0], State={'Running': True, 'Health': {'Status': 'healthy'}},
                             Config={'Labels': {'com.docker.compose.service': name}})
                        for name in ('app', 'postgres', 'clickhouse')]
            def scheduler_restores(manifest, *, timeout):
                # What ONE installed-scheduler forward tick leaves after the
                # target migrated, then failed, then was restored.
                self.assertEqual(manifest['version'], versions[1])
                self.assertGreater(timeout, 0)
                attempt = AttemptRecord(1, 'b'*32, 'c'*32, binding, target, 'always', reserved, 'restored',
                                        finished, 'd'*64, 'e'*64)
                attempts[attempt.attempt_id] = attempt
                (state_path/'attempts'/(attempt.attempt_id+'.json')).write_text('{}')
                for number in range(2 if extra_proof else 1):
                    proof = root/'backups'/(attempt.attempt_id+str(number or ''))/'proof.json'
                    proof.parent.mkdir(parents=True)
                    proof.write_text('component backup identity')
                receipts.append(dict(kind='apply', status='failed_during_rollout', target_version=versions[1],
                                     run_id='run-B', from_version=versions[0]))
                receipts.append(dict(kind='restore', status='restored', stage='rollout', run_id='run-B',
                                     from_version=versions[0], target_version=versions[1],
                                     manifest_hash=target.manifest_hash, target_image_ids={'app': 'app-'+versions[0]}))
                state[0] = RunnerState(failed_target_latch=target if latch else None)
                leaves = [EventRecord(1, 0, OperationEvent('restored', 'restore', 'rollout', finished, 'c'*32,
                    (ReasonCode.RESTORED_AFTER_UPDATE_FAILURE,)), target)]
                if latch_event:
                    # runner._finish after the next due/not-due tick refuses B.
                    leaves.append(EventRecord(1, 1, OperationEvent('blocked', 'check', None,
                        finished+timedelta(minutes=5), 'f'*32, (ReasonCode.FAILED_TARGET_LATCHED,)), target))
                # Written by the store's own atomic writer, read back lock-free.
                (state_path/'events').mkdir(mode=0o700)
                fd = os.open(str(state_path/'events'), os.O_RDONLY)
                try:
                    for record in leaves:
                        RunnerStateStore._write(fd, '%020d.json' % record.sequence, record)
                finally:
                    os.close(fd)
                return inventories[versions[1]]
            h.install, h.record, h.collect_logs, h.collect_diagnostics, h.cleanup = (Mock() for _ in range(5))
            h.install.return_value = {'seconds': 280}
            h.verify_candidate_only_absent = Mock()
            h.fail_after_migrations = Mock(side_effect=scheduler_restores)
            h.bin_update = Mock(return_value=SimpleNamespace(stdout=automatic.AUTOMATIC_UPDATE_DISCLOSURE.encode()))
            h.containers, h.seed_and_check = containers, seed
            h.migration_inventory = lambda: inventories[versions[0]]
            h.receipts = lambda: receipts
            publisher = Mock()
            publisher.new_checkout.return_value = h
            publisher.seed_and_check.side_effect = lambda checkout: checkout.seed_and_check()
            publisher._prepared = {v: {'manifest': m} for v, m in manifests.items()}
            store = Mock(path=state_path)
            store.read_registration.return_value = 'unchanged-registration'
            store.read_state.side_effect = lambda: state[0]
            store.read_events.side_effect = AssertionError('state.lock reader would contend with the updater')
            store.read_attempt.side_effect = lambda key: attempts[key]
            source_head = SimpleNamespace(release=SimpleNamespace(version=versions[0], manifest_hash='a'*64))
            baseline = Mock()
            baseline.current.return_value = source_head
            config = SimpleNamespace(policy='unchanged-policy', scheduler_options={})
            policy = SimpleNamespace(tick_timeout_seconds=1200, check_interval_seconds=300,
                required_tick_budget_seconds=1200, probe_timeout_seconds=30, stability_period_seconds=180)
            wait = e2e.wait_for
            case = automatic.AutomaticLifecycleE2E('test_one_opt_in_scheduled_A_B_C')
            with patch.object(automatic, 'selected_store', return_value=store), \
                 patch.object(automatic, 'BaselineStore', return_value=baseline), \
                 patch.object(automatic, 'load_setup_configuration', return_value=config), \
                 patch.object(automatic, 'inspect_activation', return_value=SimpleNamespace(active=True)), \
                 patch.object(automatic, 'read_attempt_ledger', return_value=(
                     {'run_id': 'run-B', 'direction': 'restore_decided'}, {'restore-decision.json', 'recovery.json'})), \
                 patch.object(automatic, 'acceptance_policy', return_value=policy), \
                 patch.object(automatic, 'policy_cli_arguments', return_value=()), \
                 patch.object(automatic.e2e, 'wait_for', side_effect=lambda predicate, *, timeout, interval, what:
                              wait(predicate, timeout=min(timeout, .2), interval=.01, what=what)):
                case.exercise(dict(mode='upgrade', deadline_monotonic=time.monotonic()+100000), versions,
                              publisher, rows, policy, restore=True)
            self.assertEqual(h.fail_after_migrations.call_count, 1)
            h.verify_candidate_only_absent.assert_called_once_with()
            self.assertEqual(h.bin_update.call_count, 2, 'exactly one setup plus deactivation, no driven ticks')
            self.assertEqual(verified[-1], ['acceptance-a'])
            recorded = {c.args[0]: c.args[1] for c in h.record.call_args_list}
            restored = recorded['restored-source.json']
            self.assertEqual((restored['source_version'], restored['failed_target_version']), versions)
            self.assertEqual(restored['fault_seam'], real.FAULT_SEAM)
            self.assertTrue(restored['failed_target_latched'] and restored['subsequent_real_cron_tick'])
            result = recorded['acceptance-result.json']
            self.assertEqual((result['scenario'], result['automatic_runs'], result['backups']), ('restore', 1, 1))
            self.assertEqual(result['witness_gaps'], [{'domain': 'shared_files'}])
            h.cleanup.assert_called_once()

    def test_restore_branch_accepts_only_the_real_restore_records_and_a_later_latched_cron_wake(self):
        self._exercise_restore_fixture()

    def test_restore_without_a_later_latched_wake_is_not_accepted(self):
        with self.assertRaisesRegex(AssertionError, 'blocked by restored target latch'):
            self._exercise_restore_fixture(latch_event=False)

    def test_restore_without_the_failed_target_latch_is_not_accepted(self):
        with self.assertRaises(AssertionError):
            self._exercise_restore_fixture(latch=False)

    def test_event_oracle_reads_atomic_leaves_without_taking_state_lock(self):
        import fcntl
        from datetime import datetime, timezone
        from deploy.update.contracts import OperationEvent, ReasonCode
        from deploy.update.runner_state import EventRecord, RunnerStateStore, StoreError, TargetIdentity
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            store = SimpleNamespace(path=Path(temp))
            self.assertEqual(real.read_event_records(store), ())
            events = Path(temp)/'events'; events.mkdir(mode=0o700)
            record = EventRecord(1, 7, OperationEvent('blocked', 'check', None, datetime(2026, 10, 29, tzinfo=timezone.utc),
                'f'*32, (ReasonCode.FAILED_TARGET_LATCHED,)), TargetIdentity('v1.55.1', 'a'*64))
            fd = os.open(str(events), os.O_RDONLY)
            try:
                RunnerStateStore._write(fd, '%020d.json' % 7, record)
            finally:
                os.close(fd)
            (events/'.00000000000000000008.json.0123.tmp').write_text('partial')
            lock = os.open(str(Path(temp)/'state.lock'), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                # The real updater holds state.lock: the oracle must still read.
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(real.read_event_records(store), (record,))
            finally:
                os.close(lock)
            os.rename(events/('%020d.json' % 7), events/('%020d.json' % 9))
            with self.assertRaisesRegex(AssertionError, 'identity differs'):
                real.read_event_records(store)
            (events/('%020d.json' % 9)).write_text('{"schema_version":1,"sequence":9,"sequence":9}')
            with self.assertRaises(StoreError):
                real.read_event_records(store)

    def test_restore_must_take_exactly_one_cold_backup(self):
        with self.assertRaisesRegex(AssertionError, 'exactly one cold backup'):
            self._exercise_restore_fixture(extra_proof=True)


class NativeBoundaryTests(unittest.TestCase):
    def test_source_prepare_is_installer_shaped_and_real_normalization_accepts_it(self):
        from deploy.update import enrollment as en
        from deploy.update.envfile import read_env
        from deploy.update.runner_state import RunnerStateStore
        from deploy.tests.update_e2e.test_real_automatic_harness import model
        manifest=versioned(default_v2(),'v1.53.0')
        component=manifest['components']['standalone']
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root=Path(temp)
            h=real.ProductHarness(root,ROOT,'fagi-upd-e2e-real-unit')
            h.root.mkdir(mode=0o700)
            (h.root/'docker-compose.yml').write_text('retained data fixture, no daemon')
            resolved=model(h.project)
            resolved['services']['app']['image']=component['image']
            with patch.object(h,'install_updater'),patch.object(h,'docker',return_value=SimpleNamespace(stdout=b'')), \
                 patch.object(h,'dc',return_value=SimpleNamespace(stdout=json.dumps(resolved).encode())),patch.object(h,'record'):
                h.prepare(component['digest'],version=manifest['version'])
            env=read_env(h.root/'.env')
            self.assertEqual(env['FUTURE_AGI_VERSION'],manifest['version'])
            store=RunnerStateStore(h.root,h.root/'.env')
            image_id='sha256:'+'d'*64
            installation=SimpleNamespace(env=env,env_file=h.root/'.env',
                futureagi_services=lambda:[dict(service='app',component='standalone',image=component['image'],buildable=False)],
                containers=lambda:[dict(service='app',status='running',image_id=image_id)],
                image=lambda ref:dict(id=image_id,repo_digests=[component['image'].split(':v')[0]+'@'+component['digest']]))
            before=installation.env_file.read_bytes()
            fingerprint=hashlib.sha256(before).hexdigest()
            release=SimpleNamespace(version=manifest['version'],manifest=manifest,manifest_sha256=manifest_hash(manifest))
            self.assertEqual(en.normalize_pins(store,installation,release,
                render_config=lambda installation,raw:hashlib.sha256(raw).hexdigest()),fingerprint)
            self.assertEqual(installation.env_file.read_bytes(),before,'setup must not undo or change source installation data')

    def test_source_install_pulls_native_pin_then_aliases_without_plain_app_pull(self):
        manifest=versioned(default_v2(),'v1.53.0')
        component=manifest['components']['standalone']
        h=real.ProductHarness(Path.home()/'fagi-unit-source-install',ROOT,'fagi-upd-e2e-real-unit')
        h.source_manifest=manifest
        h.model={'services':{'app':{'image':component['image']},'postgres':{'image':'postgres:17'},
                            'clickhouse':{'image':'clickhouse/clickhouse-server:24.8'}}}
        image=dict(Id='sha256:'+'d'*64,Os='linux',Architecture='amd64',
                   RepoDigests=[component['image'].split(':v')[0]+'@'+component['digest']])
        def docker(*args,**kwargs):
            return SimpleNamespace(stdout=json.dumps([image]).encode() if args[:2] == ('image','inspect') else b'')
        with patch.object(h,'prepare'),patch.object(h,'docker',side_effect=docker) as command, \
             patch.object(h,'boot_source',return_value={'seconds':280}):
            self.assertEqual(h.install(),{'seconds':280})
        calls=[c.args for c in command.call_args_list]
        pinned=component['image']+'@'+component['digest']
        self.assertEqual(calls[0],('pull','--platform','linux/amd64',pinned))
        self.assertIn(('image','tag',pinned,component['image']),calls)
        self.assertNotIn(('pull',component['image']),calls)
        self.assertEqual(sum(c[0] == 'pull' for c in calls),3)
        image['Architecture']='arm64'
        with patch.object(h,'prepare'),patch.object(h,'docker',side_effect=docker) as command,patch.object(h,'boot_source') as boot:
            with self.assertRaisesRegex(AssertionError,'frozen native RepoDigest'):
                h.install()
            boot.assert_not_called()
            self.assertFalse(any(c.args[:2] == ('image','tag') for c in command.call_args_list))

    def test_measured_source_boot_sizes_bounded_waits_with_real_policy_and_clock_rules(self):
        from datetime import datetime,timezone
        from deploy.update.schedule import successful_check_deadline
        with tempfile.TemporaryDirectory() as temp:
            *_,proof=fixture(Path(temp)/'measurements')
            policy=real.acceptance_policy(proof['predecessors'],source_boot_seconds=280)
            self.assertEqual(policy.wait_timeout_seconds,620)
            self.assertEqual(policy.recovery_startup_timeout_seconds,620)
            self.assertEqual(policy.pull_timeout_seconds,300)
            self.assertGreaterEqual(policy.stability_period_seconds,180)
            self.assertEqual(policy.required_tick_budget_seconds,policy.tick_timeout_seconds)
            required=real.scheduler_wait_budget(policy,100000,now=1)
            self.assertEqual(required,policy.tick_timeout_seconds+660)
            with self.assertRaisesRegex(TimeoutError,'remaining native job time'):
                real.scheduler_wait_budget(policy,required,now=1)
            start=datetime(2026,10,29,tzinfo=timezone.utc)
            # Positive-only production jitter can defer a nominal five-minute
            # check to the SECOND cron delivery. Cover the real due function.
            due=successful_check_deadline(policy,start,'native-acceptance')
            self.assertLessEqual((due-start).total_seconds(),330)
            self.assertLess(2*real.CRON_PERIOD_SECONDS+60,required)
            for value in (0,-1,float('nan'),float('inf'),True,4201):
                with self.subTest(value=value),self.assertRaisesRegex(ValueError,'source boot measurement'):
                    real.acceptance_policy(proof['predecessors'],source_boot_seconds=value)

    def test_measured_source_boot_clamps_at_startup_policy_boundary(self):
        from deploy.update.contracts import AUTOMATIC_POLICY_BOUNDS, POLICY_BOUNDS
        startup_limit=min(POLICY_BOUNDS['wait_timeout_seconds'][1],
                          AUTOMATIC_POLICY_BOUNDS['recovery_startup_timeout_seconds'][1])
        source_at_limit=(startup_limit-60)/2
        with tempfile.TemporaryDirectory() as temp:
            *_,proof=fixture(Path(temp)/'measurements')
            for source_boot_seconds in (source_at_limit,source_at_limit+.5):
                with self.subTest(source_boot_seconds=source_boot_seconds):
                    policy=real.acceptance_policy(proof['predecessors'],source_boot_seconds=source_boot_seconds)
                    self.assertEqual(policy.wait_timeout_seconds,startup_limit)
                    self.assertEqual(policy.recovery_startup_timeout_seconds,startup_limit)

    def test_actual_cron_install_inspect_and_remove_use_real_renderer_with_fake_OS_only(self):
        from deploy.tests.test_update_scheduler_install import ActivationTests
        from deploy.update import scheduler_install as si
        case=ActivationTests('test_renderer_and_enable_flag_are_not_activation')
        case.setUp()
        try:
            before=case.manager.tab
            result=case.call(si.install_scheduler,backend='cron',cron_service='cron.service')
            self.assertTrue(result.active,result)
            tab=case.manager.tab.decode('utf-8',errors='replace')
            owned=[line for line in tab.splitlines() if line.startswith('*/5 ')]
            self.assertEqual(len(owned),1)
            self.assertLess(len(owned[0].encode()),1000)
            self.assertIn('/usr/bin/env -i',owned[0])
            self.assertIn('DOCKER_CONTEXT='+case.reg.binding.docker_context,owned[0])
            self.assertIn(' run --env-file ',owned[0])
            self.assertTrue(case.call(si.inspect_activation,backend='cron').active)
            self.assertEqual(case.call(si.remove_scheduler,backend='cron').state,'removed')
            self.assertEqual(case.manager.tab,before)
            self.assertEqual(case.manager.effects,['crontab','crontab'])
        finally:
            case.doCleanups()

    def test_real_cron_renderer_fits_CI_shaped_paths_with_margin(self):
        """The real renderer with the LONGEST native-CI path shapes, not fake-OS ones.

        Only the host filesystem/pwd checks are replaced (these paths exist on the
        runner, not here); the registration/launcher contract checks still run.
        """
        from datetime import datetime, timezone
        from deploy.update import scheduler_assets
        from deploy.update.contracts import AutomationPolicy, InstallationBinding
        from deploy.update.runner_state import LauncherConfig, Registration
        home = Path('/home/runner')
        checkout = (home/'fagi-upd-e2e-real-upgrade-99999999999-99'/'acceptance'/
                    ('fagi-upd-e2e-real-one-opt-in-'+'f'*12)/'installation')
        # native_launcher_path order: compose wrapper dir, docker dir, cosign dir
        # (cosign-installer's default $HOME/.cosign), then fixed system dirs.
        path = ':'.join(['/home/runner/fagi-upd-e2e-real-tools.AbCdEf', '/usr/bin', '/home/runner/.cosign',
                         '/bin', '/usr/sbin', '/sbin'])
        python = Path('/opt/hostedtoolcache/Python/3.12.12/x64/bin/python3')
        binding = InstallationBinding('1'*32, 'a'*64, '2'*64, checkout, checkout/'.env', checkout/'.env',
            'standalone', False, 'fagi-upd-e2e-real-one-opt-in-'+'f'*12, (checkout/'docker-compose.yml',), (),
            real.CONTEXT, 'daemon', os.geteuid())
        registration = Registration(1, binding, LauncherConfig(python, home, path, 'runner'),
                                    datetime(2026, 10, 29, tzinfo=timezone.utc))
        def contract_only(value):
            value.__post_init__()
            value.launcher.__post_init__()
        store = SimpleNamespace(read_registration=lambda: registration)
        with patch.object(scheduler_assets, '_validate', side_effect=contract_only) as validate:
            assets = scheduler_assets.render_scheduler(store, AutomationPolicy(), backend='cron')
        validate.assert_called_once_with(registration)
        (content,) = assets.files.values()
        (line,) = [row for row in content.splitlines() if row.startswith('*/5 ')]
        self.assertLess(len(line.encode()), 1000-200, 'cron entry must keep margin under the 1000-byte bound')
        for value in ('HOME=/home/runner', 'PATH='+path, 'PYTHON='+str(python), 'DOCKER_CONTEXT='+real.CONTEXT,
                      str(checkout/'bin/update')+' run --env-file '+str(checkout/'.env')):
            self.assertIn(value, line)

    def test_acceptance_reserve_is_derived_from_measured_boot_cron_and_stability(self):
        boot=2*real.MEASURED_FIRST_BOOT_SECONDS+60
        update=2*real.CRON_PERIOD_SECONDS+60+boot+real.STABILITY_SECONDS
        self.assertEqual(real.acceptance_reserve_seconds('a-b-c'),boot+2*update)
        self.assertEqual(real.acceptance_reserve_seconds('upgrade'),2*(boot+update)+boot+2*real.CRON_PERIOD_SECONDS+60)
        # Two A->B->C qualifications plus the reserve must fit the job deadline;
        # the reserve is a bound on qualification, not a guard on acceptance.
        self.assertLess(real.acceptance_reserve_seconds('upgrade'),acceptance.JOB_SECONDS//3)
        with self.assertRaises(ValueError):
            real.acceptance_reserve_seconds('restore')

    def test_product_operations_use_the_runner_proven_isolated_invocation(self):
        h=real.ProductHarness(Path.home()/'fagi-unit-app-python',ROOT,'fagi-upd-e2e-real-unit')
        output=b'Django startup log line\n{"identity":"bucket/key","payload_sha256":"'+b'a'*64+b'"}\n'
        with patch.object(h,'service_id',return_value='c'*64), \
             patch.object(h,'docker',return_value=SimpleNamespace(stdout=output)) as docker:
            self.assertEqual(h.app_python('print(1)',{'k':'v'}),{'identity':'bucket/key','payload_sha256':'a'*64})
        self.assertEqual(docker.call_args.args,('exec','-i','c'*64,'python','-I','-B','-cprint(1)'))
        self.assertEqual(docker.call_args.kwargs['input'],b'{"k":"v"}')
        self.assertNotIn('-c',docker.call_args.args)
        with patch.object(h,'service_id',return_value='c'*64), \
             patch.object(h,'docker',return_value=SimpleNamespace(stdout=b'{"a":1,"a":2}\n')):
            with self.assertRaises(Exception):
                h.app_python('print(1)',{})

    def test_native_PATH_drops_ambient_entries_but_keeps_actual_host_tools(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            directory=Path(temp)
            for name in ('docker-compose','docker','cosign'):
                path=directory/name
                path.write_text('#!/bin/sh\nexit 99\n')
                path.chmod(0o700)
            path=real.native_launcher_path({'PATH':str(directory)+':/untrusted/irrelevant/path'})
            self.assertEqual(path,str(directory)+':/usr/bin:/bin:/usr/sbin:/sbin')
            self.assertNotIn('untrusted',path)
            (directory/'cosign').unlink()
            with self.assertRaisesRegex(ValueError,'cosign'):
                real.native_launcher_path({'PATH':str(directory)})

    def test_upgrade_qualifies_once_and_reuses_exact_candidate_for_fresh_forward_and_restore(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        from deploy.tests import test_update_e2e_automatic as automatic
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            versions=('v1.55.0','v1.55.1')
            inputs=dict(mode='upgrade',versions=versions,base=temp,candidates={v:{} for v in versions},
                deployment_dirs={},run_id=123,deadline_monotonic=9999999999)
            path=Path(temp)/'inputs.json'; path.write_text(json.dumps(inputs))
            publisher=Mock()
            publisher.transition.return_value='measured-stable-pr'
            harness=Mock()
            harness.automatic_publisher.return_value=nullcontext(publisher)
            case=automatic.AutomaticLifecycleE2E('test_one_opt_in_scheduled_A_B_C')
            with patch.dict(os.environ,{'FAGI_UPDATE_ACCEPTANCE_INPUTS':str(path)}), \
                 patch.object(automatic.e2e,'Harness',return_value=harness), \
                 patch.object(automatic,'acceptance_policy',return_value='measured-policy'),patch.object(case,'exercise') as exercise:
                case.test_one_opt_in_scheduled_A_B_C()
            self.assertEqual([c.args for c in publisher.prepare.call_args_list],[(v,) for v in versions])
            self.assertEqual([c.kwargs['restore'] for c in exercise.call_args_list],[False,True])
            self.assertEqual([c.args[1] for c in exercise.call_args_list],[versions,versions])
            self.assertEqual(publisher.hide_releases.call_count,2)

    def test_test_only_fault_stops_only_exact_pre_success_candidate_after_migrations(self):
        h=real.ProductHarness(Path.home()/'fagi-unit-fixed-fault',ROOT,'fagi-upd-e2e-real-unit')
        h.ports={'BACKEND_PORT':1234}
        manifest=versioned(default_v2(),'v1.54.0')
        component=manifest['components']['standalone']
        container=dict(Id='a'*64,Image='sha256:'+'d'*64,State={'Running':True},Config={'Labels':{
            'com.docker.compose.project':h.project,'com.docker.compose.service':'app'}})
        image=dict(RepoDigests=[component['image'].split(':v')[0]+'@'+component['digest']])
        migrations=dict(planned=[['app','0002']],applied=[['app','0002']],pending=[])
        def docker(*args,**kwargs):
            return SimpleNamespace(stdout=json.dumps([image if args[0]=='image' else container]).encode())
        for foreign in (False,True):
            with self.subTest(foreign=foreign),patch.object(h,'containers',return_value=[container]), \
                 patch.object(h,'docker',side_effect=docker) as command,patch.object(h,'request',return_value=(200,{})), \
                 patch.object(h,'migration_inventory',return_value=migrations),patch.object(h,'pending_forward_attempt',return_value='run'), \
                 patch.object(h,'create_candidate_only_witness',return_value={'identity':'S3-private'}),patch.object(h,'record') as record:
                container['Config']['Labels']['com.docker.compose.project']='foreign' if foreign else h.project
                if foreign:
                    with self.assertRaisesRegex(AssertionError,'identity changed'):
                        h.fail_after_migrations(manifest,timeout=2)
                    self.assertFalse(any(c.args[0]=='stop' for c in command.call_args_list))
                else:
                    self.assertEqual(h.fail_after_migrations(manifest,timeout=2),migrations)
                    self.assertEqual(command.call_args.args,('stop','--time','10',container['Id']))
                    self.assertEqual(record.call_args.args[1]['fault_seam'],real.FAULT_SEAM)
        from scripts.build_update_compatibility_proof import implementation_inventory
        self.assertTrue(all(real.FAULT_SEAM not in (ROOT/name).read_text() for name in implementation_inventory()))


if __name__ == '__main__':
    unittest.main()
