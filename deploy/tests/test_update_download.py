"""Download-only cache operation; synthetic signed inputs, real env/state files."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.test_update_apply import UpdaterCase, TARGET, id_of  # noqa: E402
from deploy.tests.test_update_compatibility import prepare, default_v2  # noqa: E402
from deploy.update.contracts import ReasonCode  # noqa: E402


class DownloadTests(UpdaterCase):
    def setup_download(self):
        from deploy.update.download import Downloader
        self.prepared = replace(prepare(default_v2(TARGET)), plan=self.plan())
        manifest = default_v2(TARGET)
        for item in self.plan()['components']:
            manifest['components'][item['component']]['image'] = item['target_image']
            manifest['components'][item['component']]['digest'] = item['target_digest']
        self.prepared = replace(prepare(manifest), plan=self.plan())
        if self.inst.production:
            self.prepared = replace(self.prepared, binding=replace(self.prepared.binding,
                                                                   topology='distributed', production=True))
        original = self.inst
        def fresh():
            clone = copy.copy(original)
            clone.env = dict(original.env)
            clone.env_overlay = {}
            clone.resolved_images = lambda: {
                service: f'{repo}:{clone.env.get(variable) or default}'
                for service, repo, variable, default in clone.specs}
            return clone
        self.inst.fresh = fresh
        self.downloader = Downloader(self.inst, self.state, space_available=lambda: 10**12)
        self.patches = [mock.patch('deploy.update.download.check_binding'),
                        mock.patch('deploy.update.download.check_deployment'),
                        mock.patch('deploy.update.download.fingerprints', return_value=(
                            self.prepared.config_fingerprint, self.prepared.profile_fingerprint,
                            self.prepared.deployment_fingerprint))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_success_only_pulls_images_and_preserves_env_inode_and_running_containers(self):
        self.setup_download()
        before = self.env_path.stat()
        running = copy.deepcopy(self.inst.running)
        receipt = self.downloader.download(self.prepared)
        after = self.env_path.stat()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(self.inst.running, running)
        self.assertEqual(self.inst.env_overlay, {})
        self.assertEqual(self.hook.calls, [])
        self.assertIsNone(self.state.read_journal())
        self.assertEqual(self.state.receipts(), [])
        self.assertFalse(list(self.state.path.glob('env-retained-*')))
        self.assertEqual(receipt.schema_version, 1)
        self.assertEqual([c[0] for c in self.compose_calls()], ['pull'])
        self.assertTrue(self.downloader.reusable(self.prepared, receipt))

    def test_wrong_digest_or_interruption_never_creates_transaction(self):
        from deploy.update.orchestrator import GuardRefused
        for failure in ('digest', 'interrupt'):
            with self.subTest(failure=failure):
                self.setup_download()
                if failure == 'digest':
                    self.inst.metadata[id_of('futureagi/standalone', TARGET)]['repo_digests'] = []
                else:
                    self.inst.failures['pull'] = KeyboardInterrupt()
                    self.inst.local_images.pop('futureagi/standalone:' + TARGET, None)
                with self.assertRaises((GuardRefused, KeyboardInterrupt)):
                    self.downloader.download(self.prepared)
                self.assertEqual(self.env_bytes(), self.original)
                self.assertIsNone(self.state.read_journal())
                self.assertEqual(self.state.receipts(), [])

    def test_eviction_changed_trust_and_binding_cannot_reuse_receipt(self):
        self.setup_download()
        receipt = self.downloader.download(self.prepared)
        self.assertFalse(self.downloader.reusable(replace(self.prepared, trust_fingerprint='2'*64), receipt))
        binding = replace(self.prepared.binding, installation_fingerprint='4'*64)
        self.assertFalse(self.downloader.reusable(replace(self.prepared, binding=binding), receipt))
        self.inst.local_images.pop('futureagi/standalone:' + TARGET)
        self.assertFalse(self.downloader.reusable(self.prepared, receipt))

    def test_pending_journal_and_missing_space_fail_before_pull(self):
        from deploy.update.orchestrator import GuardRefused
        self.setup_download()
        self.downloader.space_available = lambda: 0
        with self.assertRaises(GuardRefused) as caught:
            self.downloader.download(self.prepared)
        self.assertEqual(caught.exception.reason_code, ReasonCode.DISK_SPACE_INSUFFICIENT)
        self.state.write_journal({'stage': 'prepare'})
        with self.assertRaises(GuardRefused) as caught:
            self.downloader.download(self.prepared)
        self.assertEqual(caught.exception.reason_code, ReasonCode.RECOVERY_REQUIRED)
        self.assertEqual(self.compose_calls(), [])

    def test_plan_cannot_substitute_an_unsigned_digest(self):
        from deploy.update.orchestrator import GuardRefused
        self.setup_download()
        plan = self.plan()
        plan['components'][0]['target_digest'] = 'sha256:' + 'e'*64
        with self.assertRaises(GuardRefused):
            self.downloader.download(replace(self.prepared, plan=plan))
        self.assertEqual(self.compose_calls(), [])

    def test_production_download_does_not_stage_or_initialize(self):
        self.build(mode='production')
        self.setup_download()
        receipt = self.downloader.download(self.prepared)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertTrue(receipt.images)
        self.assertTrue(all(call[0] == 'pull' for call in self.compose_calls()))

    def test_download_receipt_is_private_and_roundtrips(self):
        self.setup_download()
        receipt = self.downloader.download(self.prepared)
        saved = self.downloader.load(self.prepared)
        self.assertEqual(saved, receipt)
        raw = self.state.read_private('download', receipt.identity)
        self.assertNotIn('env', raw)
        self.assertNotIn('config', raw)
        self.assertEqual(json.loads(json.dumps(raw)), raw)
