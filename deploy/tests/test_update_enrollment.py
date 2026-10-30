"""Setup protocol tests: real signatures/baseline/env-commit/activation stores.

Docker/state observations and OS managers are finite PRIVATE fixtures, not real
product/backend certification. No host scheduler, Docker daemon, or network.
"""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from dataclasses import replace
from unittest import mock

from deploy.tests import test_update_baseline as baseline_tests
from deploy.tests.test_update_baseline import SimulatedCrash
from deploy.tests.test_update_compatibility import bind_proof, restore_release
from deploy.tests.test_update_runner_state import registration
from deploy.tests.test_update_scheduler_install import Manager
from deploy.tests.test_update_trust import raw_settings
from deploy.update import enrollment as en
from deploy.update import scheduler_install as si
from deploy.update.automatic import BackupConfiguration
from deploy.update.baseline import BaselineStore, sha
from deploy.update.contracts import ReasonCode, VerificationSnapshot
from deploy.update.discovery import DiscoveredRelease, DiscoveryResult
from deploy.update.envfile import read_env
from deploy.update.manifest import canonical_json, manifest_hash
from deploy.update.orchestrator import GuardRefused
from deploy.update.receipt import StateDir
from deploy.update.runner_state import RunnerStateStore, StoreError
from deploy.update.standalone_backup import BackupBinding, BackupPolicy
from deploy.update.trust import FetchedManifest, Verifier

ROOT = Path(__file__).resolve().parents[2]


class InstallationFixture:
    def __init__(self, fixture):
        self.f = fixture
        self.root = fixture.root
        self.env_file = self.env_target = fixture.store.env_target
        self.env = read_env(self.env_file)
        self.runner = SimpleNamespace(policy=fixture.options_policy)

    def config(self, refresh=False):
        return self.f.model

    def fresh(self):
        return InstallationFixture(self.f)

    def futureagi_services(self):
        return [{'service': 'app', 'component': 'standalone', 'image': 'docker.io/futureagi/standalone:' +
                 (self.env.get('FUTURE_AGI_VERSION') or 'latest'), 'buildable': self.f.source_build}]

    def containers(self):
        return [{'id': 'a' * 64, 'service': 'app', 'status': 'running', 'image_id': 'sha256:' + 'd' * 64}]

    def image(self, ref):
        if ref in self.f.aliases or ref.startswith('sha256:') or ref.endswith(':latest'):
            return dict(self.f.image_data, id=self.f.aliases.get(ref, 'sha256:' + 'd' * 64))
        return None

    def docker_run(self, *args, **kwargs):
        if args[0] == 'inspect':
            return SimpleNamespace(returncode=0, stdout=json.dumps([{
                'Id': 'a' * 64, 'HostConfig': {'RestartPolicy': {'Name': self.f.restart_policy}},
            }]))
        self.f.assertEqual(args[:2], ('image', 'tag'))
        self.f.tags.append(args)
        self.f.aliases[args[3]] = args[2]
        return SimpleNamespace(returncode=0)


class HostFixture(en.SetupHost):
    def __init__(self, fixture):
        self.f = fixture
        self.boundaries = []
        self.fail = None
        self.after_install = None
        self.probe_missing = False

    def boundary(self, name):
        self.boundaries.append(name)
        if self.fail == name:
            raise SimulatedCrash()

    def context(self, options):
        self.boundary('context')
        return 'dedicated-context'

    def open(self, store, config, registered=None):
        self.boundary('open')
        self.f.options_policy = config.policy
        return InstallationFixture(self.f), registered or self.f.reg

    def discovery(self, store, config, reg, installation):
        self.boundary('discovery')
        self.f.verifier = Verifier(config.as_settings(), host_only=True, which=lambda _: self.f.cosign,
                                  environ=self.f.sign_env)
        release = self.f.a
        # Snapshot identity is derived from the same actual pinned key, not path.
        self.f.assertEqual(release.trust_fingerprint, self.f.verifier.trust_fingerprint())
        return SimpleNamespace(verifier=self.f.verifier,
            refresh=lambda version, channel: DiscoveryResult(release, checked_at=en.now()))

    def render_config(self, installation, raw):
        self.boundary('render')
        return self.f.render_config(raw)

    def observe(self, installation, reg, release, execution, policy):
        self.boundary('observe')
        if self.probe_missing:
            raise GuardRefused(ReasonCode.INITIALIZATION_REQUIRED)
        self.f.assertEqual(execution, release.manifest['deployment'])
        return self.f.observation(release, installation.env_file.read_bytes())

    def capacity(self, installation, reg, helper, policy):
        self.boundary('capacity')
        self.f.assertEqual(helper, 'sha256:' + 'd' * 64)

    def backup(self, installation, reg, observation, config, *, previous=None):
        self.boundary('backup')
        # Synthetic binding, never a fake success proof. The real backup adapter
        # is covered separately; this protocol verifies its immutable handoff.
        binding = reg.binding
        doc = {'schema_version': 1, 'installation_fingerprint': binding.installation_fingerprint,
            'root': str(binding.checkout), 'context': binding.docker_context, 'daemon': binding.daemon_identity,
            'project': binding.compose_project, 'profiles': list(binding.profiles),
            'volumes': dict(observation.volumes), 'volume_root_ids': {},
            'files': {'.env': sha(observation.env_bytes), **dict(observation.facts.deployment_files)},
            'compose_sha256': observation.facts.config_fingerprint,
            'images': {name: replicas[0].image_id for name, replicas in
                       {**observation.facts.product_images, **observation.facts.dependency_images}.items()}}
        return BackupConfiguration(BackupBinding(canonical_json(doc)), config.backup_destination, BackupPolicy()).raw

    def install(self, store, policy, **kwargs):
        self.boundary('install')
        # Positive lock-order check, before invoking the real activation adapter.
        with store.runner_lock(), StateDir(store.checkout).lock(automation=True):
            self.f.assertFalse(store.read_control().enabled_override)
        result = si.install_scheduler(store, policy, _host=self.f.manager_host, **kwargs)
        if self.after_install:
            self.after_install()
        return result

    def inspect(self, store, policy, **kwargs):
        self.boundary('inspect')
        return si.inspect_activation(store, policy, _host=self.f.manager_host, **kwargs)

    def remove(self, store, policy, **kwargs):
        self.f.assertFalse(store.read_control().enabled_override)
        with store.runner_lock(), StateDir(store.checkout).lock(automation=True):
            pass
        return si.remove_scheduler(store, policy, _host=self.f.manager_host, **kwargs)


class EnrollmentTests(unittest.TestCase):
    # Reuse only explicit synthetic observation/receipt helpers; not inherited tests.
    observation = baseline_tests.BaselineCoreTests.observation
    render_config = staticmethod(baseline_tests.BaselineCoreTests.render_config)
    prepare = baseline_tests.BaselineCoreTests.prepare
    receipt = baseline_tests.BaselineCoreTests.receipt

    @classmethod
    def setUpClass(cls):
        cls.cosign = os.environ.get('FUTURE_AGI_UPDATE_TEST_COSIGN') or shutil.which('cosign')
        if not cls.cosign:
            raise RuntimeError('setup tests require real host cosign v3+; set FUTURE_AGI_UPDATE_TEST_COSIGN')
        cls.tmp = tempfile.TemporaryDirectory(prefix='setup-signatures-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.keys = Path(cls.tmp.name).resolve()
        cls.sign_env = dict(os.environ, COSIGN_PASSWORD='')
        subprocess.run([cls.cosign, 'generate-key-pair'], cwd=str(cls.keys), env=cls.sign_env,
                       capture_output=True, check=True, timeout=30)
        b, previous, proof, _ = restore_release()
        cls.previous = json.loads(previous)
        cls.files = {name: ('synthetic layout ' + name + '\n').encode()
                     for name in cls.previous['deployment']['files']}
        deployment = {'files': {name: sha(raw) for name, raw in cls.files.items()}}
        cls.previous['deployment'] = copy.deepcopy(deployment)
        b['deployment'] = copy.deepcopy(deployment)
        row = b['compatibility']['predecessors'][0]
        row.update(manifest_hash=manifest_hash(cls.previous), raw_manifest_sha256=sha(canonical_json(cls.previous)),
                   execution_deployment_hash=manifest_hash(deployment))
        proof['deployment'] = copy.deepcopy(deployment)
        proof['predecessors'][0].update(predecessor=copy.deepcopy(row), predecessor_deployment=copy.deepcopy(deployment),
                                       execution_deployment=copy.deepcopy(deployment))
        raw_proof = bind_proof(b, proof)
        cls.verifier = Verifier(raw_settings(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(cls.keys / 'cosign.pub'),
                                             FUTURE_AGI_UPDATE_COSIGN_OFFLINE='true'),
                                host_only=True, which=lambda _: cls.cosign, environ=cls.sign_env)
        cls.releases = {}
        for manifest, proof_bytes in ((cls.previous, b''), (b, raw_proof)):
            m = cls.keys / (manifest['version'] + '.json')
            bundle = cls.keys / (manifest['version'] + '.bundle')
            m.write_bytes(canonical_json(manifest))
            subprocess.run([cls.cosign, 'sign-blob', '--yes', '--key', str(cls.keys / 'cosign.key'),
                '--use-signing-config=false', '--tlog-upload=false', '--bundle', str(bundle), str(m)],
                env=cls.sign_env, cwd=str(cls.keys), capture_output=True, check=True, timeout=30)
            verification = cls.verifier.verify(FetchedManifest(str(m), cls.keys, m, m.read_bytes(), manifest, bundle))
            if not verification.verified:
                raise AssertionError(verification.error)
            snapshot = VerificationSnapshot(True, verification.method, verification.signer, verification.issuer,
                                            verification.tool, en.now(), verification.public_key_sha256)
            cls.releases[manifest['version']] = DiscoveredRelease(m.read_bytes(), bundle.read_bytes(), manifest_hash(manifest),
                cls.verifier.trust_fingerprint(), snapshot, proof_bytes, (), 'future-agi/future-agi', '5' * 64)
        from deploy.update.compatibility import topology_profile
        cls.layout = topology_profile('standalone', ())
        cls.dependencies = proof['predecessors'][0]['dependencies']

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='setup-private-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / 'checkout'
        self.root.mkdir(mode=0o700)
        self.env_bytes = b'# untouched\r\nSECRET="keep $literal"\r\nFUTURE_AGI_VERSION=v1.51.0  # pin\r\nOTHER=x\r\n'
        (self.root / '.env').write_bytes(self.env_bytes)
        for name, raw in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        binpath = self.root / 'bin'
        binpath.mkdir()
        for name in ('update', 'crontab', 'pgrep', 'systemctl', 'loginctl', 'launchctl'):
            path = binpath / name
            path.write_text('#!/bin/sh\nexit 99\n')  # Safe if the test misses its private manager seam.
            path.chmod(0o700)
        self.store = RunnerStateStore(self.root, self.root / '.env')
        self.reg = registration(self.store)
        self.reg = replace(self.reg, launcher=replace(self.reg.launcher, path=str(binpath) + ':/usr/bin:/bin'))
        self.registration = self.reg
        self.time = en.now()
        self.a, self.b_release = (self.releases[v] for v in ('v1.51.0', 'v1.52.0'))
        self.image_data = {'version': 'v1.51.0', 'repo_digests': [
            'docker.io/futureagi/standalone@' + self.a.manifest['components']['standalone']['digest']]}
        self.aliases = {self.a.manifest['components']['standalone']['image']: 'sha256:' + 'd' * 64}
        self.tags = []
        self.source_build = False
        self.restart_policy = 'unless-stopped'
        self.model = {'services': {name: {'restart': 'unless-stopped'} for name in ('app', 'postgres', 'clickhouse')}}
        self.options = SimpleNamespace(mode='apply', yes=True, public_key=str(self.keys / 'cosign.pub'),
            backup_destination=str(self.base / 'backups'), scheduler='cron', cron_service='cron', window='always')
        self.options_policy = en.configuration(self.store, self.options, 'dedicated-context')[0].policy
        self.units = self.base / 'units'
        self.units.mkdir(mode=0o700)
        self.runtime = self.base / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.name = 'futureagi-compose-update-' + self.store.selection_hash
        self.manager = Manager(self)
        self.manager_host = si._Host(platform='linux', unit_directory=self.units,
                                     runtime_directory=self.runtime, run=self.manager)
        self.host = HostFixture(self)

    def setup(self, **changes):
        return en.setup_automation(self.store, SimpleNamespace(**dict(vars(self.options), **changes)), host=self.host)

    def test_setup_apply_selects_mandatory_restore_and_pins_a6_policy(self):
        config, _, _ = en.configuration(self.store, self.options, 'dedicated-context')
        self.assertEqual(config.policy.qualification_policy, 'standalone-cold-restore-v1')
        self.assertFalse(config.policy.automatic_rollback)
        text = en.setup_summary(self.store, self.options)
        self.assertEqual(text.count(en.AUTOMATIC_UPDATE_DISCLOSURE), 1)
        self.assertIn('--yes explicitly acknowledges', text)
        custom = SimpleNamespace(**dict(vars(self.options), restore_timeout_seconds=4000,
            wait_timeout_seconds=4200, recovery_startup_timeout_seconds=4200,
            migration_timeout_seconds=4200, tick_timeout_seconds=43200, estimate_safety_factor=3))
        configured, _, _ = en.configuration(self.store, custom, 'dedicated-context')
        self.assertEqual(configured.policy.restore_timeout_seconds, 4000)
        self.assertEqual(configured.policy.estimate_safety_factor, 3)
        en.check_options(configured, custom)
        with self.assertRaises(GuardRefused):
            en.check_options(configured, SimpleNamespace(restore_timeout_seconds=3999))

    def test_unsafe_model_or_observed_restart_policy_refuses_before_pins_or_activation(self):
        for unsafe in ('always', 'on-failure'):
            self.model['services']['postgres']['restart'] = unsafe
            with self.assertRaises(GuardRefused) as caught:
                self.setup()
            self.assertEqual(caught.exception.reason_code, ReasonCode.UNSUPPORTED_INSTALLATION)
            self.assertEqual(self.tags, [])
            self.assertNotIn('install', self.host.boundaries)
            self.model['services']['postgres']['restart'] = 'unless-stopped'
            self.restart_policy = unsafe
            with self.assertRaises(GuardRefused) as caught:
                en.qualify_restart_policies(InstallationFixture(self))
            self.assertEqual(caught.exception.reason_code, ReasonCode.UNSUPPORTED_INSTALLATION)
            self.restart_policy = 'unless-stopped'
        self.restart_policy = 'no'
        en.qualify_restart_policies(InstallationFixture(self))

    def test_deactivation_disables_forward_but_retains_safety_launcher_when_pending(self):
        self.setup()
        tab = self.manager.tab
        state = self.store.read_state()
        active = replace(state, active_attempt_id='a' * 32, active_operation_token='b' * 32)
        with mock.patch.object(self.store, 'read_state', return_value=active):
            result = en.setup_automation(self.store, SimpleNamespace(deactivate=True, yes=True), host=self.host)
            direct = si.remove_scheduler(self.store, en.read_setup_configuration(self.store).policy,
                backend='cron', cron_service='cron', _host=self.manager_host)
            self.assertEqual(direct.reason.value, 'automatic_recovery_pending')
        self.assertEqual(result.reason, 'automatic_recovery_pending')
        self.assertEqual(result.code, 3)
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.manager.tab, tab)
        self.assertEqual(en._read(self.store, en.PROGRESS, en.SetupProgress).phase, 'activated')
        final = en.setup_automation(self.store, SimpleNamespace(deactivate=True, yes=True), host=self.host)
        self.assertEqual(final.state, 'deactivated')

    def test_one_setup_activates_actual_adapter_and_preserves_cron_env(self):
        original = self.manager.tab
        result = self.setup()
        self.assertEqual((result.state, result.scheduler, result.policy_state), ('active', 'active', 'enabled'))
        self.assertTrue(self.manager.tab.startswith(original))
        config = en.load_setup_configuration(self.store)
        self.assertEqual(config.policy.mode, 'apply')
        self.assertEqual(config.policy.probe_timeout_seconds, 90)
        self.assertGreaterEqual(config.policy.tick_timeout_seconds, config.policy.required_tick_budget_seconds)
        self.assertEqual(self.store.env_target.read_bytes(), self.env_bytes)
        self.assertEqual(self.manager.effects.count('crontab'), 1)
        registered = (self.store.path / 'registration.json').read_bytes()
        with mock.patch.object(BaselineStore, 'enroll', side_effect=AssertionError('must not reenroll')):
            self.assertEqual(self.setup().state, 'active')
        self.assertEqual(self.manager.effects.count('crontab'), 1)
        self.assertEqual((self.store.path / 'registration.json').read_bytes(), registered)
        for file in (en.CONFIG, en.PROGRESS, en.KEY):
            self.assertEqual((self.store.path / file).stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.store.path / 'facts.json').exists())

    def test_scheduled_engine_compares_enrolled_settings_with_the_activated_setup_configuration(self):
        from dataclasses import replace as replace_policy
        from deploy.update.apply import Updater
        from deploy.update.orchestrator import guarded_updater
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.receipt import StateDir
        self.assertEqual(self.setup().state, 'active')
        self.assertFalse((self.root / 'update.conf').exists())  # setup never writes legacy settings
        config = en.load_setup_configuration(self.store)
        settings = config.as_settings()  # exactly what `bin/update run` gives HostRunner

        def build(values):
            supervisor = ProcessSupervisor(values.automation_policy, docker_context='dedicated-context', environment={})
            installation = SimpleNamespace(runner=supervisor, root=self.root, production=False)
            guard = SimpleNamespace(store=self.store, automatic=None, context=lambda engine: None)
            return guarded_updater(installation, values, StateDir(self.root), guard, supervisor)

        self.assertIsInstance(build(settings), Updater)
        # Real drift from the activated configuration still refuses.
        from deploy.update.settings import Settings
        changed = Settings(dict(settings._values, FUTURE_AGI_UPDATE_INTERVAL_SECONDS='600'), settings.sources)
        with self.assertRaises(GuardRefused) as caught:
            build(changed)
        self.assertEqual(caught.exception.reason_code, ReasonCode.CONFIGURATION_CHANGED)
        # A pending (not activated) configuration never drives a scheduled engine.
        progress = json.loads((self.store.path / en.PROGRESS).read_bytes())
        progress['phase'] = 'prepared'
        (self.store.path / en.PROGRESS).write_bytes(canonical_json(progress))
        with self.assertRaises(GuardRefused) as caught:
            build(settings)
        self.assertEqual(caught.exception.reason_code, ReasonCode.INITIALIZATION_REQUIRED)

    def test_latest_and_absent_pin_are_digest_verified_and_preserve_secret_bytes(self):
        for pin in (b'FUTURE_AGI_VERSION=latest  # pin\r\n', b''):
            with self.subTest(pin=pin):
                self.setUp()
                before = self.env_bytes.replace(b'FUTURE_AGI_VERSION=v1.51.0  # pin\r\n', pin)
                self.store.env_target.write_bytes(before)
                # This setup fixture stubs Docker only. Exercise the actual backup
                # config reader after normalization's real preserving envcommit.
                from deploy.update import standalone_backup
                for name in standalone_backup.CONFIG_FILES[1:]:
                    path = self.root / name
                    if not path.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(('fixture config ' + name).encode())
                        path.chmod(0o600)
                self.aliases = {}
                self.assertEqual(self.setup().state, 'active')
                after = self.store.env_target.read_bytes()
                self.assertEqual(self.store.env_target.stat().st_nlink, 2)
                self.assertEqual(standalone_backup._files(self.root)['.env'], after)
                from deploy.update.envfile import render_changes
                self.assertEqual(after, render_changes(before.decode(), {'FUTURE_AGI_VERSION': 'v1.51.0'}).encode())
                self.assertEqual(len(self.tags), 1)
                self.assertEqual(self.setup().state, 'active')
                self.assertEqual(len(self.tags), 1)

    def test_label_or_latest_digest_mismatch_never_normalizes_or_activates(self):
        self.store.env_target.write_bytes(b'SECRET=unchanged\nFUTURE_AGI_VERSION=latest\n')
        before = self.store.env_target.read_bytes()
        self.image_data['repo_digests'] = ['docker.io/futureagi/standalone@sha256:' + '0' * 64]
        with self.assertRaises(GuardRefused):
            self.setup()
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.store.env_target.read_bytes(), before)
        self.assertEqual(self.manager.effects, [])
        self.assertEqual(self.tags, [])

    def test_unknown_label_sourcebuild_arbitrary_pin_and_conflicting_alias_refuse(self):
        for case in ('label', 'source', 'pin', 'alias'):
            with self.subTest(case=case):
                self.setUp()
                if case == 'label':
                    self.image_data['version'] = 'unknown-build'
                if case == 'source':
                    self.source_build = True
                if case == 'pin':
                    self.store.env_target.write_bytes(b'FUTURE_AGI_VERSION=my-custom-tag\n')
                if case == 'alias':
                    self.aliases[next(iter(self.aliases))] = 'sha256:' + 'f' * 64
                before = self.store.env_target.read_bytes()
                with self.assertRaises(GuardRefused):
                    self.setup()
                self.assertEqual(self.store.env_target.read_bytes(), before)
                self.assertFalse(self.store.read_control().enabled_override)
                self.assertEqual(self.manager.effects, [])

    def test_predicted_config_mismatch_cannot_become_a_baseline(self):
        with mock.patch.object(self.host, 'render_config', return_value='f' * 64):
            with self.assertRaises(GuardRefused):
                self.setup()
        self.assertIsNone(self.store.read_state().baseline_head)
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.manager.effects, [])

    def test_pin_commit_rejects_concurrent_unrelated_env_change(self):
        self.store.env_target.write_bytes(b'FUTURE_AGI_VERSION=latest\nSECRET=original\n')
        original = en.envcommit.commit
        edited = b'FUTURE_AGI_VERSION=latest\nSECRET=operator-change\n'
        def concurrent(*args, **kwargs):
            self.store.env_target.write_bytes(edited)
            return original(*args, **kwargs)
        with mock.patch.object(en.envcommit, 'commit', side_effect=concurrent):
            with self.assertRaises((en.envcommit.CommitError, GuardRefused)):
                self.setup()
        self.assertEqual(self.store.env_target.read_bytes(), edited)
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.manager.effects, [])

    def test_missing_real_probe_remains_incomplete_with_no_pin_write(self):
        self.host.probe_missing = True
        self.store.env_target.write_bytes(b'FUTURE_AGI_VERSION=latest\n')
        with self.assertRaises(GuardRefused) as refused:
            self.setup()
        self.assertEqual(refused.exception.reason_code, ReasonCode.INITIALIZATION_REQUIRED)
        self.assertEqual(self.store.env_target.read_bytes(), b'FUTURE_AGI_VERSION=latest\n')
        self.assertIsNone(self.store.read_state().baseline_head)
        self.assertEqual(self.manager.effects, [])

    def test_private_destination_not_checkout_build_context_or_app_mount(self):
        for where in ('checkout', 'build', 'bind', 'symlink'):
            with self.subTest(where=where):
                self.setUp()
                destination = self.base / 'backups'
                if where == 'checkout':
                    destination = self.root / 'private-backups'
                if where == 'build':
                    self.model['services']['app']['build'] = {'context': str(self.base)}
                if where == 'bind':
                    self.model['services']['app']['volumes'] = [{'type': 'bind', 'source': str(self.base)}]
                if where == 'symlink':
                    destination.symlink_to(self.root, target_is_directory=True)
                with self.assertRaises((GuardRefused, RuntimeError, OSError)):
                    self.setup(backup_destination=str(destination))
                self.assertFalse(self.store.read_control().enabled_override)
                self.assertEqual(self.manager.effects, [])

    def test_missing_scheduler_authority_or_daemon_is_incomplete(self):
        self.manager.cron_running = False
        result = self.setup()
        self.assertEqual((result.state, result.scheduler, result.policy_state), ('incomplete', 'incomplete', 'disabled'))
        self.assertIn('cron', result.reason)
        self.assertIsNotNone(self.store.read_state().baseline_head)
        self.manager.cron_running = True
        self.assertEqual(self.setup().state, 'active')
        self.assertEqual(self.manager.effects.count('crontab'), 1)

    def test_control_generation_disable_pause_and_resume_races_win(self):
        for action in ('disable', 'pause', 'resume'):
            with self.subTest(action=action):
                self.setUp()
                self.host.after_install = lambda: self.store.change_control(action, en.now())
                result = self.setup()
                self.assertEqual(result.state, 'incomplete')
                self.assertEqual(result.reason, 'control_changed')
                self.assertFalse(self.store.read_control().enabled_override)
                self.assertEqual(result.scheduler, 'active')
                self.host.after_install = None
                self.assertNotEqual(self.setup().state, 'active')
                self.assertFalse(self.store.read_control().enabled_override)

    def test_disable_pause_resume_and_deactivate_are_different(self):
        self.setup()
        baseline = self.store.read_state().baseline_head
        self.store.change_control('pause', en.now())
        self.assertEqual(self.setup().policy_state, 'paused')
        self.store.change_control('disable', en.now())
        self.store.change_control('resume', en.now())
        inactive = self.setup()
        self.assertEqual(inactive.policy_state, 'disabled')
        self.assertEqual(inactive.code, 3)  # Never a success message for inactive selected apply.
        self.assertEqual(self.manager.effects.count('crontab'), 1)
        self.store.change_control('enable', en.now())
        self.assertEqual(self.setup().state, 'active')
        result = en.setup_automation(self.store, SimpleNamespace(deactivate=True, yes=True), host=self.host)
        self.assertEqual(result.state, 'deactivated')
        self.assertEqual(self.manager.tab, b'# unrelated \xff\r\n17 * * * * /usr/bin/true\n')
        self.assertEqual(self.store.read_state().baseline_head, baseline)
        self.assertEqual(self.setup().reason, 'scheduler_removed')
        with self.assertRaises(GuardRefused):
            en.load_setup_configuration(self.store)

    def test_rerun_after_real_baseline_B_commit_does_not_enroll_A(self):
        self.setup()
        # The shared cold-hold fixture records snapshot_at at wire-second
        # precision. Generate its source observation at that same instant, never
        # round a snapshot to BEFORE the observed source (the real guard refuses).
        self.time = self.time.replace(microsecond=0)
        self.baseline = BaselineStore(self.store, verifier=self.verifier, clock=lambda: self.time)
        self.head = self.baseline.current()
        self.obs = self.observation(self.a, self.env_bytes)
        with self.baseline.locked():
            intent, post = self.prepare()
            receipt = self.receipt(intent, post)
            successor_backup = self.host.backup(InstallationFixture(self), self.reg, post,
                                               en.load_setup_configuration(self.store))
            head = self.baseline.commit(intent, receipt_name=receipt, observation=post, backup_state=successor_backup)
        registration_before = self.store.read_registration()
        with mock.patch.object(BaselineStore, 'enroll', side_effect=AssertionError('no reenrollment')):
            self.assertEqual(self.setup().state, 'active')
        self.assertEqual(self.baseline.current(), head)
        self.assertEqual(head.release.version, 'v1.52.0')
        self.assertEqual(self.store.read_registration(), registration_before)

    def test_crashes_at_configuration_and_durable_setup_boundaries_retry_once(self):
        for target in (en.CONFIG, en.PROGRESS, 'registration.json', en.NORMALIZATION, 'state.json'):
            with self.subTest(target=target):
                self.setUp()
                original = self.store._write
                hit = [False]
                def crash(directory, name, value):
                    original(directory, name, value)
                    # For state.json interrupt AFTER baseline head, not initial registration state.
                    if name == target and not hit[0] and (target != 'state.json' or getattr(value, 'baseline_head', None)):
                        hit[0] = True
                        raise SimulatedCrash()
                with mock.patch.object(self.store, '_write', side_effect=crash):
                    with self.assertRaises(SimulatedCrash):
                        self.setup()
                self.assertTrue(hit[0])
                self.assertFalse(self.store.read_control().enabled_override)
                self.assertEqual(self.setup().state, 'active')
                self.assertEqual(self.manager.effects.count('crontab'), 1)

    def test_crash_after_scheduler_side_effect_reuses_ledger(self):
        from deploy.tests.test_update_scheduler_install import Crash
        self.manager.crash_after = 'crontab'
        with self.assertRaises(Crash):
            self.setup()
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.manager.effects.count('crontab'), 1)
        self.assertEqual(self.setup().state, 'active')
        self.assertEqual(self.manager.effects.count('crontab'), 1)

    def test_crash_before_or_after_final_enable_is_idempotent(self):
        for after in (False, True):
            with self.subTest(after=after):
                self.setUp()
                original = self.store.change_control
                def crash(action, *args, **kwargs):
                    if action == 'enable' and not after:
                        raise SimulatedCrash()
                    result = original(action, *args, **kwargs)
                    if action == 'enable':
                        raise SimulatedCrash()
                    return result
                with mock.patch.object(self.store, 'change_control', side_effect=crash):
                    with self.assertRaises(SimulatedCrash):
                        self.setup()
                self.assertEqual(self.setup().state, 'active')
                self.assertEqual(self.manager.effects.count('crontab'), 1)

    def test_each_host_prerequisite_failure_is_disabled_and_retryable(self):
        for boundary in ('open', 'discovery', 'observe', 'render', 'capacity', 'backup', 'install', 'inspect'):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.host.fail = boundary
                with self.assertRaises(SimulatedCrash):
                    self.setup()
                self.assertFalse(self.store.read_control().enabled_override)
                self.host.fail = None
                self.assertEqual(self.setup().state, 'active')
                self.assertEqual(self.manager.effects.count('crontab'), 1)

    def test_source_configuration_write_failure_never_enables(self):
        original = self.store._write
        def fail(directory, name, value):
            if name == en.CONFIG:
                raise OSError('synthetic disk full')
            return original(directory, name, value)
        with mock.patch.object(self.store, '_write', side_effect=fail):
            with self.assertRaises(StoreError):
                self.setup()
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.manager.effects, [])
        self.assertEqual(self.setup().state, 'active')

    def test_closed_bounded_nofollow_metadata_and_pinned_key(self):
        self.setup()
        path = self.store.path / en.CONFIG
        original = path.read_bytes()
        for corrupt in (original[:-2] + b',"extra":true}\n', b'{' + b' ' * 65536 + b'}', b'{"schema_version":1,"schema_version":1}'):
            path.write_bytes(corrupt)
            with self.assertRaises(StoreError):
                en.load_setup_configuration(self.store)
        path.write_bytes(original)
        path.unlink()
        external = self.base / 'foreign'
        external.write_bytes(original)
        path.symlink_to(external)
        with self.assertRaises((StoreError, OSError)):
            en.load_setup_configuration(self.store)
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o600)
        (self.store.path / en.KEY).write_bytes(b'changed key')
        with self.assertRaises(StoreError):
            en.load_setup_configuration(self.store)

    def test_unsafe_checkout_and_no_consent_do_not_mutate(self):
        for consent, mode in ((False, 0o700), (True, 0o777)):
            self.root.chmod(mode)
            with self.assertRaises((GuardRefused, StoreError)):
                self.setup(yes=consent)
            self.assertFalse((self.root / '.futureagi-updates').exists())
        self.root.chmod(0o700)

    def test_explicit_setting_changes_refuse_and_no_new_baseline_is_generated(self):
        self.setup()
        for changes in ({'window': '01:00-05:00'}, {'mode': 'notify'}, {'docker_context': 'elsewhere'},
                        {'release_source': 'https://example.invalid'}, {'check_interval': 300}):
            with self.subTest(changes=changes):
                with self.assertRaises(GuardRefused):
                    self.setup(**changes)
        self.assertEqual(self.manager.effects.count('crontab'), 1)

    def test_manual_never_opens_docker_or_installs_scheduler(self):
        self.host.fail = 'open'
        result = self.setup(mode='manual')
        self.assertEqual(result.state, 'manual')
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.manager.effects, [])

    def test_cli_run_constructs_generated_runtime_without_legacy_facts(self):
        self.setup()
        from deploy.update import __main__ as cli
        tick = SimpleNamespace(code=0, disposition='noop', reason_codes=(), skip_reason='not_due',
                               transaction_started=False, rollout_started=False, forward_result=None,
                               fallback_result=None, warnings=())
        with mock.patch.object(cli, 'HostRunner') as runner, mock.patch.object(cli, 'EnrolledFacts') as legacy, mock.patch('sys.stdout', new_callable=io.StringIO):
            runner.return_value.tick.return_value = tick
            self.assertEqual(cli.main(['--root', str(self.root), 'run']), 0)
            legacy.assert_not_called()
            self.assertEqual(runner.call_args.kwargs['settings'].automation_policy.mode, 'apply')
            self.assertIs(runner.call_args.kwargs['discovery_factory'], en.configured_discovery)
            self.assertIn('space_available', runner.call_args.kwargs)

    def test_enable_refuses_removed_or_incomplete_but_disable_still_works(self):
        self.manager.cron_running = False
        self.setup()
        from deploy.update import __main__ as cli
        with mock.patch('deploy.update.scheduler_install.inspect_activation', side_effect=self.host.inspect), mock.patch('sys.stdout', new_callable=io.StringIO), mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertNotEqual(cli.main(['--root', str(self.root), 'enable', '--yes']), 0)
            self.assertEqual(cli.main(['--root', str(self.root), 'disable']), 0)
        self.assertFalse(self.store.read_control().enabled_override)

    def test_revalidation_after_scheduler_rejects_env_drift_before_enable(self):
        self.host.after_install = lambda: self.store.env_target.write_bytes(self.env_bytes + b'UNRELATED=changed\n')
        with self.assertRaises((GuardRefused, StoreError)):
            self.setup()
        self.assertFalse(self.store.read_control().enabled_override)
        self.assertEqual(self.manager.effects.count('crontab'), 1)
        self.host.after_install = None
        self.store.env_target.write_bytes(self.env_bytes)
        self.assertEqual(self.setup().state, 'active')
        self.assertEqual(self.manager.effects.count('crontab'), 1)

    def test_foreign_systemd_state_is_not_adopted(self):
        del self.options.cron_service
        foreign = self.units / (self.name + '.service')
        foreign.write_bytes(b'foreign unit\n')
        foreign.chmod(0o600)
        result = self.setup(scheduler='systemd')
        self.assertEqual(result.state, 'incomplete')
        self.assertEqual(result.reason, 'scheduler_foreign_state')
        self.assertEqual(foreign.read_bytes(), b'foreign unit\n')
        self.assertFalse(self.store.read_control().enabled_override)

    def test_default_probe_does_not_synthesize_success(self):
        from deploy.update.standalone_state import StandaloneStateObserver
        with mock.patch.object(StandaloneStateObserver, 'observe', side_effect=GuardRefused(ReasonCode.INITIALIZATION_REQUIRED)) as probe:
            with self.assertRaises(GuardRefused):
                en.SetupHost().observe(InstallationFixture(self), self.reg, self.a, self.a.manifest['deployment'], self.options_policy)
            self.assertEqual(probe.call_args.kwargs['boundary'], 'enrollment')

    def test_default_host_missing_cosign_refuses_before_network(self):
        self.setup()
        config = en.load_setup_configuration(self.store)
        supervisor = SimpleNamespace(environment={})
        with mock.patch('deploy.update.enrollment.shutil.which', return_value=None), mock.patch('deploy.update.enrollment.http_get') as network:
            with self.assertRaises(Exception):
                en.make_discovery(self.store, config, self.reg, supervisor)
            network.assert_not_called()

    def test_https_only_source_and_defaults_are_unchanged(self):
        from deploy.update.settings import load_settings
        defaults = load_settings(self.root, environ={}).automation_policy
        self.assertEqual((defaults.mode, defaults.enabled, defaults.probe_timeout_seconds), ('manual', False, 60))
        del self.options.window
        config, _, _ = en.configuration(self.store, self.options, 'dedicated-context')
        self.assertIsNotNone(config.policy.maintenance_window)
        self.assertEqual(config.as_settings().get('FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW'), '02:00-06:00')
        for value in ('http://127.0.0.1:1234', 'https://user:password@example.invalid', 'https://example.invalid/path'):
            with self.subTest(value=value):
                with self.assertRaises((ValueError, GuardRefused)):
                    en.configuration(self.store, SimpleNamespace(**dict(vars(self.options), release_source=value)), 'dedicated-context')


class SetupCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([str(ROOT / 'bin/update'), *args], cwd=str(ROOT),
            env=dict(os.environ, PYTHON=sys.executable, PATH='/usr/bin:/bin'),
            text=True, capture_output=True, timeout=30)

    def test_actual_help_subprocess_and_new_option_leakage(self):
        result = self.run_cli('setup', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for word in ('--mode', '--yes', '--deactivate', '--scheduler', '--backup-destination', '--release-source', '--ca-file'):
            self.assertIn(word, result.stdout)
        for args in (('setup',), ('setup', '--mode', 'apply', '--deactivate'),
                     ('setup', '--mode', 'apply', '--check-interval', '1'),
                     ('setup', '--deactivate', '--window', 'always'),
                     ('setup', '--mode', 'apply', '--window', ''),
                     ('check', '--mode', 'apply'), ('--ca-file', '/no/key', 'run'),
                     ('apply', '--scheduler', 'cron'), ('--release-source', 'https://example.invalid', 'status')):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)
        for args in (('--mode', 'apply', 'setup', '--yes'), ('setup', '--mode', 'notify', '--yes'),
                     ('setup', '--deactivate', '--yes')):
            from deploy.update.__main__ import build_parser
            self.assertEqual(build_parser().parse_args(args).command, 'setup')

    def test_actual_subprocess_missing_docker_and_compose_are_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / '.env').write_text('FUTURE_AGI_VERSION=latest\n')
            (root / 'docker-compose.yml').write_text('services: {}\n')
            binpath = root / 'bin'
            binpath.mkdir()
            for name, source in (('bash', '/bin/bash'), ('dirname', '/usr/bin/dirname')):
                (binpath / name).symlink_to(source)
            update = binpath / 'update'
            update.write_text('#!/bin/sh\nexit 99\n')
            update.chmod(0o700)
            environment = dict(os.environ, PYTHON=sys.executable, PATH=str(binpath))
            for compose_missing in (False, True):
                if compose_missing:
                    docker = binpath / 'docker'
                    docker.write_text('#!/bin/sh\n# Finite missing-Compose fixture, no daemon\nexit 1\n')
                    docker.chmod(0o700)
                result = subprocess.run([str(ROOT / 'bin/update'), '--root', str(root), 'setup', '--mode', 'notify',
                    '--docker-context', 'private-fixture', '--yes'], env=environment, cwd=str(ROOT),
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn('Setup incomplete:', result.stdout)
                self.assertNotIn('Setup active;', result.stdout)
                self.assertFalse(RunnerStateStore(root, root / '.env').read_control().enabled_override)

    def test_legacy_prefixes_and_full_diagnostics_stay_intact(self):
        from deploy.update.__main__ import build_parser
        parser = build_parser()
        self.assertEqual(parser.parse_args(['--man', 'x', 'check']).manifest, 'x')
        self.assertTrue(parser.parse_args(['apply', '--sta']).stage)
        self.assertEqual(parser.parse_args(['--ba', '/backup', 'apply']).backup_command, '/backup')
        result = self.run_cli('rollback', '--window', 'always')
        self.assertEqual(result.returncode, 2)
        self.assertIn('not allowed with rollback', result.stderr)

    def test_default_context_is_observed_not_forced_to_default(self):
        with mock.patch.dict(os.environ, {'PATH': '/private-fixture'}, clear=True), mock.patch('deploy.update.enrollment.shutil.which', return_value='/private-fixture/docker'), mock.patch('deploy.update.enrollment.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='chosen-context\n')) as run:
            self.assertEqual(en.SetupHost().context(SimpleNamespace()), 'chosen-context')
            self.assertEqual(run.call_args.args[0], ['/private-fixture/docker', 'context', 'show'])
            self.assertNotIn('DOCKER_CONTEXT', run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.kwargs['timeout'], 15)
        with mock.patch.dict(os.environ, {'DOCKER_HOST': 'tcp://private-fixture:1234'}, clear=True), mock.patch('deploy.update.enrollment.subprocess.run') as run:
            with self.assertRaises(GuardRefused):
                en.SetupHost().context(SimpleNamespace())
            run.assert_not_called()

    def test_decline_is_noop_before_adapters(self):
        from deploy.update.__main__ import _setup
        store = SimpleNamespace(selection_hash='1' * 64)
        with mock.patch('sys.stdin.isatty', return_value=False), mock.patch('deploy.update.enrollment.setup_summary', return_value='Private fixture setup'), mock.patch('deploy.update.enrollment.setup_automation') as setup, mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(_setup(SimpleNamespace(mode='apply', yes=False), store), 3)
            setup.assert_not_called()

    def test_installer_actual_help_and_invalid_optin_grammar_before_docker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'bin').mkdir()
            shutil.copy2(ROOT / 'bin/install', root / 'bin/install')
            shutil.copytree(ROOT / 'bin/lib', root / 'bin/lib')
            marker = root / 'unexpected-docker'
            docker = root / 'bin/docker'
            docker.write_text('#!/bin/sh\n: > "$DOCKER_MARKER"\nexit 97\n')
            docker.chmod(0o700)
            env = dict(os.environ, PATH=str(root / 'bin') + ':/usr/bin:/bin', DOCKER_MARKER=str(marker))
            for args, code in ((('--help',), 0), (('--auto-update',), 1),
                               (('--auto-update', 'invalid'), 1),
                               (('--auto-update', 'apply', '--no-up'), 1),
                               (('--auto-update', 'apply', '--from-source'), 1),
                               (('--auto-update', 'apply', '--distributed'), 1),
                               (('--host-status-help',), 0),
                               (('--prepare-host-status', '--auto-update', 'apply'), 2)):
                with self.subTest(args=args):
                    run = subprocess.run(['/bin/bash', str(root / 'bin/install'), *args], cwd=str(root),
                                         env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(run.returncode, code, run.stdout + run.stderr)
                    if args == ('--help',):
                        self.assertIn('--auto-update apply', run.stdout)
                    self.assertFalse(marker.exists())

    def test_installer_default_off_explicit_flag_and_no_double_prompt(self):
        installer = (ROOT / 'bin/install').read_text()
        block = installer.split('# ---------------- one-time automatic-update opt-in ----------------\n', 1)[1]
        block = block.split('# ---------------- done ----------------', 1)[0]
        self.assertGreater(installer.index('# ---------------- one-time automatic-update'), installer.index('App container stable'))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'bin').mkdir()
            update = root / 'bin/update'
            update.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CALLS"\nexit "$RESULT"\n')
            update.chmod(0o700)
            calls = root / 'calls'
            for mode, outcome in (('', '0'), ('apply', '0'), ('download', '3')):
                if calls.exists():
                    calls.unlink()
                env = dict(os.environ, ROOT=str(root), CALLS=str(calls), RESULT=outcome, AUTO_UPDATE_MODE=mode)
                script = 'NON_INTERACTIVE=1; DISTRIBUTED=0; FROM_SOURCE=0; say(){ :; }; ok(){ :; }; warn(){ :; };\n' + block + '\nexit "$AUTO_UPDATE_RC"\n'
                run = subprocess.run(['/bin/bash', '-c', script], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, int(outcome))
                if mode:
                    self.assertEqual(calls.read_text().splitlines(), ['setup', '--mode', mode, '--yes'])
                else:
                    self.assertFalse(calls.exists())



class SetupCapacityReasonTests(unittest.TestCase):
    """Setup tells an unmeasurable image store apart from a measured shortage."""

    def capacity(self, available):
        policy = SimpleNamespace(probe_timeout_seconds=90)
        with mock.patch('deploy.update.docker_host.observe_daemon_storage', return_value=available) as observe:
            en.SetupHost.capacity(object.__new__(en.SetupHost), 'installation', 'registration',
                                  'sha256:' + 'd' * 64, policy)
        observe.assert_called_once_with('installation', 'registration', helper_image_id='sha256:' + 'd' * 64,
                                        timeout=90)

    def test_unknown_capacity_and_a_measured_shortage_refuse_with_different_codes(self):
        for available, code in ((None, ReasonCode.RESTORE_CAPACITY_UNKNOWN),
                                (64 * 1024 * 1024 - 1, ReasonCode.DISK_SPACE_INSUFFICIENT)):
            with self.subTest(available=available), self.assertRaises(GuardRefused) as raised:
                self.capacity(available)
            self.assertEqual(raised.exception.reason_code, code)
        self.capacity(64 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
