"""Host facts refuse unknowns; no real daemon, SSH or filesystem qualification mock."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests import test_update_public_status as pub  # noqa: E402
from deploy.tests.test_update_compatibility import synthetic_context  # noqa: E402
from deploy.update import host_facts as facts  # noqa: E402
from deploy.update.compatibility import topology_profile  # noqa: E402
from deploy.update.contracts import ReasonCode  # noqa: E402
from deploy.update.orchestrator import GuardRefused  # noqa: E402


class FactsTests(pub.PublisherCase):
    def setUp(self):
        super().setUp()
        prepared, self.obs, _, _ = synthetic_context()
        self.prepared = prepared
        self.layout = topology_profile('standalone', ())
        self.document = {'schema_version': 1, 'installation_fingerprint': self.binding.installation_fingerprint,
            'config_fingerprint': 'd'*64, 'profile_fingerprint': 'e'*64, 'deployment_fingerprint': 'f'*64,
            'predecessor_sha256': hashlib.sha256(self.obs.predecessor_raw).hexdigest(),
            'bundle_sha256': hashlib.sha256(b'bundle').hexdigest(),
            'proof_sha256': hashlib.sha256(self.obs.release_proof).hexdigest(),
            'state_epochs': dict(self.obs.state_epochs), 'persistence': dict(self.obs.persistence),
            'unchanged_state_attested': True, 'no_pending_migrations_attested': True}
        for name, raw in (('predecessor.json', self.obs.predecessor_raw), ('predecessor.bundle', b'bundle'),
                          ('compatibility.json', self.obs.release_proof)):
            (self.store.path/name).write_bytes(raw)
            (self.store.path/name).chmod(0o600)
        self.write_document()
        self.inst = mock.Mock(root=self.root, env={'SECRET': 'private'}, environ={})
        self.inst.service_model.return_value = {'complete': True}
        images = dict(self.obs.product_images, **self.obs.dependency_images)
        self.services = {s: {'image': values[0].image} for s, values in images.items()}
        self.inst.config.return_value = {'services': self.services}
        self.containers = [{'service': s, 'image': values[0].image, 'image_id': 'sha256:'+str(i)*64,
                            'status': 'running'} for i, (s, values) in enumerate(images.items(), 1)]
        self.inst.containers.return_value = self.containers
        self.metadata = {c['image_id']: {'id': c['image_id'], 'repo_digests': [
            facts.parse_image_ref(c['image']).name+'@'+images[c['service']][0].digest]} for c in self.containers}
        self.inst.image.side_effect = self.metadata.get
        self.inst.docker_run.return_value.stdout = json.dumps({'OSType': 'linux', 'Architecture': 'x86_64'})
        self.verifier = mock.Mock()
        self.verifier.verify.return_value = self.obs.predecessor_verification
        self.verifier.trust_fingerprint.return_value = '1'*64
        self.adapter = facts.EnrolledFacts(self.store, clock=lambda: pub.NOW,
                                           verifier_factory=lambda *args, **kwargs: self.verifier)
        for name, kwargs in (('check_binding', {}), ('fingerprints', {'return_value': ('d'*64, 'e'*64, 'f'*64)}),
                             ('hash_path', {'return_value': 'a'*64})):
            patcher = mock.patch.object(facts, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_document(self):
        (self.store.path/'facts.json').write_text(json.dumps(self.document))
        (self.store.path/'facts.json').chmod(0o600)

    def assert_refused(self):
        with self.assertRaises(GuardRefused) as caught:
            self.adapter(self.inst, 'before_backup')
        self.assertIn(caught.exception.reason_code, set(ReasonCode))
        return caught.exception.reason_code

    def test_complete_attestation_observes_all_replicas_and_private_facts(self):
        before = {p.name: p.read_bytes() for p in self.store.path.iterdir() if p.is_file()}
        result = self.adapter(self.inst, 'before_backup')
        self.assertTrue(result.baseline_attested and result.state_unchanged and result.runtime_complete)
        self.assertEqual(result.platform, 'linux/amd64')
        self.assertEqual(result.predecessor_raw, self.obs.predecessor_raw)
        self.assertEqual(result.release_proof, self.obs.release_proof)
        self.assertEqual(result.env, {'SECRET': 'private'})
        self.assertEqual(set(result.product_images), {'app'})
        self.assertEqual(set(result.dependency_images), {'postgres', 'clickhouse'})
        self.assertFalse(result.online_verified, 'the independent guard owns online target verification')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.path.iterdir() if p.is_file()})

    def test_missing_unknown_or_unattested_state_never_becomes_permission(self):
        for key, value in (('unchanged_state_attested', False), ('no_pending_migrations_attested', False),
                           ('schema_version', True), ('extra', True), ('state_epochs', {}),
                           ('state_epochs', dict(self.obs.state_epochs, redis=True)), ('persistence', {}),
                           ('config_fingerprint', '0'*64), ('predecessor_sha256', '0'*64)):
            old = dict(self.document)
            with self.subTest(key=key, value=value):
                self.document[key] = value
                self.write_document()
                self.assert_refused()
                self.document = old
        (self.store.path/'facts.json').unlink()
        self.assertEqual(self.assert_refused(), ReasonCode.INITIALIZATION_REQUIRED)

    def test_private_evidence_rejects_links_permissions_bounds_and_duplicates(self):
        path = self.store.path/'facts.json'
        path.chmod(0o644)
        self.assert_refused()
        self.write_document()
        path.write_bytes(b'x'*65537)
        self.assert_refused()
        path.write_bytes(b'{"schema_version":1,"schema_version":1}')
        self.assert_refused()
        path.unlink()
        path.symlink_to(self.env)
        self.assert_refused()

    def test_signature_change_and_verification_failure_refuse(self):
        self.verifier.verify.return_value = SimpleNamespace(verified=False)
        self.assertEqual(self.assert_refused(), ReasonCode.SIGNATURE_REJECTED)
        def swap(_fetched):
            (self.store.path/'predecessor.bundle').write_bytes(b'changed')
            return self.obs.predecessor_verification
        self.verifier.verify.side_effect = swap
        self.assertEqual(self.assert_refused(), ReasonCode.IMMUTABLE_RELEASE_CHANGED)

    def test_runtime_graph_missing_replica_digest_and_status_refuse(self):
        self.inst.service_model.return_value = {'complete': False}
        self.assert_refused()
        self.inst.service_model.return_value = {'complete': True}
        self.services['app']['build'] = '.'
        self.assert_refused()
        del self.services['app']['build']
        self.inst.containers.return_value = self.containers[1:]
        self.assert_refused()
        self.inst.containers.return_value = self.containers
        for field, value in (('status', 'restarting'), ('service', 'foreign'), ('image_id', 'unknown')):
            original = self.containers[0][field]
            self.containers[0][field] = value
            self.assert_refused()
            self.containers[0][field] = original
        self.metadata[self.containers[0]['image_id']]['repo_digests'] = []
        self.assert_refused()

    def test_default_clock_and_verifier_lookup_use_registered_path(self):
        self.assertIsNotNone(facts.now().utcoffset())
        def verifier(settings, **kwargs):
            self.assertTrue(kwargs['host_only'])
            with mock.patch('shutil.which', return_value='/cosign') as which:
                self.assertEqual(kwargs['which']('cosign'), '/cosign')
                self.assertEqual(which.call_args.kwargs['path'], self.store.read_registration().launcher.path)
            return self.verifier
        facts.EnrolledFacts(self.store, verifier_factory=verifier)(self.inst, 'after_backup')


class SelectionTests(pub.PublisherCase):
    def test_selection_is_local_and_registration_facts_are_not_approval(self):
        with mock.patch.object(facts, 'Installation', wraps=facts.Installation) as constructor:
            store = facts.selected_store(self.root)
        constructor.assert_not_called()
        self.assertEqual(store.selection_hash, self.store.selection_hash)
        with self.assertRaises(GuardRefused):
            facts.selected_store(self.root, env_file=self.root/'wrong')
        (self.root/'deploy').mkdir()
        (self.root/'deploy/.env.production').write_text('X=1\n')
        self.assertTrue(facts.selected_store(self.root, production=True).production)

    def test_executor_inventory_matches_producer_and_never_borrows_proof_digest(self):
        from scripts.build_update_compatibility_proof import implementation_inventory, inventory_hash
        names = ['scripts/rehearse_update_compatibility.py', 'scripts/build_update_compatibility_proof.py',
            'scripts/generate_update_manifest.py', 'scripts/update_rehearsal_shipping.py',
            'scripts/update_rehearsal_milestone.py', 'scripts/update_rehearsal_qualification.py',
            'scripts/update_rehearsal_resources.py', 'scripts/update_rehearsal_spool.py', 'bin/update',
            'deploy/standalone/bin/start',
            'deploy/standalone/bin/bootstrap.py', 'deploy/standalone/nginx.conf',
            'deploy/update/fixed.py', 'deploy/update/probes/fixed.py', 'deploy/update/mapping.json']
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixed installed data: ' + name)
        digest = facts.executor_implementation_sha256(self.root)
        self.assertEqual(digest, inventory_hash(implementation_inventory(self.root)))
        ignored = self.root / 'deploy/update/__pycache__/untrusted.py'
        ignored.parent.mkdir()
        ignored.write_text('not part of shipped closure')
        self.assertEqual(facts.executor_implementation_sha256(self.root), digest)
        changed = self.root / 'deploy/update/probes/fixed.py'
        changed.write_text('changed fixed observer')
        self.assertNotEqual(facts.executor_implementation_sha256(self.root), digest)
        self.assertEqual(facts.executor_implementation_sha256(self.root), inventory_hash(implementation_inventory(self.root)))
        changed.unlink()
        changed.symlink_to(self.env)
        with self.assertRaises(GuardRefused):
            facts.executor_implementation_sha256(self.root)
        changed.unlink()
        with changed.open('wb') as handle:
            handle.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaises(GuardRefused):
            facts.executor_implementation_sha256(self.root)

    def test_binding_reuses_public_token_but_never_repairs_corrupt_registration(self):
        installation = mock.Mock(root=self.root, env_file=self.env, env_alias=self.env, env={}, project='test')
        installation._ownership_files.return_value = [self.compose]
        installation.docker_run.return_value.stdout = 'test-daemon'
        binding = facts.installation_binding(installation, self.store, 'test-context')
        self.assertEqual(binding.slot_id, self.binding.slot_id)
        path = self.store.path/'registration.json'
        path.write_text('{}')
        with self.assertRaises(facts.StoreError):
            facts.installation_binding(installation, self.store, 'test-context')
        self.assertEqual(path.read_text(), '{}')

    def test_safe_paths_reject_symlink_parent_mode_relative_and_nondirectory(self):
        self.assertEqual(facts.safe_path(self.root, directory=True), self.root)
        link = self.root/'link'
        link.symlink_to(self.env)
        for path, directory in ((link, False), (Path('relative'), False), (self.env, True)):
            with self.assertRaises(GuardRefused):
                facts.safe_path(path, directory=directory)
        self.env.chmod(0o666)
        with self.assertRaises(GuardRefused):
            facts.safe_path(self.env)


class OverlayAdapterTests(pub.PublisherCase):
    def test_fixed_inventory_missing_malformed_unsafe_and_nondirectory_refuse(self):
        overlay = self.root / facts.STATUS_OVERLAY
        overlay.parent.mkdir()
        inventory = self.root / 'deploy/update/deployment-files.txt'
        inventory.parent.mkdir()
        for content in (None, '../escape\n', 'docker-compose.yml\n', facts.STATUS_OVERLAY+'\n'):
            if content is not None:
                inventory.write_text(content)
            with self.assertRaises(GuardRefused):
                facts.production_status_overlay(self.root)
        overlay.mkdir()
        with self.assertRaises(GuardRefused):
            facts.production_status_overlay(self.root)
        overlay.rmdir()
        overlay.symlink_to(self.compose)
        with self.assertRaises(GuardRefused):
            facts.production_status_overlay(self.root)
        overlay.unlink()
        overlay.write_text('services: {}\n')
        self.assertEqual(facts.production_status_overlay(self.root), overlay)
        self.assertIsNone(facts.binding_status_overlay(self.binding))
        self.assertIsNone(facts.registered_status_overlay(self.store))
        from deploy.update import __main__ as cli
        with self.assertRaises(GuardRefused):
            cli._enroll(self.store, 'test-context', None, status_overlay=True)

    def test_checked_discovery_refresh_and_no_candidate_or_unverified_result(self):
        from deploy.update.discovery import DiscoveryResult
        from dataclasses import replace
        discovery = mock.Mock()
        with mock.patch('deploy.update.runner.host_discovery', return_value=discovery), \
                mock.patch.object(facts, 'check_status_deployment') as check:
            adapter = facts.overlay_discovery(self.store, None, None, None, None)
            discovery.refresh.return_value = DiscoveryResult(self.release)
            self.assertIs(adapter.refresh('v1.2.3', channel='stable').candidate, self.release)
            discovery.refresh.assert_called_once_with('v1.2.3', channel='stable')
            check.assert_called_once()
            check.reset_mock()
            for result in (DiscoveryResult(), DiscoveryResult(replace(self.release,
                           verification=replace(self.release.verification, verified=False)))):
                discovery.discover.return_value = result
                self.assertIs(adapter.discover(current='v1.0.0'), result)
            check.assert_not_called()
        with self.assertRaises(GuardRefused) as caught:
            facts.check_status_deployment(self.root, {'deployment': {'files': {}}})
        self.assertEqual(caught.exception.reason_code, ReasonCode.DEPLOYMENT_FILES_MISMATCH)


class DockerContextSpellingTests(unittest.TestCase):
    """Review round 2: one spelling rule for the registered context, applied before a supervisor exists."""

    def test_accepts_ordinary_context_names(self):
        for name in ('default', 'colima-fagi-update', 'desktop-linux', 'my.ctx_1', 'ünï', 'a' * 200):
            with self.subTest(name=name):
                self.assertTrue(facts.valid_docker_context(name))

    def test_rejects_empty_whitespace_control_and_non_text(self):
        for name in ('', ' ', 'a b', 'a\tb', 'a\nb', 'a\rb', 'a\x00b', '\x1b[0m', '\x7f', 'c1\x85', 'nbsp\xa0',
                     'em\u2003space', 'line\u2028sep', 'bell\x07', None, 7, b'ctx', ['ctx']):
            with self.subTest(name=name):
                self.assertFalse(facts.valid_docker_context(name))

    def test_installation_binding_applies_the_same_rule_before_touching_docker(self):
        installation = mock.Mock()
        for name in ('', 'a b', '\x85'):
            with self.subTest(name=name), self.assertRaises(GuardRefused) as caught:
                facts.installation_binding(installation, mock.Mock(), name)
            self.assertEqual(caught.exception.reason_code, ReasonCode.OWNERSHIP_UNCONFIRMED)
        installation.check_ownership.assert_not_called()
        installation.docker_run.assert_not_called()


class CapacityTests(unittest.TestCase):
    def test_remote_vm_or_unknown_capacity_never_uses_client_disk_or_connects(self):
        inst = mock.Mock(environ={'DOCKER_CONTEXT': 'registered'})
        binding = SimpleNamespace(docker_context='registered', daemon_identity='daemon')
        with mock.patch.object(facts.sys, 'platform', 'darwin'), mock.patch.object(facts.os, 'statvfs') as usage:
            self.assertIsNone(facts.daemon_space_available(inst, binding))
            usage.assert_not_called()
            inst.docker_run.assert_not_called()
        for response in ('{}', '[]', 'not JSON', '[{"Name":"wrong"}]',
                         '[{"Name":"registered","Endpoints":{"docker":{"Host":"ssh://host"}}}]'):
            inst.docker_run.return_value.stdout = response
            with mock.patch.object(facts.sys, 'platform', 'linux'), mock.patch.object(facts.socket, 'socket') as sock:
                self.assertIsNone(facts.daemon_space_available(inst, binding))
                sock.assert_not_called()

    def test_native_peer_and_classic_overlay2_capacity_not_checkout_usage(self):
        inst = mock.Mock(environ={'DOCKER_CONTEXT': 'registered'})
        binding = SimpleNamespace(docker_context='registered', daemon_identity='daemon')
        context = [{'Name': 'registered', 'Endpoints': {'docker': {'Host': 'unix:///socket'}}}]
        info = {'ID': 'daemon', 'DockerRootDir': '/native/docker', 'Driver': 'overlay2'}
        inst.docker_run.side_effect = lambda *args: SimpleNamespace(stdout=json.dumps(context if args[0]=='context' else info))
        with mock.patch.object(facts.sys, 'platform', 'linux'), \
                mock.patch.object(facts.socket, 'SO_PEERCRED', 17, create=True), \
                mock.patch.object(facts.socket, 'socket') as sock, \
                mock.patch.object(Path, 'read_text', return_value='dockerd\n'), \
                mock.patch.object(facts.os, 'readlink', return_value='mnt:[1]'), \
                mock.patch.object(facts.os, 'stat', return_value='same-root'), \
                mock.patch.object(facts, 'safe_path', side_effect=lambda path, **kwargs: path), \
                mock.patch.object(facts.os, 'statvfs', return_value=SimpleNamespace(f_bavail=123, f_frsize=4096)) as usage:
            sock.return_value.__enter__.return_value.getsockopt.return_value = struct.pack('3i', 123, 0, 0)
            self.assertEqual(facts.daemon_space_available(inst, binding), 123*4096)
            usage.assert_called_once_with(Path('/native/docker/overlay2'))
            for field, value in (('ID', 'wrong'), ('Driver', 'overlayfs'), ('DockerRootDir', 'relative'),
                                 ('DriverStatus', [['driver-type', 'io.containerd.snapshotter.v1']])):
                old = dict(info)
                info[field] = value
                self.assertIsNone(facts.daemon_space_available(inst, binding))
                info.clear()
                info.update(old)
            sock.return_value.__enter__.return_value.connect.side_effect = OSError('private')
            self.assertIsNone(facts.daemon_space_available(inst, binding))
