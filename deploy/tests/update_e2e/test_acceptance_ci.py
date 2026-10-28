"""Daemon-free acceptance plumbing; never product compatibility evidence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from deploy.tests.update_e2e import acceptance, backup_ci, harness, real_automatic
from deploy.tests.update_e2e.oci_registry import OCIRegistry


class AcceptancePlanTests(unittest.TestCase):
    def test_both_plans_are_isolated_daemon_free_CLI(self):
        for mode in ('upgrade','a-b-c'):
            with self.subTest(mode=mode):
                result=subprocess.run([sys.executable,'-I',str(Path(acceptance.__file__)),'--mode',mode,'--plan'],
                    capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                plan=json.loads(result.stdout)
                self.assertTrue(plan['fail_closed'])
                self.assertFalse(plan['product_qualification'])
                self.assertEqual(plan['context'],'colima-fagi-update')
                self.assertIn('qualify',' '.join(plan['qualification']))
                self.assertIn('package',' '.join(plan['qualification']))
        self.assertEqual(acceptance.plan('a-b-c')['versions'],['v1.53.0','v1.54.0','v1.55.0'])
        self.assertEqual(len(acceptance.plan('upgrade')['builds']),5)

    def test_PR_builds_never_invent_missing_release_package_or_bypass_registry_guard(self):
        from types import SimpleNamespace
        previous={'version':'v1.55.0','components':{name:{'image':'futureagi/'+repo+':v1.55.0'}
            for name,repo in (('backend','future-agi'),('frontend','frontend'),('fi_collector','fi-collector'),
                              ('agentcc_gateway','agentcc-gateway'),('standalone','standalone'))}}
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            with patch.object(driver,'command',side_effect=[b'a'*40+b'\n']+[b'']*5) as command:
                registry=SimpleNamespace(host='localhost:12345',add=lambda *args:'sha256:'+'b'*64)
                with self.assertRaisesRegex(RuntimeError,'complete real component/chart candidate'):
                    driver.build_pr(previous,registry)
            calls=[list(map(str,call.args[0])) for call in command.call_args_list]
            self.assertEqual(len(calls),6)
            for argv in calls[1:]:
                self.assertEqual(argv[:4],['docker','--context','colima-fagi-update','buildx'])
                self.assertIn('--output',argv)
                self.assertNotIn('--push',argv)
                self.assertNotIn('--load',argv)
            self.assertEqual(len(list(Path(temp).glob('*manifest*'))),0)
            built=json.loads((Path(temp)/'pr-built-images.json').read_bytes())
            self.assertFalse(built['product_qualified'])
            self.assertEqual(len(built['components']),5)

    def test_execution_is_refused_outside_native_CI_before_provisioning(self):
        with patch.object(acceptance,'native_ci',return_value=False),patch.object(acceptance,'NativeAcceptance') as driver:
            with self.assertRaises(SystemExit):
                acceptance.main(['--mode','upgrade','--out','/tmp/not-used'])
        driver.assert_not_called()

    def test_native_result_cannot_be_green_on_zero_or_skipped_tests(self):
        for log in (b'Ran 0 tests in 0.0s\n\nOK\n',b'Ran 1 test in 0.0s\n\nOK (skipped=1)\n'):
            with self.subTest(log=log),tempfile.TemporaryDirectory(dir=Path.home()) as temp:
                driver=acceptance.NativeAcceptance(Path(temp))
                def fake_unittest(argv,**kwargs):
                    kwargs['stdout'].write(log)
                    return subprocess.CompletedProcess(argv,0)
                with patch.object(driver,'published',return_value=({},Path(temp))), \
                     patch.object(acceptance.subprocess,'run',side_effect=fake_unittest), \
                     patch.dict(os.environ,{'GITHUB_RUN_ID':'123'}):
                    with self.assertRaisesRegex(RuntimeError,'without skips'):
                        driver.run_tests('a-b-c',None)

    def test_harness_factory_never_starts_fake_registry_or_images(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            h=harness.Harness(base=Path(temp))
            with patch('deploy.tests.update_e2e.automatic_publisher.AutomaticPublisher') as publisher, \
                 patch.object(h,'start') as fake_start:
                h.automatic_publisher(lane='product',versions=('v1.53.0',),retained_layout='v1.53.0',
                    candidates={'v1.53.0':{}},deployment_dirs={'v1.53.0':temp})
            fake_start.assert_not_called()
            options=publisher.call_args.kwargs
            self.assertEqual(options['docker_context'],'colima-fagi-update')
            self.assertTrue(callable(options['product_factory']))
            self.assertNotIn('synthetic_fixtures',options)

    def test_started_synthetic_harness_cannot_be_used_for_product(self):
        h=harness.Harness(); h._started=True
        with self.assertRaises(ValueError):
            h.automatic_publisher(lane='product',versions=('v1.53.0',),retained_layout='v1.53.0',
                candidates={},deployment_dirs={})

    def test_real_setup_CLI_exposes_acceptance_options_without_a_daemon(self):
        result=subprocess.run([str(ROOT/'bin/update'),'setup','--help'],cwd=ROOT,
            env=dict(os.environ,PYTHON=sys.executable),capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        for option in ('--mode','--scheduler','--release-source','--public-key','--ca-file',
                       '--backup-destination','--tick-timeout-seconds','--stability-period-seconds',
                       '--migration-timeout-seconds','--recovery-startup-timeout-seconds'):
            self.assertIn(option,result.stdout)

    def test_product_harness_forbids_test_driven_ticks_and_manual_updates(self):
        h=real_automatic.ProductHarness(Path.home()/'fagi-fixture',ROOT,'fagi-upd-e2e-real-unit')
        with patch.object(h,'command') as command:
            for verb in ('run','apply','retry','enable','schedule'):
                with self.assertRaises(ValueError):
                    h.bin_update(verb)
        command.assert_not_called()

    def test_fixed_updater_closure_is_copied_without_changing_retained_deployment(self):
        from deploy.update.manifest import hash_path
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            h=real_automatic.ProductHarness(Path(temp),ROOT,'fagi-upd-e2e-real-unit')
            h.root.mkdir()
            (h.root/'docker-compose.yml').write_text('retained A deployment data, not a running fixture')
            digest=hash_path(h.root,'docker-compose.yml')
            h.source_manifest={'deployment':{'files':{'docker-compose.yml':digest}}}
            with patch.object(h,'docker') as docker:
                h.install_updater()
            docker.assert_not_called()
            self.assertEqual(hash_path(h.root,'docker-compose.yml'),digest)
            inventory=json.loads((h.evidence/'updater-code-inventory.json').read_bytes())
            for name in ('bin/update','scripts/update_rehearsal_shipping.py','deploy/standalone/bin/bootstrap.py'):
                self.assertEqual(inventory[name],hashlib.sha256((ROOT/name).read_bytes()).hexdigest())
            self.assertFalse(any(name.endswith('.pyc') for name in inventory))

    def test_updater_install_refuses_a_conflicting_retained_runtime_file(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            h=real_automatic.ProductHarness(Path(temp),ROOT,'fagi-upd-e2e-real-unit')
            h.root.mkdir()
            h.source_manifest={'deployment':{'files':{'deploy/standalone/bin/bootstrap.py':'0'*64}}}
            with self.assertRaisesRegex(ValueError,'overlaps retained'):
                h.install_updater()
            self.assertFalse((h.root/'bin/update').exists())

    def test_product_compose_command_always_binds_dedicated_context(self):
        h=real_automatic.ProductHarness(Path.home()/'fagi-fixture',ROOT,'fagi-upd-e2e-real-unit',compose=['docker','compose'])
        with patch.object(h,'command') as command:
            h.dc('config','--format','json')
        self.assertEqual(command.call_args.args[0][:4],['docker','--context','colima-fagi-update','compose'])

    def test_native_null_bind_exception_does_not_apply_on_mac_or_non_CI(self):
        with patch.object(real_automatic.sys,'platform','darwin'),patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}):
            self.assertFalse(real_automatic.native_ci())
        with patch.object(real_automatic.sys,'platform','linux'),patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}):
            self.assertFalse(real_automatic.native_ci())

    def test_installed_scheduler_test_has_no_tick_or_synthetic_calls(self):
        source=(ROOT/'deploy/tests/test_update_e2e_automatic.py').read_text()
        self.assertNotIn('h.start(',source)
        self.assertNotIn('h.new_checkout(',source)
        self.assertNotIn('bin_update("run"',source)
        self.assertIn('publisher.publish(version)',source)
        self.assertIn('checkout.fail_after_migrations',source)
        self.assertIn('AUTOMATIC_UPDATE_DISCLOSURE',source)

    def test_backup_native_entrypoint_sets_actual_opt_in_and_discovery_pattern(self):
        source=Path(backup_ci.__file__).read_text()
        self.assertIn("FAGI_UPDATE_BACKUP_E2E='1'",source)
        self.assertIn("'-m','unittest','discover'",source)
        self.assertIn("'native_backup.py'",source)
        self.assertNotIn('FUTURE_AGI_UPDATE_E2E=',source)

    def test_workflow_native_jobs_have_read_only_permissions_and_no_publication(self):
        text=(ROOT/'.github/workflows/update-ci.yml').read_text()
        for name in ('automatic-upgrade','automatic-a-b-c'):
            job=text.split('\n  '+name+':\n',1)[1]
            job=job.split('\n  automatic-',1)[0].split('\n  backup:',1)[0]
            self.assertIn('timeout-minutes: 360',job)
            self.assertIn('runs-on: ubuntu-24.04',job)
            self.assertIn('contents: read',job)
            self.assertNotIn('packages: write',job)
            self.assertNotIn('id-token: write',job)
            self.assertNotIn('secrets.',job)
        for suffix in ('**/*.py','**/*.go','**/*.sql','**/*.sh','deploy/**'):
            self.assertGreaterEqual(text.count('"'+suffix+'"'),2)


class OCIRegistryTests(unittest.TestCase):
    def layout(self,directory):
        directory=Path(directory)
        blobs=directory/'blobs/sha256'; blobs.mkdir(parents=True)
        def put(raw,media):
            digest=hashlib.sha256(raw).hexdigest()
            (blobs/digest).write_bytes(raw)
            return dict(digest='sha256:'+digest,size=len(raw),mediaType=media)
        config=put(b'{"architecture":"amd64","os":"linux"}','application/vnd.oci.image.config.v1+json')
        layer=put(b'transport-fixture-not-product-image','application/vnd.oci.image.layer.v1.tar+gzip')
        raw=json.dumps(dict(schemaVersion=2,config=config,layers=[layer])).encode()
        root=put(raw,'application/vnd.oci.image.manifest.v1+json')
        (directory/'index.json').write_text(json.dumps({'manifests':[root]}))
        return root,raw,layer

    def test_real_HTTP_GET_HEAD_digest_and_no_upload_route(self):
        with tempfile.TemporaryDirectory() as temp:
            root,raw,layer=self.layout(temp)
            with OCIRegistry() as registry:
                digest=registry.add('futureagi/standalone','v1.56.0',temp)
                self.assertEqual(digest,root['digest'])
                url='http://'+registry.host+'/v2/futureagi/standalone/manifests/v1.56.0'
                with urlopen(url,timeout=5) as response:
                    self.assertEqual(response.read(),raw)
                    self.assertEqual(response.headers['Docker-Content-Digest'],digest)
                with urlopen(Request(url,method='HEAD'),timeout=5) as response:
                    self.assertEqual(response.read(),b'')
                    self.assertEqual(int(response.headers['Content-Length']),len(raw))
                with self.assertRaises(HTTPError) as raised:
                    urlopen(Request('http://'+registry.host+'/v2/futureagi/standalone/blobs/uploads/',data=b'x',method='POST'),timeout=5)
                self.assertEqual(raised.exception.code,501)
                self.assertIsNone(registry.lookup('/v2/other/repo/blobs/'+layer['digest']))
                self.assertIsNone(registry.lookup('/v2/futureagi/standalone/blobs/../../index.json'))
                self.assertIsNone(registry.lookup('/v2/futureagi/standalone/manifests/v1.56.0?token=x'))

    def test_tampered_or_symlink_blob_never_registered(self):
        for kind in ('tamper','symlink'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root,raw,layer=self.layout(temp)
                path=Path(temp)/'blobs/sha256'/layer['digest'][7:]
                if kind == 'tamper':
                    path.write_bytes(b'x'*layer['size'])
                else:
                    other=Path(temp)/'other'; path.rename(other); path.symlink_to(other)
                with self.assertRaises(ValueError):
                    OCIRegistry().add('futureagi/standalone','v1.56.0',temp)

    def test_multiple_descriptors_refuse_instead_of_choosing_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            root,_,_=self.layout(temp)
            (Path(temp)/'index.json').write_text(json.dumps({'manifests':[root,root]}))
            with self.assertRaises(ValueError):
                OCIRegistry().add('futureagi/standalone','v1.56.0',temp)


if __name__ == '__main__':
    unittest.main()
