"""Daemon-free acceptance plumbing; never product compatibility evidence."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from deploy.tests.update_e2e import acceptance, backup_ci, harness, real_automatic
from deploy.tests.update_e2e.oci_registry import DockerHubMirror, MIRROR_HOST, OCIRegistry, REGISTRY_IMAGE
from deploy.update.manifest import COMPONENT_REPOSITORIES, COMPONENT_TAG_SUFFIX, parse_image_ref, validate_manifest


# Shape of `docker buildx inspect <name>` for setup-buildx-action's builder.
BUILDER_INSPECTION=(b'Name:          builder-unit\nDriver:        docker-container\n'
    b'Last Activity: 2026-10-29 22:26:10 +0000 UTC\n\nNodes:\nName:      builder-unit0\n'
    b'Endpoint:  colima-fagi-update\nStatus:    running\nBuildkit:  v0.25.1\nPlatforms: linux/amd64\n')


class AcceptancePlanTests(unittest.TestCase):
    def test_native_jobs_use_the_real_compose_plugin_without_a_context_override_wrapper(self):
        from deploy.tests.test_update_release_static import load
        workflow = load(ROOT/'.github/workflows/update-ci.yml')
        jobs = [job for job in workflow['jobs'].values() if job.get('name','').startswith('Real ')]
        self.assertEqual(len(jobs), 2)
        for job in jobs:
            setup = next(step['run'] for step in job['steps']
                         if 'fagi-upd-e2e-real-tools.' in step.get('run',''))
            self.assertIn('.ClientInfo.Plugins', setup)
            self.assertIn('test -n "$plugin" && test -f "$plugin" && test -x "$plugin"', setup)
            self.assertIn('ln -s "$plugin" "$dir/docker-compose"', setup)
            self.assertNotIn('exec docker --context', setup)

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

    def test_PR_build_returns_complete_uncertified_candidate_with_real_identity_checks(self):
        from deploy.tests.test_update_automatic_release import default_v2, versioned
        previous=versioned(default_v2(),'v1.55.0')
        original=copy.deepcopy(previous)
        sha='a'*40
        def digest(repo):
            return 'sha256:'+hashlib.sha256(repo.encode()).hexdigest()
        def execute(args,**kwargs):
            args=list(map(str,args))
            if args[:2] == ['git','rev-parse']:
                return (sha+'\n').encode()
            if 'info' in args:
                return json.dumps(['http://'+MIRROR_HOST+'/']).encode()
            if args[3:5] == ['buildx','inspect']:
                return BUILDER_INSPECTION
            if 'inspect' in args:
                rows=[]
                for ref in args[5:]:
                    parsed=parse_image_ref(ref)
                    rows.append(dict(Id='image-'+parsed.repository,Os='linux',Architecture='amd64',
                        RepoDigests=[parsed.name+'@'+digest(parsed.repository)]))
                return json.dumps(rows).encode()
            return b''
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            transport=object()
            chart=dict(chart='oci://localhost:12345/futureagi/acceptance-chart-v1-55-1',
                       version='1.55.1',digest='sha256:'+'e'*64)
            with patch.object(driver,'command',side_effect=execute) as command, \
                 patch.object(DockerHubMirror,'publish',side_effect=lambda repo,*args:digest(repo)) as publish, \
                 patch.object(driver,'historical_chart',return_value=chart) as package_chart, \
                 patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2',
                                        'FAGI_UPDATE_E2E_BUILDER':'builder-unit'}):
                candidate=driver.build_pr(previous,transport)
            self.assertEqual(validate_manifest(candidate),candidate)
            self.assertEqual(previous,original)
            self.assertEqual(candidate['version'],'v1.55.1')
            self.assertEqual(candidate['source_sha'],sha)
            self.assertFalse(candidate['compatibility']['automatic_apply'])
            self.assertIsNone(candidate['compatibility']['validation'])
            self.assertEqual(candidate['migration'],dict(requires_bootstrap=True,rollback='manual'))
            self.assertEqual(set(candidate['components']),set(COMPONENT_REPOSITORIES))
            built_names={name for name,*_ in acceptance.BUILDS}
            for name,component in candidate['components'].items():
                ref=parse_image_ref(component['image'])
                self.assertEqual(ref.registry,'docker.io')
                self.assertEqual(ref.tag,'v1.55.1'+COMPONENT_TAG_SUFFIX.get(name,''))
                self.assertEqual(ref.repository,COMPONENT_REPOSITORIES[name])
                self.assertEqual(component['platforms'],['linux/amd64'])
                self.assertEqual(component['digest'],digest(ref.repository) if name in built_names
                                 else previous['components'][name]['digest'])
            package_chart.assert_called_once_with('v1.55.1',sha,transport,candidate=True)
            self.assertEqual(publish.call_count,5)
            for call in publish.call_args_list:
                self.assertEqual(call.args[1:3],('v1.55.1','pr-'+sha+'-123-2'))
            calls=[list(map(str,call.args[0])) for call in command.call_args_list]
            builds=[argv for argv in calls if 'buildx' in argv]
            self.assertEqual(builds[0],['docker','--context','colima-fagi-update','buildx','inspect','builder-unit'])
            builds=builds[1:]
            self.assertEqual(len(builds),5)
            for argv in builds:
                self.assertEqual(argv[:7],['docker','--context','colima-fagi-update','buildx','build',
                                           '--builder','builder-unit'])
                self.assertIn('--output',argv)
                self.assertNotIn('--push',argv)
                self.assertNotIn('--load',argv)
                self.assertIn('REVISION='+sha,argv)
            self.assertEqual(builds[-1].count('--build-context'),4)
            self.assertEqual(sum('pull' in argv for argv in calls),15)
            observations=json.loads((Path(temp)/'pr-image-transport.json').read_bytes())
            self.assertEqual(set(observations['images']),built_names)
            self.assertFalse(observations['qualification_evidence'])
            self.assertEqual(json.loads((Path(temp)/'test-candidate-v1.55.1.json').read_bytes()),candidate)
            built=json.loads((Path(temp)/'pr-built-images.json').read_bytes())
            self.assertFalse(built['product_qualified'])
            self.assertEqual(set(built['unchanged_components']),set(COMPONENT_REPOSITORIES)-built_names)

    def test_PR_pull_refuses_retag_only_wrong_repository_digest_arch_or_tag_drift(self):
        image='docker.io/futureagi/standalone:v1.55.1'
        digest='sha256:'+'b'*64
        good=dict(Id='image-id',Os='linux',Architecture='amd64',RepoDigests=['futureagi/standalone@'+digest])
        bad_rows=[dict(good,RepoDigests=[]),dict(good,RepoDigests=['localhost:5000/futureagi/standalone@'+digest]),
            dict(good,RepoDigests=['futureagi/frontend@'+digest]),
            dict(good,RepoDigests=['futureagi/standalone@sha256:'+'c'*64]),dict(good,Architecture='arm64'),
            dict(good,Os='windows'),dict(good,Id='different-tag-id')]
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            for bad in bad_rows:
                with self.subTest(bad=bad),patch.object(driver,'command',side_effect=
                        [b'',b'',b'',json.dumps([good,good,bad]).encode()]):
                    with self.assertRaisesRegex(ValueError,'RepoDigest|identity differ'):
                        driver.pull_pr_image(image,digest,'pr-'+'a'*40+'-1-1')
            with patch.object(driver,'command',side_effect=[b'',b'',b'',json.dumps([good]*3).encode()]):
                result=driver.pull_pr_image(image,digest,'pr-'+'a'*40+'-1-1')
                self.assertEqual(result['image_id'],'image-id')

    def test_PR_build_refuses_unconfigured_mirror_before_build_or_upload(self):
        from deploy.tests.test_update_automatic_release import default_v2
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            with patch.object(driver,'command',side_effect=[b'a'*40,b'[]']) as command, \
                 patch.object(DockerHubMirror,'publish') as publish, \
                 patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2'}):
                with self.assertRaisesRegex(ValueError,'mirror is not configured'):
                    driver.build_pr(default_v2(),object())
            self.assertEqual(command.call_count,2)
            publish.assert_not_called()

    def test_PR_build_names_only_a_verified_docker_container_builder_before_any_build(self):
        from deploy.tests.test_update_automatic_release import default_v2
        docker_driver=BUILDER_INSPECTION.replace(b'docker-container',b'docker')
        mirrors=json.dumps(['http://'+MIRROR_HOST+'/']).encode()
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            for name,inspection,reason in (('',None,'explicit docker-container'),
                    ('bad name;rm',None,'explicit docker-container'),
                    ('builder-unit',docker_driver,'docker-container Buildx driver'),
                    ('builder-unit',BUILDER_INSPECTION+BUILDER_INSPECTION,'docker-container Buildx driver')):
                outputs=[b'a'*40,mirrors]+([inspection] if inspection is not None else [])
                with self.subTest(name=name,inspection=inspection), \
                     patch.object(driver,'command',side_effect=outputs) as command, \
                     patch.object(DockerHubMirror,'publish') as publish, \
                     patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2',
                                            'FAGI_UPDATE_E2E_BUILDER':name}):
                    with self.assertRaisesRegex(ValueError,reason):
                        driver.build_pr(default_v2(),object())
                    self.assertEqual(command.call_count,len(outputs))
                    self.assertFalse(any('build' in list(map(str,c.args[0])) for c in command.call_args_list))
                    publish.assert_not_called()

    def test_cron_environment_probe_starts_the_launcher_closure_in_a_cleared_environment(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            tools=Path(temp)/'tools'; tools.mkdir()
            for name in ('docker-compose','docker','cosign'):
                (tools/name).write_text('#!/bin/sh\nexit 0\n'); (tools/name).chmod(0o700)
            driver=acceptance.NativeAcceptance(Path(temp))
            outputs=[b'',b'x86_64\n',b'',b'']
            with patch.dict(os.environ,{'PATH':str(tools)+':/untrusted/ambient'}), \
                 patch.object(driver,'command',side_effect=outputs) as command:
                path=driver.cron_environment_probe()
            self.assertEqual(path,str(tools)+':/usr/bin:/bin:/usr/sbin:/sbin')
            calls=[list(map(str,c.args[0])) for c in command.call_args_list]
            for argv in calls:
                self.assertEqual(argv[:6],['/usr/bin/env','-i','HOME='+str(Path.home()),'PATH='+path,
                                           'PYTHON='+sys.executable,'DOCKER_CONTEXT=colima-fagi-update'])
                self.assertNotIn('untrusted',' '.join(argv))
            self.assertEqual(calls[0][6:],['/bin/bash','--',str(ROOT/'bin/update'),'--help'])
            self.assertEqual([c[6] for c in calls[1:]],['docker','docker-compose','cosign'])
            self.assertEqual(json.loads((Path(temp)/'cron-environment.json').read_bytes())['status'],'passed')
            with patch.dict(os.environ,{'PATH':str(tools)}), \
                 patch.object(driver,'command',side_effect=[b'',b'aarch64\n']):
                with self.assertRaisesRegex(ValueError,'native amd64'):
                    driver.cron_environment_probe()

    def test_PR_build_requires_real_revision_and_run_identity_before_upload(self):
        from deploy.tests.test_update_automatic_release import default_v2
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            for revision,env,reason in ((b'not-a-commit',{'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'},'source commit'),
                    (b'a'*40,{'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':''},'run and attempt identity')):
                with self.subTest(reason=reason),patch.object(driver,'command',return_value=revision) as command, \
                     patch.object(DockerHubMirror,'publish') as publish,patch.dict(os.environ,env):
                    with self.assertRaisesRegex(ValueError,reason):
                        driver.build_pr(default_v2(),object())
                    self.assertEqual(command.call_count,1)
                    publish.assert_not_called()

    def test_PR_build_refuses_prerelease_predecessor_before_any_command(self):
        from deploy.tests.test_update_automatic_release import default_v2, versioned
        previous=versioned(default_v2(),'v1.55.0-rc.1')
        previous['channel']='beta'
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            with patch.object(driver,'command') as command:
                with self.assertRaisesRegex(ValueError,'stable predecessor'):
                    driver.build_pr(previous,object())
                command.assert_not_called()

    def test_upgrade_candidate_reaches_test_inputs_instead_of_transport_refusal(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            old={'version':'v1.55.0'}
            target={'version':'v1.55.1'}
            with patch.object(acceptance,'get_json',return_value={'tag_name':old['version']}), \
                 patch.object(driver,'published',return_value=(old,Path(temp))), \
                 patch.object(driver,'build_pr',return_value=target), \
                 patch.object(acceptance.subprocess,'run',return_value=subprocess.CompletedProcess([],1)), \
                 patch.dict(os.environ,{'GITHUB_RUN_ID':'123'}):
                self.assertEqual(driver.run_tests('upgrade',object()),1)
            inputs=json.loads((Path(temp)/'inputs.json').read_bytes())
            self.assertEqual(inputs['candidates'][target['version']],target)
            self.assertEqual(inputs['versions'],[old['version'],target['version']])
            self.assertEqual(inputs['deployment_dirs'][target['version']],str(ROOT))

    def test_producer_refusal_is_clear_and_nonzero_without_echoing_private_diagnostics(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            driver=acceptance.NativeAcceptance(Path(temp))
            path=Path(temp)/'acceptance/fagi-upd-e2e-real-publisher-unit/v1.55.1/qualification/diagnostics.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(failure=dict(refusal_code='shipping_executor_unavailable',
                phase='shipping_product_rehearsal',active_suite='NEVER-ECHO-SUITE',
                suites=dict(source_snapshot='passed',target_readiness='failed',failure_restore='unrun'),
                detail='NEVER-ECHO-DETAIL'),private_fixture='NEVER-ECHO-THIS')))
            with patch.object(driver,'published',return_value=({},Path(temp))), \
                 patch.object(acceptance.subprocess,'run',return_value=subprocess.CompletedProcess([],1)), \
                 patch.dict(os.environ,{'GITHUB_RUN_ID':'123'}),patch.object(acceptance.sys,'stderr',io.StringIO()) as stderr:
                self.assertEqual(driver.run_tests('a-b-c',None),1)
            self.assertIn('shipping_executor_unavailable',stderr.getvalue())
            self.assertNotIn('NEVER-ECHO',stderr.getvalue())
            failure=json.loads((Path(temp)/'failure.json').read_bytes())
            self.assertEqual(failure['status'],'failed')
            self.assertEqual(failure['reason'],'shipping_executor_unavailable')
            self.assertFalse(failure['qualification_evidence'])
            self.assertEqual(failure['target_version'],'v1.55.1')
            self.assertEqual(failure['phase'],'shipping_product_rehearsal')
            self.assertEqual(failure['suites']['target_readiness'],'failed')
            self.assertNotIn('NEVER-ECHO',json.dumps(failure))

    def test_PR_chart_packages_real_source_with_private_version_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp, OCIRegistry() as registry:
            driver=acceptance.NativeAcceptance(Path(temp))
            sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
            chart=driver.historical_chart('v1.55.1',sha,registry,candidate=True)
            repo=chart['chart'].split('/',3)[-1]
            _,root,blobs=registry.repositories[repo]
            manifest=json.loads(blobs[root][0].read_bytes())
            payload=blobs[manifest['layers'][0]['digest']][0].read_bytes()
            with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as package:
                metadata=package.extractfile('futureagi/Chart.yaml').read().decode()
                self.assertIn('version: 1.55.1\n',metadata)
                self.assertIn('appVersion: "v1.55.1"\n',metadata)
                template=package.extractfile('futureagi/templates/_helpers.tpl').read()
                expected=subprocess.check_output(['git','show',sha+':deploy/helm/futureagi/templates/_helpers.tpl'],cwd=ROOT)
                self.assertEqual(template,expected)
            self.assertEqual(chart['version'],'1.55.1')
            self.assertEqual(chart['digest'],root)

    def test_full_qualify_CLI_exposes_the_native_entrypoint_interface(self):
        result=subprocess.run([sys.executable,'-I',str(ROOT/'scripts/rehearse_update_compatibility.py'),
            'qualify','--help'],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        for name in ('--source-ref','--target-ref','--platform','--deployment-dir',
                     '--reference-scale','--boot-timeout','--public-key','--docker-context','--out'):
            self.assertIn(name,result.stdout)
        # Integration availability is the producer lane's responsibility. Do not
        # pin a negative test to its former shipping_executor_unavailable state.

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

    def test_workflow_mirror_is_separate_runner_only_pinned_and_before_PR_builds(self):
        workflow=(ROOT/'.github/workflows/update-ci.yml').read_text()
        job=workflow.split('\n  automatic-upgrade:\n',1)[1].split('\n  automatic-a-b-c:\n',1)[0]
        self.assertIn('ONLY this disposable GitHub-hosted runner',job)
        self.assertIn('REGISTRY_IMAGE: '+REGISTRY_IMAGE,job)
        self.assertIn("config['registry-mirrors'] = ['http://"+MIRROR_HOST+"']",job)
        self.assertIn('sudo systemctl restart docker.service',job)
        self.assertIn('-p 127.0.0.1:5000:5000',job)
        self.assertIn('Full qualify/package must pass in this job',job)
        self.assertIn('pr-image-transport.json',job)
        self.assertLess(job.index('name: Ephemeral runner-only'),job.index('uses: docker/setup-buildx-action'))
        self.assertLess(job.index('uses: docker/setup-buildx-action'),job.index('acceptance.py --mode upgrade'))
        # The step id feeds the explicit builder; the default-context `use` does not.
        self.assertIn('- id: buildx\n        uses: docker/setup-buildx-action',job)
        self.assertIn('FAGI_UPDATE_E2E_BUILDER: ${{ steps.buildx.outputs.name }}',job)
        self.assertLess(job.index('FAGI_UPDATE_E2E_BUILDER'),job.index('acceptance.py --mode upgrade'))
        abc=workflow.split('\n  automatic-a-b-c:\n',1)[1].split('\n  backup:\n',1)[0]
        self.assertNotIn('daemon.json',abc)
        plan=acceptance.plan('upgrade')
        self.assertFalse(plan['image_transport']['proxy_mode'])
        self.assertEqual(plan['image_transport']['registry_image'],REGISTRY_IMAGE)
        self.assertEqual(plan['known_blockers'],[])
        self.assertIn('every shipping suite',plan['qualification_contract'])

    def test_workflow_native_jobs_have_read_only_permissions_and_no_publication(self):
        text=(ROOT/'.github/workflows/update-ci.yml').read_text()
        for name in ('automatic-upgrade','automatic-a-b-c'):
            job=text.split('\n  '+name+':\n',1)[1]
            job=job.split('\n  automatic-',1)[0].split('\n  backup:',1)[0]
            self.assertIn('timeout-minutes: 355',job)
            # Setup steps run before the deadline starts; keep >= 15 minutes.
            self.assertLessEqual(acceptance.JOB_SECONDS, 340*60)
            self.assertIn('systemctl is-enabled --quiet cron.service',job)
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


class DockerHubMirrorTests(unittest.TestCase):
    def test_registry_API_streams_verified_bytes_and_publishes_identical_PR_and_version_tags(self):
        uploads, manifests, calls = {}, {}, []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args):
                pass
            def do_POST(self):
                calls.append(('POST',self.path))
                self.send_response(202)
                self.send_header('Location','http://'+self.headers['Host']+self.path+'fixture-id?_state=private-upload-state')
                self.send_header('Content-Length','0')
                self.end_headers()
            def do_PUT(self):
                calls.append(('PUT',self.path))
                body=self.rfile.read(int(self.headers['Content-Length']))
                digest='sha256:'+hashlib.sha256(body).hexdigest()
                if '/blobs/uploads/' in self.path:
                    self.assert_upload_digest(body,digest)
                    uploads[digest]=body
                else:
                    manifests[self.path.rsplit('/',1)[1]]=body
                self.send_response(201)
                self.send_header('Docker-Content-Digest',digest)
                self.send_header('Content-Length','0')
                self.end_headers()
            def assert_upload_digest(self,body,digest):
                from urllib.parse import parse_qs, urlsplit
                if parse_qs(urlsplit(self.path).query)['digest'] != [digest]:
                    raise AssertionError('upload digest not bound to bytes')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root,raw,layer=OCIRegistryTests().layout(temp)
                pr_tag='pr-'+'a'*40+'-123-2'
                mirror=DockerHubMirror()
                with patch.object(mirror,'host','localhost:'+str(server.server_port)):
                    digest=mirror.publish('futureagi/standalone','v1.55.1',pr_tag,temp)
                self.assertEqual(digest,root['digest'])
                self.assertEqual(manifests,{root['digest']:raw,pr_tag:raw,'v1.55.1':raw})
                self.assertIn(layer['digest'],uploads)
                self.assertEqual(len(uploads),2)
                self.assertEqual(len(calls),7)
                self.assertTrue(all(path.startswith('/v2/futureagi/standalone/') for _,path in calls))
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()

    def test_upload_location_cannot_redirect_to_hub_other_hosts_or_repository(self):
        mirror=DockerHubMirror()
        repo='futureagi/standalone'
        path='/v2/'+repo+'/blobs/uploads/owned-id'
        pin='sha256:'+'a'*64
        for unsafe in ('https://'+mirror.host+path,'http://docker.io'+path,
                       '//attacker.invalid'+path,path+'?token=external',
                       '/v2/futureagi/frontend/blobs/uploads/owned-id',path+'#fragment',
                       path+'/../../other',''):
            with self.subTest(location=unsafe):
                with self.assertRaisesRegex(ValueError,'upload location|upload query'):
                    mirror.upload_path(unsafe,repo,pin)
        safe=mirror.upload_path('http://'+mirror.host+path+'?_state=opaque',repo,pin)
        self.assertTrue(safe.startswith(path+'?_state=opaque&digest=sha256%3A'))

    def test_bad_PR_tag_or_layout_fails_before_any_registry_write(self):
        mirror=DockerHubMirror()
        with patch.object(mirror,'request') as request:
            with self.assertRaisesRegex(ValueError,'PR transport tag'):
                mirror.publish('futureagi/standalone','v1.55.1','latest','/not-read')
            with tempfile.TemporaryDirectory() as temp:
                _,_,layer=OCIRegistryTests().layout(temp)
                (Path(temp)/'blobs/sha256'/layer['digest'][7:]).write_bytes(b'bad')
                with self.assertRaisesRegex(ValueError,'OCI blob|OCI digest'):
                    mirror.publish('futureagi/standalone','v1.55.1','pr-'+'a'*40+'-1-1',temp)
            request.assert_not_called()

    def test_registry_digest_mismatch_cannot_become_a_candidate_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            OCIRegistryTests().layout(temp)
            mirror=DockerHubMirror()
            response={'location':'/v2/futureagi/standalone/blobs/uploads/owned-id'}
            with patch.object(mirror,'request',side_effect=[response,{'docker-content-digest':'sha256:'+'0'*64}]):
                with self.assertRaisesRegex(ValueError,'blob digest differs'):
                    mirror.publish('futureagi/standalone','v1.55.1','pr-'+'a'*40+'-1-1',temp)


if __name__ == '__main__':
    unittest.main()
