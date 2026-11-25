"""Baseline transaction tests, NOT product-release certification.

Structural/runtime/backup observations here are deterministic synthetic fixtures.
Manifest signatures are REAL, using the existing host Verifier and local cosign
public-key mode, offline and without publication. Set FUTURE_AGI_UPDATE_TEST_COSIGN
when cosign v3+ is not on PATH. No accept-all verifier or keyless snapshot is used.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest import mock

from deploy.tests.test_update_compatibility import (NOW, bind_proof, default_v2, runtime_images,
                                                   stamp, synthetic_release, restore_release)
from deploy.tests.test_update_runner_state import registration
from deploy.tests.test_update_trust import raw_settings
from deploy.update.baseline import BaselineStore, StructuralObservation, sha
from deploy.update.compatibility import (CompatibilityError, Observations, RuntimeImage, authorize_execution,
                                        topology_profile, validate_release_proof)
from deploy.update.contracts import (BackupSeal, ColdHold, RuntimeObservationV2, ReasonCode, VerificationSnapshot,
                                     installation_binding_hash)
from deploy.update.discovery import DiscoveredRelease
from deploy.update.envfile import render_changes
from deploy.update.httpcache import HttpCache
from deploy.update.manifest import canonical_json, manifest_hash
from deploy.update.receipt import StateDir
from deploy.update.runner_state import RunnerStateStore, StaleGeneration, StoreBusy, StoreError, TargetIdentity
from deploy.update.trust import FetchedManifest, Verifier


class SimulatedCrash(BaseException):
    """Do not mistake a sanitized I/O refusal for the intended crash boundary."""


class BaselineCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cosign = os.environ.get("FUTURE_AGI_UPDATE_TEST_COSIGN") or shutil.which("cosign")
        if not cls.cosign:
            raise RuntimeError("real signature tests require host cosign v3+ (FUTURE_AGI_UPDATE_TEST_COSIGN)")
        cls.temporary = tempfile.TemporaryDirectory(prefix="baseline-signatures-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.keys = Path(cls.temporary.name).resolve()
        cls.sign_env = dict(os.environ, COSIGN_PASSWORD="")
        subprocess.run([cls.cosign, "generate-key-pair"], cwd=str(cls.keys), env=cls.sign_env,
                       capture_output=True, check=True, timeout=30)
        target, previous_raw, proof, _ = restore_release()
        # Independently measured tables; repeated-table input/output growth is
        # deliberately notify-only under the A6 estimator.
        for index, measurement in enumerate(proof['predecessors'][0]['migration_measurements']):
            measurement['tables'][0]['table'] = 'public.synthetic_table_%d' % index
        cls.previous = json.loads(previous_raw)
        # Contract 2 retains A's exact execution layout despite B metadata churn.
        target["compatibility"]["contract_version"] = 2
        row = target["compatibility"]["predecessors"][0]
        row["execution_deployment_hash"] = manifest_hash(cls.previous["deployment"])
        target["deployment"]["files"][".env.example"] = "d" * 64
        proof["schema_version"] = 2
        proof["deployment"] = copy.deepcopy(target["deployment"])
        proof["predecessors"][0]["predecessor"] = copy.deepcopy(row)
        proof["predecessors"][0]["execution_deployment"] = copy.deepcopy(cls.previous["deployment"])
        cls.b_proof = bind_proof(target, proof)
        cls.b = target
        cls.c = copy.deepcopy(target)
        cls.c["version"] = "v1.53.0"
        cls.c["helm"]["version"] = "1.53.0"
        for component in cls.c["components"].values():
            component["image"] = component["image"].replace("v1.52.0", "v1.53.0")
        cls.c["deployment"]["files"][".env.example"] = "e" * 64
        c_row = cls.c["compatibility"]["predecessors"][0]
        c_row.update(version=target["version"], manifest_hash=manifest_hash(target),
                     raw_manifest_sha256=sha(canonical_json(target)), source_sha=target["source_sha"])
        c_proof = copy.deepcopy(proof)
        c_proof.update(tag=cls.c["version"], components=copy.deepcopy(cls.c["components"]),
                       deployment=copy.deepcopy(cls.c["deployment"]))
        c_proof["predecessors"][0].update(predecessor=copy.deepcopy(c_row),
            predecessor_components=copy.deepcopy(target["components"]), predecessor_deployment=copy.deepcopy(target["deployment"]))
        c_proof['predecessors'][0]['migration_evidence'].update(
            source_inventory_sha256='2' * 64, source_applied_set_sha256='4' * 64,
            target_inventory_sha256='a' * 64, target_applied_set_sha256='b' * 64)
        cls.c_proof = bind_proof(cls.c, c_proof)
        cls.releases = {}
        cls.verifier = Verifier(raw_settings(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(cls.keys / "cosign.pub"),
                                             FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true"),
                                host_only=True, which=lambda _: cls.cosign, environ=cls.sign_env)
        cls.source_hash = "5" * 64
        for manifest, raw_proof in ((cls.previous, b""), (cls.b, cls.b_proof), (cls.c, cls.c_proof)):
            directory = cls.keys / manifest["version"]
            directory.mkdir(mode=0o700)
            m, b = directory / "manifest.json", directory / "bundle.json"
            m.write_bytes(canonical_json(manifest))
            subprocess.run([cls.cosign, "sign-blob", "--yes", "--key", str(cls.keys / "cosign.key"),
                            "--use-signing-config=false", "--tlog-upload=false", "--bundle", str(b), str(m)],
                           cwd=str(directory), env=cls.sign_env, check=True, capture_output=True, timeout=30)
            fetched = FetchedManifest(str(m), directory, m, m.read_bytes(), manifest, bundle_path=b)
            verification = cls.verifier.verify(fetched)
            if not verification.verified:
                raise AssertionError("real local-key verification failed: " + verification.error)
            snapshot = VerificationSnapshot(True, verification.method, verification.signer, verification.issuer,
                                            verification.tool, NOW, verification.public_key_sha256)
            cls.releases[manifest["version"]] = DiscoveredRelease(m.read_bytes(), b.read_bytes(), manifest_hash(manifest),
                cls.verifier.trust_fingerprint(), snapshot, raw_proof, (), "future-agi/future-agi", cls.source_hash)
        cls.layout = topology_profile("standalone", ())
        cls.dependencies = proof["predecessors"][0]["dependencies"]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="baseline-state-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.env_bytes = b"FUTURE_AGI_VERSION=v1.51.0\nSECRET=unchanged\n"
        (self.root / ".env").write_bytes(self.env_bytes)
        self.store = RunnerStateStore(self.root, self.root / ".env")
        self.registration = registration(self.store)
        self.store.register(self.registration)
        self.time = NOW
        self.baseline = BaselineStore(self.store, verifier=self.verifier, clock=lambda: self.time)
        self.a, self.b_release, self.c_release = (self.releases[v] for v in ("v1.51.0", "v1.52.0", "v1.53.0"))
        self.obs = self.observation(self.a, self.env_bytes)
        with self.baseline.locked():
            self.head = self.baseline.enroll(self.a, self.obs)

    @staticmethod
    def render_config(raw):
        # Deterministic stand-in for later Compose observation; binds ALL bytes.
        # This test does not claim to execute the real host config renderer.
        return sha(b"synthetic-resolved-compose-and-env\0" + raw)

    def observation(self, release, env):
        facts = Observations(self.time, platform="linux/amd64",
            product_images=runtime_images(release.manifest["components"], self.layout),
            dependency_images={i["service"]: (RuntimeImage(i["image"], i["digest"], "sha256:" + "e" * 64),)
                               for i in self.dependencies},
            state_epochs={d: 7 for d in self.layout.domains}, persistence=dict(self.layout.persistence),
            deployment_files=self.previous["deployment"]["files"], env={"FUTURE_AGI_VERSION": release.version},
            installation_fingerprint=self.registration.binding.installation_fingerprint,
            config_fingerprint=self.render_config(env), profile_fingerprint=manifest_hash([]),
            trust_fingerprint=self.verifier.trust_fingerprint(), owner_confirmed=True, topology_qualified=True,
            runtime_complete=True, no_source_build=True, no_pending_migrations=True, no_pending_transaction=True)
        inventory, applied = {'v1.51.0': ('1', '3'), 'v1.52.0': ('2', '4'), 'v1.53.0': ('a', 'b')}[release.version]
        cid = '9' * 64
        runtime = RuntimeObservationV2(release.version, release.manifest_sha256, self.time,
            installation_binding_hash(self.registration.binding), facts.platform, facts.product_images,
            facts.dependency_images, inventory * 64, applied * 64, '5' * 64,
            manifest_hash({'files': dict(facts.deployment_files)}), sha(env), facts.config_fingerprint,
            'd' * 64, 'e' * 64, {cid: 0}, {cid: 'unless-stopped'}, True, True)
        return StructuralObservation(replace(facts, runtime_observation=runtime, binding_sha256=runtime.binding_sha256,
                                             executor_sha256='7' * 64), env, {},
                                     {d: sha(('volume-' + d).encode()) for d in self.layout.backup_domains},
                                     'standalone-runtime-v2', True)

    def prepare(self, target=None, obs=None, head=None):
        target, obs, head = target or self.b_release, obs or self.obs, head or self.head
        attempt = self.store.reserve_attempt(TargetIdentity(target.version, target.manifest_hash), "always", self.time.replace(microsecond=0))
        backup = canonical_json({"schema_version": 1, "attempt_id": attempt.attempt_id,
            "installation_fingerprint": self.registration.binding.installation_fingerprint,
            "target_manifest_hash": target.manifest_hash, "source_version": head.release.version,
            "backup_id": "synthetic-backup-" + attempt.attempt_id, "started_at": stamp(self.time),
            "completed_at": stamp(self.time), "covered_domains": list(self.layout.backup_domains),
            "verification": "passed", "restore_procedure_id": "synthetic-only-not-a-restore-test"})
        source = obs.facts.runtime_observation
        backup_doc = json.loads(backup)
        backup_doc.update(schema_version=2, binding_sha256=source.binding_sha256, inventory_sha256='c' * 64,
            source_observation_sha256=source.sha256, quiescence_evidence_sha256='f' * 64,
            snapshot_at=stamp(self.time), inventory_version=2,
            restore_procedure_id='standalone-inplace-cold-restore-v1')
        backup = canonical_json(backup_doc)
        hold = ColdHold(attempt.attempt_id, attempt.operation_token, attempt.operation_token, source.binding_sha256,
            source.sha256, 'f' * 64, self.time.replace(microsecond=0), self.time, attempt.binding.daemon_identity,
            source.storage_sha256, tuple(source.restart_counts), source.restart_counts, source.restart_policies)
        seal = BackupSeal(attempt.attempt_id, attempt.operation_token, attempt.operation_token, source.binding_sha256,
            target.manifest_hash, head.release.version, sha(backup), 'c' * 64, source.sha256, 'f' * 64,
            hold.snapshot_at, self.time)
        self.baseline.retain_attempt(attempt, run_id=attempt.operation_token, target=target, observation=obs)
        self.last_backup, self.last_hold, self.last_seal = backup, hold, seal
        intent = self.baseline.prepare_success(attempt, expected_head=head.identity, run_id=attempt.operation_token,
            target=target, observation=obs, backup_raw=backup, render_config=self.render_config,
            cold_hold=hold, backup_seal=seal)
        env = render_changes(obs.env_bytes.decode(), {p.name: p.value for p in intent.pins}).encode()
        self.time += timedelta(seconds=1)
        return intent, self.observation(target, env)

    def receipt(self, intent, post, *, status="succeeded"):
        # The synthetic engine owns selection. Once native retention exists,
        # never mutate its accepted candidate in place: use another real commit.
        path = self.root / '.env'
        if path.read_bytes() != post.env_bytes:
            if path.stat().st_nlink > 1:
                from deploy.update.envcommit import commit, reference
                commit(path, path, path.read_bytes(), post.env_bytes, path.stat().st_mode & 0o7777, reference()['forward'])
            else:
                path.write_bytes(post.env_bytes)
        old = self.baseline._head(intent.old_head)
        metadata = {"schema_version": 1, "attempt_id": intent.attempt.attempt_id, "run_id": intent.run_id,
            "manifest_sha256": intent.target.manifest_sha256, "bundle_sha256": intent.target.bundle_sha256,
            "manifest_hash": intent.target.manifest_hash, "installation_fingerprint": old.installation_fingerprint,
            "config_fingerprint": old.config_fingerprint, "profile_fingerprint": old.profile_fingerprint,
            "trust_fingerprint": intent.target.trust_fingerprint,
            "backup_proof": json.loads(self.baseline.blob(intent.backup_sha256))}
        record = {"kind": "apply", "status": status, "run_id": intent.run_id, "target_version": intent.target.version,
            "from_version": old.release.version, "manifest_hash": intent.target.manifest_hash,
            "project": self.registration.binding.compose_project, "mode": "standalone", "production": False,
            "env_file": ".env", "env_changes": {p.name: p.value for p in intent.pins},
            "target_image_ids": {s: v[0].image_id for s, v in post.facts.product_images.items()},
            "finished_at": stamp(self.time), "automation": metadata}
        return self.baseline.engine_state.write_receipt(record).name

    def test_rollover_A_B_C_keeps_registration_physical_layout_and_original_evidence(self):
        control = self.store.change_control("pause", NOW)
        before_registration = (self.store.path / "registration.json").read_bytes()
        state = self.store.read_state()
        latch = TargetIdentity("v1.51.9", "a" * 64)
        self.store.save_state(replace(state, failed_target_latch=latch), expected_generation=state.generation)
        head, obs = self.head, self.obs
        intents = []
        for target in (self.b_release, self.c_release):
            with self.baseline.locked():
                intent, post = self.prepare(target, obs, head)
                receipt = self.receipt(intent, post)
                head = self.baseline.commit(intent, receipt_name=receipt, observation=post)
            intents.append(intent)
            # A new process would reconstruct exactly these stores; no re-enrollment.
            self.baseline = BaselineStore(RunnerStateStore(self.root, self.root / ".env"),
                                          verifier=self.verifier, clock=lambda: self.time)
            self.assertEqual(self.baseline.current(), head)
            self.assertEqual(head.release.version, target.version)
            self.assertEqual(head.execution_deployment, self.head.execution_deployment)
            self.assertNotEqual(head.config_fingerprint, self.head.config_fingerprint)
            self.assertIsNone(self.store.read_state().active_attempt_id)
            self.assertEqual(self.store.read_state().failed_target_latch, latch)
            self.assertEqual(self.store.read_control(), control)
            obs = post
        self.assertEqual(head.generation, 2)
        self.assertEqual((self.store.path / "registration.json").read_bytes(), before_registration)
        self.assertEqual(self.baseline.blob(self.head.release.manifest_sha256), self.a.raw_manifest)
        self.assertEqual(self.baseline.blob(intents[0].target.proof_sha256), self.b_proof)
        self.assertNotEqual(intents[0].backup_sha256, intents[1].backup_sha256)
        self.assertFalse((self.store.path / "facts.json").exists())
        # Core never writes env: it preserves exactly the synthetic engine's bytes.
        self.assertEqual((self.root / ".env").read_bytes(), obs.env_bytes)

    def restore_fixture(self):
        from deploy.update import envfile
        intent, post = self.prepare()
        path = self.root / '.env'
        changes = {p.name: p.value for p in intent.pins}
        inverse = envfile.recovery_record(path, changes)
        envfile.write_changes(path, changes, recovery=inverse)
        restore = self.baseline.prepare_restore(intent.attempt, expected_head=self.head.identity, run_id=intent.run_id,
            source_observation=self.obs, backup_raw=self.last_backup, backup_seal=self.last_seal,
            env_inverse_raw=canonical_json(inverse))
        envfile.restore_changes(path, inverse)
        restored = self.observation(self.a, self.env_bytes)
        record = {'kind': 'restore', 'status': 'restored', 'run_id': restore.run_id,
            'attempt_id': intent.attempt.attempt_id, 'operation_token': intent.attempt.operation_token,
            'source_version': self.a.version, 'from_version': self.a.version, 'target_version': self.b_release.version,
            'target_image_ids': {s: v[0].image_id for s, v in restored.facts.product_images.items()},
            'manifest_hash': intent.attempt.target.manifest_hash, 'backup_proof_sha256': restore.backup_sha256,
            'backup_seal_sha256': restore.backup_seal_sha256, 'env_inverse_sha256': restore.env_inverse_sha256,
            'source_observation_sha256': self.last_seal.source_observation_sha256,
            'project': intent.attempt.binding.compose_project, 'mode': 'standalone', 'production': False,
            'finished_at': stamp(self.time)}
        receipt = self.baseline.engine_state.write_receipt(record).name
        return intent, restore, restored, receipt

    def test_restore_retains_A_and_atomically_latches_exact_B(self):
        registration_before = (self.store.path / 'registration.json').read_bytes()
        intent, restore, restored, receipt = self.restore_fixture()
        before = self.store.read_state()
        self.assertEqual(self.baseline.commit_restore(restore, receipt_name=receipt, observation=restored), self.head)
        state = self.store.read_state()
        self.assertEqual(state.generation, before.generation + 1)
        self.assertEqual(state.baseline_head, self.head.identity)
        self.assertIsNone(state.active_attempt_id)
        self.assertEqual(state.failed_target_latch, intent.attempt.target)
        self.assertEqual(self.store.read_attempt(intent.attempt.attempt_id).outcome, 'restored')
        self.assertEqual((self.store.path / 'registration.json').read_bytes(), registration_before)
        # B->C is not A->C, even though failed B really was started.
        with self.assertRaises(CompatibilityError):
            self.baseline.observe(restored, self.c_release)
        for action in ('pause', 'disable', 'resume', 'enable'):
            self.store.change_control(action, self.time)
            self.assertEqual(self.store.read_state().failed_target_latch, intent.attempt.target)
        self.assertEqual(self.baseline.reconcile_restore(intent.attempt.attempt_id, receipt_name=receipt,
            observation=restored), self.head)
        self.store.retry_target(intent.attempt.target)
        self.baseline.reconcile_restore(intent.attempt.attempt_id, receipt_name=receipt, observation=restored)
        self.assertIsNone(self.store.read_state().failed_target_latch)  # replay cannot undo explicit retry

    def test_after_restored_B_a_different_C_requires_an_exact_signed_A_C_row(self):
        _, restore, restored, receipt = self.restore_fixture()
        self.baseline.commit_restore(restore, receipt_name=receipt, observation=restored)
        with self.assertRaises(CompatibilityError):
            self.baseline.observe(restored, self.c_release)  # C's genuine B->C row is wrong for A.
        target = copy.deepcopy(self.c)
        proof = json.loads(self.c_proof)
        pred = copy.deepcopy(self.b['compatibility']['predecessors'][0])
        target['compatibility']['predecessors'] = [pred]
        proof['predecessors'][0].update(predecessor=copy.deepcopy(pred),
            predecessor_components=copy.deepcopy(self.previous['components']),
            predecessor_deployment=copy.deepcopy(self.previous['deployment']))
        proof['predecessors'][0]['migration_evidence'].update(
            source_inventory_sha256='1' * 64, source_applied_set_sha256='3' * 64)
        rawproof = bind_proof(target, proof)
        with tempfile.TemporaryDirectory(prefix='exact-A-C-', dir=self.keys) as temporary:
            directory = Path(temporary)
            manifest_path, bundle = directory / 'manifest.json', directory / 'bundle.json'
            raw = canonical_json(target)
            manifest_path.write_bytes(raw)
            subprocess.run([self.cosign, 'sign-blob', '--yes', '--key', str(self.keys / 'cosign.key'),
                '--use-signing-config=false', '--tlog-upload=false', '--bundle', str(bundle), str(manifest_path)],
                cwd=str(directory), env=self.sign_env, check=True, capture_output=True, timeout=30)
            fetched = FetchedManifest(str(manifest_path), directory, manifest_path, raw, target, bundle_path=bundle)
            verified = self.verifier.verify(fetched)
            self.assertTrue(verified.verified)
            direct = DiscoveredRelease(raw, bundle.read_bytes(), manifest_hash(target), self.verifier.trust_fingerprint(),
                VerificationSnapshot(True, verified.method, verified.signer, verified.issuer, verified.tool, self.time,
                    verified.public_key_sha256), rawproof, (), self.b_release.repository, self.source_hash)
        self.baseline.observe(restored, direct)
        intent, post = self.prepare(direct, restored, self.head)
        success = self.receipt(intent, post)
        successor = self.baseline.commit(intent, receipt_name=success, observation=post)
        self.assertEqual(successor.release.version, direct.version)
        self.assertEqual(successor.previous_head, self.head.identity)
        self.assertEqual(self.store.read_state().failed_target_latch, restore.attempt.target)

    def test_restore_crashes_at_seal_terminal_and_CAS_are_metadata_only(self):
        intent, restore, restored, receipt = self.restore_fixture()
        original_immutable, original_write = self.baseline._immutable, self.store._write
        def crash_seal(child, name, value):
            original_immutable(child, name, value)
            if name.endswith('-restore.json'):
                raise SimulatedCrash()
        with mock.patch.object(self.baseline, '_immutable', side_effect=crash_seal), self.assertRaises(SimulatedCrash):
            self.baseline.commit_restore(restore, receipt_name=receipt, observation=restored)
        self.assertEqual(self.store.read_state().baseline_head, self.head.identity)
        self.assertIsNone(self.store.read_state().failed_target_latch)
        def crash_state(directory, name, value):
            if name == 'state.json':
                raise SimulatedCrash()
            original_write(directory, name, value)
        with mock.patch.object(self.store, '_write', side_effect=crash_state), self.assertRaises(SimulatedCrash):
            self.baseline.commit_restore(restore, receipt_name=receipt, observation=restored)
        self.assertEqual(self.store.read_attempt(intent.attempt.attempt_id).outcome, 'restored')
        self.assertIsNotNone(self.store.read_state().active_attempt_id)
        # New writes accepted by restored A survive finalization. No storage API
        # is present in BaselineStore, and no row contents enter its digest.
        witness = self.root / 'post-restore-business-write'
        witness.write_bytes(b'new acknowledged write')
        self.time += timedelta(days=3)  # genuine backup expiry is irrelevant to recovery
        restored = self.observation(self.a, self.env_bytes)
        restarted = BaselineStore(self.store, verifier=self.verifier, clock=lambda: self.time)
        restarted.reconcile_restore(intent.attempt.attempt_id, receipt_name=receipt, observation=restored)
        self.assertEqual(witness.read_bytes(), b'new acknowledged write')
        self.assertIsNone(self.store.read_state().active_attempt_id)
        self.assertEqual(self.store.read_state().failed_target_latch, intent.attempt.target)
        self.assertEqual(restarted.current(), self.head)

    def test_restore_wrong_volume_image_receipt_and_conflicting_direction_refuse(self):
        intent, restore, restored, receipt = self.restore_fixture()
        with self.assertRaises(StoreError):
            self.baseline.commit_restore(restore, receipt_name=receipt,
                observation=replace(restored, volumes=dict(restored.volumes, temporal='f' * 64)))
        wrong_runtime = replace(restored.facts.runtime_observation, storage_sha256='a' * 64)
        with self.assertRaises(StoreError):
            self.baseline.commit_restore(restore, receipt_name=receipt,
                observation=replace(restored, facts=replace(restored.facts, runtime_observation=wrong_runtime)))
        with self.assertRaises(StoreError):
            self.baseline.commit_restore(replace(restore, env_inverse_sha256='a' * 64),
                receipt_name=receipt, observation=restored)
        # A success receipt after restore decision cannot advance B.
        post = self.observation(self.b_release, self.env_bytes.replace(b'v1.51.0', b'v1.52.0'))
        success = self.receipt(intent, post)
        with self.assertRaises(StoreError):
            self.baseline.commit(intent, receipt_name=success, observation=post)

    def test_retained_attempt_roundtrip_preserves_original_observation_time(self):
        intent, _ = self.prepare()
        retained = self.baseline.automatic_attempt(intent.attempt.attempt_id)
        original = self.baseline.retained_observation(retained)
        self.assertEqual(original.retained_state, self.obs.retained_state)
        self.assertEqual(original.facts.now, NOW)
        self.assertNotEqual(original.structure_sha256, self.observation(self.b_release,
            self.env_bytes.replace(b'v1.51.0', b'v1.52.0')).structure_sha256)

    def test_final_before_backup_reprobe_cannot_replace_attempt_linked_source_observation(self):
        import time
        from types import SimpleNamespace
        from deploy.update.automatic import AutomaticLifecycle
        from deploy.update.contracts import AutomaticUpdateDecision
        intent, _ = self.prepare()
        life = AutomaticLifecycle(self.store, SimpleNamespace(verifier=self.verifier), self.registration, lambda: self.time,
            executor_digest=lambda root: '7' * 64)
        life.target, life.source_observation = self.b_release, self.obs
        life.retain_attempt(intent.attempt, intent.run_id)
        fresh_observation = self.observation(self.a, self.env_bytes)
        self.assertNotEqual(fresh_observation.facts.runtime_observation.sha256, self.obs.facts.runtime_observation.sha256)
        inst = SimpleNamespace(root=self.root, runner=SimpleNamespace(deadline=time.monotonic() + 100,
            policy=SimpleNamespace(probe_timeout_seconds=90)))
        inst.fresh = lambda: inst
        with mock.patch.object(life, '_observation', return_value=fresh_observation), \
             mock.patch.object(life, 'preflight', return_value=AutomaticUpdateDecision(True, ())), \
             mock.patch('deploy.update.host_facts.check_binding'), \
             mock.patch('deploy.update.host_facts.fingerprints', return_value=(self.head.config_fingerprint,
                 self.head.profile_fingerprint, 'f' * 64)), \
             mock.patch('deploy.update.host_facts.hash_path', side_effect=lambda root, name: self.obs.facts.deployment_files[name]):
            final = life.facts(inst, 'before_backup')
        self.assertIs(life.source_observation, self.obs)
        self.assertEqual(final.runtime_observation.observed_at, fresh_observation.facts.now)
        retained = life.baseline.retained_observation(life.attempt_intent)
        self.assertEqual(retained.facts.runtime_observation.sha256, self.obs.facts.runtime_observation.sha256)

    def test_decided_success_replays_retained_observation_without_live_health_or_capture(self):
        import time
        from types import SimpleNamespace
        from deploy.update.automatic import AutomaticLifecycle
        from deploy.update.baseline import identity
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        life = AutomaticLifecycle(self.store, SimpleNamespace(verifier=self.verifier), self.registration, lambda: self.time)
        life.intent, life.target = intent, self.b_release
        life.ledger = SimpleNamespace(direction='forward_pending')
        inst = SimpleNamespace(runner=SimpleNamespace(deadline=time.monotonic()+300,
            policy=SimpleNamespace(probe_timeout_seconds=90)))
        with mock.patch.object(life, '_successor_backup', return_value=None):
            life.retain_success(inst, post)
        decision = dict(run_id=intent.run_id, successor_intent_sha256=identity(intent),
            observation_sha256=post.facts.runtime_observation.sha256,
            env_sha256=sha(post.env_bytes), config_sha256=post.facts.config_fingerprint,
            storage_sha256=post.facts.runtime_observation.storage_sha256)
        life.ledger = SimpleNamespace(direction='success_decided', snapshot={'success_seal': decision})
        self.time += timedelta(days=3)
        # B may be unavailable, and an operator may have subsequently edited env.
        # Neither revokes the accepted successor or authorizes a restore.
        (self.root/'.env').write_bytes(post.env_bytes+b'AFTER_SUCCESS=operator\n')
        with mock.patch.object(life, '_observation', side_effect=StoreError(ReasonCode.READINESS_FAILED)) as probe, \
             mock.patch.object(life, '_successor_backup', side_effect=AssertionError('live capture after decision')):
            life.complete_success(SimpleNamespace(installation=inst), self.baseline.engine_state.path/receipt)
            life.complete_success(SimpleNamespace(installation=inst), self.baseline.engine_state.path/receipt)
        probe.assert_not_called()
        self.assertEqual(self.baseline.current().release.version, self.b_release.version)
        self.assertIsNone(self.store.read_state().active_attempt_id)
        self.assertEqual(life.latest_observation.facts.now, post.facts.now)
        self.assertEqual((self.root/'.env').read_bytes(), post.env_bytes+b'AFTER_SUCCESS=operator\n')
        for key in decision:
            wrong = dict(decision, **{key:'f'*64})
            with self.subTest(key=key), self.assertRaises(StoreError):
                self.baseline.commit_decided_success(intent, receipt_name=receipt, decision=wrong)

    def test_preseal_failed_available_terminal_CAS_replays_and_preserves_explicit_retry(self):
        intent, _ = self.prepare()
        retained = self.baseline.automatic_attempt(intent.attempt.attempt_id)
        original = self.store._write
        def crash(directory, name, value):
            if name == 'state.json': raise SimulatedCrash()
            return original(directory, name, value)
        with mock.patch.object(self.store, '_write', side_effect=crash), self.assertRaises(SimulatedCrash):
            self.baseline.finish_failed_available(retained, self.obs)
        self.assertEqual(self.store.read_attempt(intent.attempt.attempt_id).outcome, 'failed_available')
        self.assertIsNotNone(self.store.read_state().active_attempt_id)
        self.time += timedelta(seconds=1)
        self.baseline.finish_failed_available(retained, self.observation(self.a, self.env_bytes))
        state = self.store.read_state()
        self.assertIsNone(state.active_attempt_id)
        self.assertEqual(state.failed_target_latch, intent.attempt.target)
        self.assertEqual(self.baseline.current(), self.head)
        self.store.retry_target(intent.attempt.target)
        self.baseline.finish_failed_available(retained, self.observation(self.a, self.env_bytes))
        self.assertIsNone(self.store.read_state().failed_target_latch)

    def test_restore_intent_replay_after_env_inverse_never_replaces_original_inverse(self):
        from types import SimpleNamespace
        from deploy.update.automatic import AutomaticLifecycle
        intent, restore, restored, receipt = self.restore_fixture()
        life = AutomaticLifecycle(self.store, SimpleNamespace(verifier=self.verifier), self.registration, lambda: self.time)
        life.ledger, life.backup_seal = SimpleNamespace(direction='restore_decided'), self.last_seal
        life.source_observation = self.obs
        engine = SimpleNamespace(active_record=None, state=self.baseline.engine_state)
        witness = self.root / 'post-restore-write'
        witness.write_bytes(b'accepted by A after its startup')
        with mock.patch.object(life.baseline, 'prepare_restore', side_effect=AssertionError('must retain original inverse')):
            self.assertEqual(life.prepare_restore(engine), restore)
        self.assertEqual(self.baseline.blob(restore.env_inverse_sha256), life.baseline.blob(restore.env_inverse_sha256))
        self.assertEqual(witness.read_bytes(), b'accepted by A after its startup')
        self.assertEqual((self.root / '.env').read_bytes(), self.env_bytes)

    def test_resume_dispatches_same_attempt_to_engine_without_forward_retry_or_resetting_ambiguity(self):
        import time
        from types import SimpleNamespace
        from deploy.update.automatic import AutomaticLifecycle
        intent, post = self.prepare()
        life = AutomaticLifecycle(self.store, SimpleNamespace(verifier=self.verifier), self.registration,
            lambda: self.time, executor_digest=lambda root: '7' * 64)
        inst = SimpleNamespace(root=self.root, runner=SimpleNamespace(deadline=time.monotonic() + 100,
            policy=SimpleNamespace(probe_timeout_seconds=90), uncertain=True))
        ledger = mock.Mock(direction='restore_decided', backup_seal=None)
        ledger.snapshot = {'old_head': self.head.identity,
            'source_observation_sha256': self.obs.facts.runtime_observation.sha256,
            'previous_start_armed': True, 'pending': {'kind': 'remote_writer'}}
        engine = mock.Mock()
        engine.resume_automatic_locked.return_value = 'engine-owned-result'
        with mock.patch('deploy.update.recovery.RecoveryLedger.open', return_value=ledger) as opened, \
             mock.patch('deploy.update.automatic.BackupConfiguration.load',
                        return_value=SimpleNamespace(destination=self.root / 'enrolled-backups')), \
             mock.patch('deploy.update.apply.Updater', return_value=engine):
            with self.baseline.locked():
                factory = mock.Mock(return_value=inst)
                self.assertEqual(life.resume(factory, self.baseline.engine_state, mock.Mock(),
                                             supervisor=inst.runner), 'engine-owned-result')
                factory.assert_called_once_with()
        opened.assert_called_once()
        self.assertEqual(opened.call_args.args[0], self.root / 'enrolled-backups' / intent.attempt.attempt_id)
        self.assertEqual(opened.call_args.kwargs['operation_token'], intent.attempt.operation_token)
        self.assertEqual(opened.call_args.kwargs['run_id'], intent.run_id)
        engine.resume_automatic_locked.assert_called_once_with(lifecycle=life, ledger=ledger)
        engine.apply.assert_not_called()
        engine.rollback.assert_not_called()
        self.assertTrue(inst.runner.uncertain)
        self.assertTrue(ledger.snapshot['previous_start_armed'])
        self.assertEqual(self.store.read_state().active_attempt_id, intent.attempt.attempt_id)
        self.assertEqual(self.store.read_state().baseline_head, self.head.identity)

    def test_stopped_guards_use_retained_A_and_verified_hold_without_reprobing_or_refreshing_time(self):
        import time
        from types import SimpleNamespace
        from deploy.update.automatic import AutomaticLifecycle
        intent, post = self.prepare()
        (self.root / '.env').write_bytes(post.env_bytes)
        self.time += timedelta(seconds=600)  # the source observation is deliberately stale
        hold = replace(self.last_hold, verified_at=self.time)
        observer = mock.Mock()
        life = AutomaticLifecycle(self.store, SimpleNamespace(verifier=self.verifier), self.registration,
            lambda: self.time, observer=observer, executor_digest=lambda root: '7' * 64)
        life.target, life.intent = self.b_release, intent
        life.source_observation, life.ledger, life.backup_seal = self.obs, object(), self.last_seal
        inst = SimpleNamespace(root=self.root, runner=SimpleNamespace(deadline=time.monotonic() + 100,
            policy=SimpleNamespace(probe_timeout_seconds=90)))
        inst.fresh = lambda: inst
        verifier = mock.Mock()
        verifier.verify_hold.return_value = hold
        with mock.patch.object(life, 'backup', return_value=verifier), \
             mock.patch('deploy.update.host_facts.check_binding'), \
             mock.patch('deploy.update.host_facts.fingerprints', return_value=(intent.expected_config_fingerprint,
                 self.head.profile_fingerprint, 'f' * 64)), \
             mock.patch('deploy.update.host_facts.hash_path', side_effect=lambda root, name: self.obs.facts.deployment_files[name]):
            result = life.facts(inst, 'before_rollout')
        observer.observe.assert_not_called()
        verifier.verify_hold.assert_called_once()
        self.assertEqual(result.runtime_observation.observed_at, NOW)
        self.assertEqual(result.runtime_observation.sha256, self.obs.facts.runtime_observation.sha256)
        self.assertEqual(result.cold_hold.verified_at, self.time)
        self.assertEqual(result.config_fingerprint, self.head.config_fingerprint)
        self.assertEqual((self.root / '.env').read_bytes(), post.env_bytes)

    def test_fixed_host_runtime_envelope_is_normalised_without_cross_release_structure_equality(self):
        from deploy.update.standalone_state import StandaloneObservation, MOUNTS
        from deploy.update.baseline import normalise_observation
        containers = {s: {} for s in MOUNTS}
        host = StandaloneObservation(self.obs.facts, self.obs.env_bytes, self.obs.volumes,
            self.obs.facts.runtime_observation, containers, 'd' * 64)
        self.assertEqual(normalise_observation(host).structure_sha256, self.head.structure_sha256)
        self.assertEqual(self.baseline.enroll(self.a, host), self.head)
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        target = StandaloneObservation(post.facts, post.env_bytes, post.volumes, post.facts.runtime_observation, containers, 'd' * 64)
        successor = self.baseline.commit(intent, receipt_name=receipt, observation=target)
        self.assertEqual(successor.structure_sha256, post.structure_sha256)
        self.assertNotEqual(successor.structure_sha256, self.head.structure_sha256)

    def test_A6_preflight_uses_signed_measurements_fixed_host_sizes_and_real_budgets(self):
        import time
        from types import SimpleNamespace
        from deploy.update.automatic import AutomaticLifecycle
        from deploy.update.compatibility import transition_measurements
        from deploy.update.contracts import AutomationPolicy, HostSizeObservation, HostTableSize, MaintenanceWindowSpec
        policy = AutomationPolicy(qualification_policy='standalone-cold-restore-v1',
            maintenance_window=MaintenanceWindowSpec('always', 'UTC'))
        measurements = transition_measurements(json.loads(self.b_proof)['predecessors'][0])
        sizes = HostSizeObservation(tuple(HostTableSize(m.engine, t.table, t.reference_rows, t.reference_bytes)
            for m in measurements.migration_measurements for t in m.tables),
            {'app-data': 1000, 'postgres-data': 1000, 'clickhouse-data': 1000}, 10**15, 10**15, True,
            {'app-data': 1000, 'postgres-data': 1000, 'clickhouse-data': 1000},
            {'app-data': 1, 'postgres-data': 1, 'clickhouse-data': 1})
        measured = mock.Mock()
        measured.measure_update_sizes.return_value = sizes
        factory = mock.Mock(return_value=measured)
        life = AutomaticLifecycle(self.store, SimpleNamespace(verifier=self.verifier), self.registration,
            lambda: self.time, size_observer_factory=factory)
        life.measurements = transition_measurements(json.loads(self.b_proof)['predecessors'][0])
        inst = SimpleNamespace(runner=SimpleNamespace(policy=policy, deadline=time.monotonic() + 100000))
        inst.fresh = lambda: inst
        with mock.patch('deploy.update.automatic.BackupConfiguration.load',
                        return_value=SimpleNamespace(destination=self.root / 'enrolled-backups')):
            decision = life.preflight(inst)
            self.assertTrue(decision.allowed, decision.reason_codes)
            self.assertGreater(decision.estimate.migration_seconds, 0)
            oversized = replace(sizes, table_sizes=(replace(sizes.table_sizes[0], rows=sizes.table_sizes[0].rows+1),
                                                    *sizes.table_sizes[1:]))
            measured.measure_update_sizes.return_value = oversized
            denied = life.preflight(inst)
            self.assertEqual(denied.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            self.assertIsNone(denied.estimate)
            self.assertIsNone(self.store.read_state().active_attempt_id)
            measured.measure_update_sizes.return_value = sizes
            self.assertEqual(measured.measure_update_sizes.call_args.kwargs['measurements'], life.measurements)
            self.assertEqual(measured.measure_update_sizes.call_args.kwargs['backup_destination'], self.root / 'enrolled-backups')
            factory.assert_called_once_with(self.registration, policy, life.clock)
            inst.runner.deadline = time.monotonic() + 1
            denied = life.preflight(inst)
            self.assertFalse(denied.allowed)
            self.assertIn(ReasonCode.MIGRATION_EXCEEDS_WINDOW, denied.reason_codes)
            self.assertIn(ReasonCode.BACKUP_EXCEEDS_WINDOW, denied.reason_codes)
            self.assertIsNotNone(denied.estimate)
            inst.runner.deadline = time.monotonic() + 100000
            measured.measure_update_sizes.return_value = replace(sizes, docker_free_bytes=0, backup_free_bytes=0)
            denied = life.preflight(inst)
            self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_BACKUP, denied.reason_codes)
            self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION, denied.reason_codes)
            from deploy.update.orchestrator import GuardRefused
            measured.measure_update_sizes.side_effect = GuardRefused(ReasonCode.RESTORE_CAPACITY_INSUFFICIENT)
            self.assertEqual(life.preflight(inst).reason_codes, (ReasonCode.RESTORE_CAPACITY_INSUFFICIENT,))
            measured.measure_update_sizes.side_effect = RuntimeError('CANARY /private/backup')
            self.assertEqual(life.preflight(inst).reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            self.assertNotIn('CANARY', repr(life.preflight_decision))
            life.measurements = None
            measured.reset_mock()
            self.assertEqual(life.preflight(inst).reason_codes, (ReasonCode.UNCERTIFIED_RELEASE,))
            measured.measure_update_sizes.assert_not_called()

    def test_contract2_runtime_and_optional_jsonschema_parity(self):
        import importlib.util
        from deploy.update.compatibility import schema
        from deploy.update.manifest import validate_manifest
        validators = None
        if importlib.util.find_spec("jsonschema"):
            from jsonschema import Draft202012Validator
            validators = (Draft202012Validator(schema("release-manifest.schema.json")),
                          Draft202012Validator(schema("compatibility-proof.schema.json")))
        for release in (self.b_release, self.c_release):
            target = validate_manifest(release.manifest)
            proof = validate_release_proof(release.release_proof, target, repository=release.repository)
            if validators:
                validators[0].validate(target)
                validators[1].validate(proof)
            missing = copy.deepcopy(target)
            del missing["compatibility"]["predecessors"][0]["execution_deployment_hash"]
            with self.assertRaises(CompatibilityError):
                validate_manifest(missing)
            if validators:
                self.assertFalse(validators[0].is_valid(missing))
            missing_proof = copy.deepcopy(proof)
            del missing_proof["predecessors"][0]["execution_deployment"]
            raw = bind_proof(target, missing_proof)
            with self.assertRaises(CompatibilityError):
                validate_release_proof(raw, target, repository=release.repository)
            if validators:
                self.assertFalse(validators[1].is_valid(missing_proof))

    def test_business_rows_are_not_in_structural_baseline_and_caller_maps_are_frozen(self):
        before = self.obs.structure_sha256
        data = self.root / "synthetic-business-rows.json"
        data.write_text('{"rows":[1,2,3]}')
        self.assertTrue(self.baseline.observe(self.obs, self.b_release).state_unchanged)
        data.write_text('{"rows":[1,2,3,4]}')
        self.assertEqual(self.obs.structure_sha256, before)
        self.assertTrue(self.baseline.observe(self.obs, self.b_release).state_unchanged)
        with self.assertRaises(TypeError):
            self.obs.facts.state_epochs["temporal"] = 999
        with self.assertRaises(TypeError):
            self.obs.volumes["temporal"] = "f" * 64

    def test_legacy_state_reader_accepts_only_the_one_new_optional_field(self):
        path = self.store.path / "state.json"
        state = json.loads(path.read_bytes())
        # Legacy records existed only before generated enrollment; use a separate registration.
        root = self.root / "legacy"
        root.mkdir(mode=0o700)
        (root / ".env").touch()
        store = RunnerStateStore(root, root / ".env")
        store.register(registration(store))
        legacy = store.path / "state.json"
        value = json.loads(legacy.read_bytes())
        del value["baseline_head"]
        legacy.write_bytes(canonical_json(value))
        self.assertIsNone(store.read_state().baseline_head)
        del value["retry_count"]
        legacy.write_bytes(canonical_json(value))
        with self.assertRaises(StoreError):
            store.read_state()
        self.assertEqual(json.loads(path.read_bytes()), state)

    def test_duplicate_finalize_is_idempotent_conflicting_receipt_run_or_target_refuses(self):
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        first = self.baseline.commit(intent, receipt_name=receipt, observation=post)
        state = self.store.read_state()
        self.assertEqual(self.baseline.reconcile(intent.attempt.attempt_id, receipt_name=receipt, observation=post), first)
        self.assertEqual(self.store.read_state(), state)
        for altered in (replace(intent, run_id="a" * 32), replace(intent, expected_config_fingerprint="f" * 64)):
            with self.assertRaises(StoreError):
                self.baseline.commit(altered, receipt_name=receipt, observation=post)
        # Another same-run receipt is not the exact retained completion.
        second_receipt = self.receipt(intent, post)
        with self.assertRaises(StoreError):
            self.baseline.commit(intent, receipt_name=second_receipt, observation=post)

    def test_missing_success_and_fallback_never_advance(self):
        intent, post = self.prepare()
        for status in ("failed", "rolled_back", "staged", "running"):
            receipt = self.receipt(intent, post, status=status)
            with self.assertRaises(StoreError):
                self.baseline.reconcile(intent.attempt.attempt_id, receipt_name=receipt, observation=post)
            self.assertEqual(self.baseline.current(), self.head)
        self.store.finish_attempt(replace(intent.attempt, outcome="rolled_back", finished_at=self.time))
        receipt = self.receipt(intent, post)
        with self.assertRaises(StoreError):
            self.baseline.commit(intent, receipt_name=receipt, observation=post)
        self.assertEqual(self.store.read_state().failed_target_latch, intent.attempt.target)

    def test_fast_success_uses_engine_receipt_second_precision(self):
        self.time = NOW + timedelta(microseconds=400000)
        intent, post = self.prepare()
        self.time = NOW + timedelta(microseconds=900000)
        post = replace(post, facts=replace(post.facts, now=self.time,
            runtime_observation=replace(post.facts.runtime_observation, observed_at=self.time)))
        receipt = self.receipt(intent, post)
        self.assertEqual(self.baseline.commit(intent, receipt_name=receipt, observation=post).release.version,
                         self.b_release.version)

    def test_success_receipt_and_claimed_postimage_cannot_override_actual_env(self):
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        for actual in (self.env_bytes, post.env_bytes + b"UNRELATED=operator-edit\n"):
            (self.root / ".env").write_bytes(actual)
            with self.assertRaises(StoreError):
                self.baseline.commit(intent, receipt_name=receipt, observation=post)
            self.assertEqual(self.baseline.current(), self.head)
            self.assertEqual((self.root / ".env").read_bytes(), actual)

    def test_exact_predicted_config_and_structural_state_not_adopted_after_rollout(self):
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        variants = [replace(post, env_bytes=post.env_bytes + b"UNRELATED=drift\n"),
                    replace(post, facts=replace(post.facts, config_fingerprint="f" * 64)),
                    replace(post, facts=replace(post.facts, runtime_observation=replace(
                        post.facts.runtime_observation, migration_inventory_sha256='f' * 64))),
                    replace(post, volumes=dict(post.volumes, temporal="f" * 64)),
                    replace(post, facts=replace(post.facts, runtime_observation=replace(
                        post.facts.runtime_observation, applied_migrations_sha256='f' * 64))),
                    replace(post, ready=False),
                    replace(post, facts=replace(post.facts, deployment_files=self.b["deployment"]["files"]))]
        for bad in variants:
            with self.subTest(bad=variants.index(bad)), self.assertRaises((StoreError, CompatibilityError)):
                self.baseline.commit(intent, receipt_name=receipt, observation=bad)
        self.assertEqual(self.baseline.current(), self.head)
        self.baseline.commit(intent, receipt_name=receipt, observation=post)

    def test_no_enrollment_from_consent_or_unknown_probe(self):
        for bad in (replace(self.obs, facts=replace(self.obs.facts, runtime_observation=replace(
                        self.obs.facts.runtime_observation, stable=False))),
                    replace(self.obs, facts=replace(self.obs.facts, runtime_complete=False)),
                    replace(self.obs, facts=replace(self.obs.facts, owner_confirmed=False)),
                    replace(self.obs, ready=False)):
            with self.assertRaises(StoreError):
                self.baseline.enroll(self.a, bad)
        self.assertEqual(self.baseline.enroll(self.a, self.obs), self.head)
        with self.assertRaises(StoreError):
            self.baseline.enroll(replace(self.a, raw_bundle=b"not-a-signature"), self.obs)

    def test_stale_head_CAS_and_metadata_lock_contention(self):
        with self.assertRaises(StaleGeneration):
            self.baseline.prepare_success(None, expected_head="f" * 64, run_id="a" * 32,
                target=self.b_release, observation=self.obs, backup_raw=b"", render_config=self.render_config)
        with self.store._lock("state.lock"), self.assertRaises(StoreBusy):
            self.baseline.enroll(self.a, self.obs)
        state = self.store.read_state()
        with self.assertRaises(StoreError):
            self.store.save_state(replace(state, baseline_head="a" * 64), expected_generation=state.generation)

    def test_crash_each_metadata_write_recovers_without_mutation_callbacks(self):
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        # A single transition, successive crashes after each durable boundary.
        original_blob = self.baseline._blob
        original_immutable = self.baseline._immutable
        original_write = self.store._write
        def stop_blob(raw):
            result = original_blob(raw)
            if b'"status"' in raw:
                raise SimulatedCrash("crash after receipt retention")
            return result
        with mock.patch.object(self.baseline, "_blob", side_effect=stop_blob), self.assertRaises(SimulatedCrash):
            self.baseline.commit(intent, receipt_name=receipt, observation=post)
        for wanted in ("transitions", "baselines"):
            def stop_immutable(child, name, value):
                original_immutable(child, name, value)
                if child == wanted:
                    raise SimulatedCrash("crash after immutable write")
            with mock.patch.object(self.baseline, "_immutable", side_effect=stop_immutable), self.assertRaises(SimulatedCrash):
                self.baseline.commit(intent, receipt_name=receipt, observation=post)
            self.assertEqual(self.baseline.current(), self.head)
        def stop_state(directory, name, value):
            if name == "state.json":
                raise SimulatedCrash("crash after terminal before head")
            return original_write(directory, name, value)
        with mock.patch.object(self.store, "_write", side_effect=stop_state), self.assertRaises(SimulatedCrash):
            self.baseline.commit(intent, receipt_name=receipt, observation=post)
        self.assertEqual(self.store.read_attempt(intent.attempt.attempt_id).outcome, "succeeded")
        self.assertEqual(self.baseline.current(), self.head)
        restarted = BaselineStore(self.store, verifier=self.verifier, clock=lambda: self.time)
        new = restarted.reconcile(intent.attempt.attempt_id, receipt_name=receipt, observation=post)
        self.assertEqual(new.release.version, self.b_release.version)
        self.assertEqual(restarted.reconcile(intent.attempt.attempt_id, receipt_name=receipt, observation=post), new)

    def test_process_death_after_head_commit_reconciles_metadata_once(self):
        from dataclasses import asdict
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        fields = ("platform", "state_epochs", "persistence", "deployment_files", "env", "installation_fingerprint",
                  "config_fingerprint", "profile_fingerprint", "trust_fingerprint", "owner_confirmed",
                  "topology_qualified", "runtime_complete", "no_source_build", "no_pending_migrations", "no_pending_transaction")
        from deploy.update.compatibility import plain
        facts = {name: plain(getattr(post.facts, name)) for name in fields}
        facts["now"] = post.facts.now.isoformat()
        for name in ("product_images", "dependency_images"):
            facts[name] = {s: [asdict(i) for i in replicas] for s, replicas in getattr(post.facts, name).items()}
        from deploy.update.runner_state import _wire
        facts['runtime_observation'] = _wire(post.facts.runtime_observation)
        data = json.dumps(dict(facts=facts, env=post.env_bytes.decode(), formats=dict(post.formats), volumes=dict(post.volumes)))
        # Real Python process death, real flock release and durable metadata; no
        # Docker/backup/rollout action exists in this child. Fixed synthetic probe.
        script = '''
import json,os,sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,sys.argv[1])
from deploy.tests.test_update_trust import raw_settings
from deploy.update.trust import Verifier
from deploy.update.baseline import BaselineStore,StructuralObservation
from deploy.update.compatibility import Observations,RuntimeImage
from deploy.update.runner_state import RunnerStateStore
root,key,cosign,attempt,receipt,mode=sys.argv[2:8]
v=json.loads(sys.argv[8]); f=v['facts']; f['now']=datetime.fromisoformat(f['now'])
for name in ('product_images','dependency_images'):
    f[name]={s:tuple(RuntimeImage(**i) for i in images) for s,images in f[name].items()}
from deploy.update.contracts import RuntimeObservationV2
r=f['runtime_observation']; r['observed_at']=datetime.fromisoformat(r['observed_at'].replace('Z','+00:00'))
for name in ('product_images','dependency_images'):
    r[name]={s:tuple(RuntimeImage(**i) for i in images) for s,images in r[name].items()}
f['runtime_observation']=RuntimeObservationV2(**r)
obs=StructuralObservation(Observations(**f),v['env'].encode(),v['formats'],v['volumes'],'standalone-runtime-v2',True)
verifier=Verifier(raw_settings(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=key,FUTURE_AGI_UPDATE_COSIGN_OFFLINE='true'),
                  host_only=True,which=lambda _:cosign)
store=RunnerStateStore(Path(root),Path(root)/'.env')
baseline=BaselineStore(store,verifier=verifier,clock=lambda:f['now'])
original=store._write
def write(directory,name,value):
    original(directory,name,value)
    if mode=='crash' and name=='state.json': os._exit(73)
store._write=write
with baseline.locked():
    print(baseline.reconcile(attempt,receipt_name=receipt,observation=obs).identity)
'''
        args = [sys.executable, "-I", "-B", "-c", script, str(Path(__file__).resolve().parents[2]),
                str(self.root), str(self.keys / "cosign.pub"), self.cosign, intent.attempt.attempt_id, receipt]
        crashed = subprocess.run([*args, "crash", data], capture_output=True, text=True, timeout=30)
        self.assertEqual(crashed.returncode, 73, crashed.stderr)
        after_crash = self.store.read_state()
        self.assertIsNone(after_crash.active_attempt_id)
        restarted = subprocess.run([*args, "finish", data], capture_output=True, text=True, timeout=30)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertEqual(restarted.stdout.strip(), after_crash.baseline_head)
        self.assertEqual(self.store.read_state(), after_crash)

    def test_proof_tamper_predecessor_layout_and_source_refuse(self):
        for target in (replace(self.b_release, release_proof=self.b_proof + b" "),
                       replace(self.b_release, release_proof=b""),
                       replace(self.b_release, source_identity_sha256="f" * 64), self.c_release):
            with self.assertRaises((StoreError, CompatibilityError)):
                self.baseline.observe(self.obs, target)
        wrong = replace(self.obs, facts=replace(self.obs.facts, deployment_files=self.b["deployment"]["files"]))
        with self.assertRaises(StoreError):
            self.baseline.observe(wrong, self.b_release)
        self.assertTrue(self.baseline.observe(self.obs, self.b_release).state_unchanged)

    def test_private_records_closed_modes_links_tamper_and_journal_refuse(self):
        path = self.store.path / "baselines" / (self.head.identity + ".json")
        raw = path.read_bytes()
        path.write_bytes(raw[:-2] + b',"extra":true}\n')
        with self.assertRaises(StoreError):
            self.baseline.current()
        path.write_bytes(raw)
        path.chmod(0o644)
        with self.assertRaises(StoreError):
            self.baseline.current()
        path.chmod(0o600)
        path.unlink()
        path.symlink_to(self.root / ".env")
        with self.assertRaises(StoreError):
            self.baseline.current()
        path.unlink()
        path.write_bytes(raw)
        path.chmod(0o600)
        intent, post = self.prepare()
        receipt = self.receipt(intent, post)
        StateDir(self.root).write_journal({"run_id": "unrelated"})
        with self.assertRaises(StoreError):
            self.baseline.commit(intent, receipt_name=receipt, observation=post)
        self.assertEqual(StateDir(self.root).read_journal(), {"run_id": "unrelated"})
        for p in self.store.path.rglob("*"):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700 if p.is_dir() else 0o600)
        blob = self.store.path / "evidence" / self.head.release.manifest_sha256
        blob.write_bytes(blob.read_bytes() + b" ")
        with self.assertRaises(StoreError):
            self.baseline.evidence(self.head.release)

    def test_real_public_key_policy_and_discovery_proof_304(self):
        from deploy.tests.test_update_discovery import Web
        from deploy.update.compatibility import Controls, _verified
        from deploy.update.discovery import ReleaseDiscovery
        from deploy.update.manifest import MANIFEST_ASSET, BUNDLE_ASSET
        from deploy.update.trust import HttpResponse
        controls = Controls(public_key_sha256=sha((self.keys / "cosign.pub").read_bytes()))
        _verified(self.b_release.verification, self.b_release.version, controls, NOW, fresh=True)
        with self.assertRaises(CompatibilityError):
            _verified(self.b_release.verification, self.b_release.version, Controls(), NOW, fresh=True)
        web = Web()
        def transport(url, headers, timeout, **kwargs):
            if "max_bytes" in kwargs:
                self.assertEqual(kwargs["max_bytes"], 256 * 1024)
            return web(url, headers, timeout)
        cache = HttpCache(self.root / "cache", transport=transport, clock=lambda: NOW.timestamp())
        discovery = ReleaseDiscovery(cache, self.verifier)
        base = discovery._url(self.b_release.version).rsplit("/", 1)[0]
        from deploy.update.compatibility import PROOF_ASSET
        for name, raw in ((MANIFEST_ASSET, self.b_release.raw_manifest), (BUNDLE_ASSET, self.b_release.raw_bundle),
                          (PROOF_ASSET, self.b_proof)):
            web.routes[base + "/" + name] = HttpResponse(200, raw, '"fixed"')
        first = discovery.refresh(self.b_release.version)
        self.assertTrue(first.complete, first.reason_codes)
        self.assertEqual(first.candidate.release_proof, self.b_proof)
        for url in web.routes:
            web.routes[url] = HttpResponse(304, etag='"fixed"')
        self.assertEqual(discovery.evidence(first.candidate).release_proof, self.b_proof)
        web.routes[base + "/" + PROOF_ASSET] = HttpResponse(404)
        manual = discovery.refresh(self.b_release.version)
        self.assertTrue(manual.complete)
        self.assertEqual(manual.candidate.proof_reason_codes, (ReasonCode.RELEASE_INCOMPLETE,))
        from deploy.update.httpcache import DiscoveryError
        with self.assertRaises(DiscoveryError):
            discovery.evidence(first.candidate)
        # Bad/replayed/oversized proof never becomes automatic evidence; the
        # authenticated manifest remains usable by notification/manual consumers.
        for bad_proof in (self.b_proof + b" ", self.c_proof, b"x" * (256 * 1024 + 1)):
            web.routes[base + "/" + PROOF_ASSET] = HttpResponse(200, bad_proof)
            rejected = discovery.refresh(self.b_release.version)
            self.assertTrue(rejected.complete)
            self.assertFalse(rejected.candidate.release_proof)
            self.assertTrue(rejected.candidate.proof_reason_codes)
        # No proof network request occurs until actual signature verification.
        web.routes[base + "/" + MANIFEST_ASSET] = HttpResponse(200, self.b_release.raw_manifest + b" ")
        web.routes[base + "/" + BUNDLE_ASSET] = HttpResponse(200, self.b_release.raw_bundle)
        before = len(web.calls)
        rejected = discovery.refresh(self.b_release.version)
        self.assertEqual(rejected.reason_codes, (ReasonCode.SIGNATURE_REJECTED,))
        self.assertNotIn(base + "/" + PROOF_ASSET, [url for url, _, _ in web.calls[before:]])

    def test_evidence_facts_adapter_requires_typed_fixed_probe_and_does_not_use_legacy_files(self):
        from deploy.update.host_facts import EvidenceFacts
        from types import SimpleNamespace
        inst = SimpleNamespace(root=self.root)
        adapter = EvidenceFacts(self.baseline, self.b_release, probe=lambda *_: self.obs)
        with mock.patch("deploy.update.host_facts.check_binding"), mock.patch(
                "deploy.update.host_facts.fingerprints", return_value=(self.obs.facts.config_fingerprint,
                    self.obs.facts.profile_fingerprint, "f" * 64)), mock.patch(
                "deploy.update.host_facts.hash_path", side_effect=lambda root, name: self.obs.facts.deployment_files[name]):
            result = adapter(inst, "before_backup")
            self.assertTrue(result.baseline_attested and result.state_unchanged)
            self.assertFalse(result.online_verified)  # Independent guard still owns freshness/controls.
            self.assertEqual(result.predecessor_raw, self.a.raw_manifest)
            self.assertEqual(result.release_proof, self.b_proof)
            from deploy.update.orchestrator import GuardRefused
            with self.assertRaises(GuardRefused):
                EvidenceFacts(self.baseline, self.b_release, probe=lambda *_: True)(inst, "before_backup")
            (self.root / ".env").write_bytes(self.env_bytes + b"UNRELATED=drift\n")
            with self.assertRaises(GuardRefused):
                adapter(inst, "before_backup")


class ExecutionContractTests(unittest.TestCase):
    def test_default_policy_stays_uncertified_and_legacy_contract_stays_exact(self):
        self.assertFalse(default_v2()["compatibility"]["automatic_apply"])
        target, previous, proof, raw = synthetic_release()
        validate_release_proof(raw, target, repository="future-agi/future-agi")
        target["deployment"]["files"][".env.example"] = "f" * 64
        proof["deployment"] = copy.deepcopy(target["deployment"])
        raw = bind_proof(target, proof)
        with self.assertRaises(CompatibilityError):
            validate_release_proof(raw, target, repository="future-agi/future-agi")
        with self.assertRaises(CompatibilityError):
            authorize_execution(canonical_json(target), previous, raw, repository="future-agi/future-agi",
                mode="standalone", profiles=(), platform="linux/amd64", deployment_files=target["deployment"]["files"])
