"""A real-duration source observation must not expire retained-signature checks.

Uses the real AutomationPolicy, the real Verifier deadline/timeout methods and a
virtual monotonic clock that advances by the installed stability period on every
healthy-source observation. Only Docker/cosign process work is replaced.
"""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from deploy.update import automatic, trust
from deploy.update.automatic import AutomaticLifecycle
from deploy.update.contracts import AutomationPolicy
from deploy.update.trust import VerificationError, Verifier


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def policy(stability=180):
    # The exact probe timeout setup installs, with the acceptance stability period.
    return AutomationPolicy(qualification_policy='standalone-cold-restore-v1', mode='apply',
                            probe_timeout_seconds=90, stability_period_seconds=stability)


class WindowFixture:
    def lifecycle(self, clock, *, tick_seconds=14400, stability=180):
        verifier = object.__new__(Verifier)
        verifier.deadline = None
        verifier._versions = {}
        runner = SimpleNamespace(policy=policy(stability), deadline=clock()+tick_seconds)
        installation = SimpleNamespace(runner=runner, root='/unused', fresh=lambda: installation)
        verified = []

        def evidence(reference):
            # BaselineStore._verify -> Verifier.verify -> Verifier.timeout(...)
            verified.append((clock(), verifier.timeout(30)))
            return SimpleNamespace(reference=reference)

        def observe(observation, target, **kwargs):
            evidence('head')  # BaselineStore.observe re-verifies retained predecessor evidence.
            return SimpleNamespace(predecessor_raw=b'{}', release_proof=b'{}', platform='linux/amd64',
                                   deployment_files={})

        life = object.__new__(AutomaticLifecycle)
        life.baseline = SimpleNamespace(verifier=verifier, evidence=evidence, observe=observe)
        life.head = SimpleNamespace(release='head')
        life.discovery = SimpleNamespace(evidence=lambda candidate: SimpleNamespace(
            proof_reason_codes=(), release_proof=b'proof', raw_manifest=b'{}', repository='future-agi/future-agi'))
        life.registration = SimpleNamespace(binding=SimpleNamespace(topology='standalone', profiles=()))
        life.target = None
        life.attempt_intent = None

        def slow_observation(inst, previous, boundary):
            # StandaloneStateObserver waits the full stability period after its
            # first readiness pass, plus fixed probe I/O.
            clock.now += inst.runner.policy.stability_period_seconds + 25
            return SimpleNamespace(boundary=boundary)
        life._observation = slow_observation
        return life, installation, verified

    def patched(self, clock):
        return (mock.patch.object(automatic.time, 'monotonic', clock),
                mock.patch.object(trust.time, 'monotonic', clock),
                mock.patch.object(Verifier, 'require_host', return_value='cosign'),
                mock.patch.object(automatic, 'authorize_execution',
                                  return_value=SimpleNamespace(row=SimpleNamespace())),
                mock.patch.object(automatic, 'transition_measurements', return_value=SimpleNamespace()))


class VerificationWindowTests(WindowFixture, unittest.TestCase):
    def test_select_reverifies_in_a_fresh_window_after_a_full_stability_observation(self):
        clock = Clock()
        life, installation, verified = self.lifecycle(clock)
        patches = self.patched(clock)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            life.select(installation, candidate='candidate')
        self.assertEqual(len(verified), 2)
        self.assertGreaterEqual(verified[1][0]-verified[0][0], 180)
        self.assertTrue(all(0 < remaining <= 30 for _, remaining in verified))
        # The renewed window is still bounded by probe_timeout, not extended.
        self.assertLessEqual(life.baseline.verifier.deadline, clock()+90)

    def test_without_renewal_the_real_verifier_deadline_expires(self):
        clock = Clock()
        life, installation, _ = self.lifecycle(clock)
        calls = []
        original = AutomaticLifecycle._verification_window
        def first_only(self, inst):
            calls.append(clock())
            if len(calls) == 1:
                original(self, inst)
        patches = self.patched(clock)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             mock.patch.object(AutomaticLifecycle, '_verification_window', first_only), \
             self.assertRaisesRegex(VerificationError, 'discovery_incomplete'):
            life.select(installation, candidate='candidate')

    def test_exhausted_tick_deadline_still_fails_closed_after_observation(self):
        clock = Clock()
        # Enough tick for the first verification, not for one 180 s observation.
        life, installation, verified = self.lifecycle(clock, tick_seconds=120)
        patches = self.patched(clock)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             self.assertRaisesRegex(VerificationError, 'discovery_incomplete'):
            life.select(installation, candidate='candidate')
        self.assertEqual(len(verified), 1)

    def test_facts_probe_renews_before_evidence_reverification(self):
        clock = Clock()
        life, installation, verified = self.lifecycle(clock)
        life.target = SimpleNamespace()
        life.executor_digest = lambda root: '7'*64

        class Provider:
            def __init__(self, baseline, target, *, probe, **kwargs):
                self.baseline, self.probe = baseline, probe

            def __call__(self, fresh, boundary):
                self.probe(fresh, boundary)
                self.baseline.observe(None, None)  # EvidenceFacts -> BaselineStore.observe
                return SimpleNamespace(executor_sha256=None)
        patches = self.patched(clock)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             mock.patch.object(automatic, 'EvidenceFacts', Provider), \
             mock.patch.object(automatic, 'replace', lambda value, **changes: value):
            life.facts(installation, 'before_recovery')
        # One retained verification inside the slow probe, one after it.
        self.assertEqual(len(verified), 2)
        self.assertGreaterEqual(verified[1][0]-verified[0][0], 180)
        clock2 = Clock()
        life2, installation2, verified2 = self.lifecycle(clock2, tick_seconds=120)
        life2.target, life2.executor_digest = SimpleNamespace(), lambda root: '7'*64
        patches = self.patched(clock2)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             mock.patch.object(automatic, 'EvidenceFacts', Provider), \
             self.assertRaisesRegex(VerificationError, 'discovery_incomplete'):
            life2.facts(installation2, 'before_recovery')


class SuccessAndRestoreBoundaryTests(WindowFixture, unittest.TestCase):
    """Post-observation re-verification on the success and restore paths.

    The stub baseline re-verifies exactly where the real BaselineStore does:
    validate_success_observation (target and predecessor), retain_success
    (validates again after the successor backup capture) and commit_restore /
    reconcile_restore (validate_restore_observation re-verifies A).
    """

    def boundary(self, clock, *, tick_seconds=14400):
        life, installation, verified = self.lifecycle(clock, tick_seconds=tick_seconds)
        baseline = life.baseline
        evidence = baseline.evidence
        baseline.validate_success_observation = lambda intent, observation: (evidence('target'), evidence('head'))
        baseline.retain_success = lambda intent, observation, backup: baseline.validate_success_observation(intent, observation)
        baseline.commit_restore = lambda intent, **kwargs: evidence('head')
        baseline.restore_intent = lambda attempt_id: SimpleNamespace()
        life.intent = SimpleNamespace(target='target')
        life.ledger = SimpleNamespace(direction='forward_pending')
        life.restore_intent = SimpleNamespace()
        engine = SimpleNamespace(installation=installation)
        return life, installation, engine, verified

    def run_patched(self, clock, call):
        patches = self.patched(clock)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            return call()

    def test_success_session_evidence_and_post_stability_validation_use_fresh_windows(self):
        clock = Clock()
        life, installation, _, verified = self.boundary(clock)
        def scenario():
            life._verification_window(installation)       # last guard before env commit/create
            clock.now += 60                               # env commit + Compose create
            life.target_evidence(installation)            # evidence for the success session
            clock.now += 180 + 25                         # B's full stability observation
            life.validate_success(installation, SimpleNamespace())
        self.run_patched(clock, scenario)
        self.assertEqual(len(verified), 3)
        self.assertTrue(all(0 < remaining <= 30 for _, remaining in verified))

    def test_retain_success_reverifies_after_the_slow_successor_capture(self):
        clock = Clock()
        life, installation, _, verified = self.boundary(clock)
        def capture(*args):
            clock.now += 120                              # bounded helper capture
            return b'{}'
        life._successor_backup = capture
        def scenario():
            life._verification_window(installation)       # window from the last guard
            clock.now += 205                              # preceding stability observation
            life.retain_success(installation, SimpleNamespace())
        self.run_patched(clock, scenario)
        self.assertEqual(len(verified), 4)
        self.assertGreaterEqual(verified[2][0]-verified[1][0], 120)

    def test_complete_restore_and_verify_success_reverify_after_full_observation(self):
        clock = Clock()
        life, installation, engine, verified = self.boundary(clock)
        life.ledger = SimpleNamespace(direction='restore_decided')
        self.run_patched(clock, lambda: life.complete_restore(engine, SimpleNamespace(name='restore.json')))
        self.assertEqual(len(verified), 2)  # evidence before, and commit_restore after, the observation
        self.assertGreaterEqual(verified[1][0]-verified[0][0], 180)
        clock2 = Clock()
        life2, _, engine2, verified2 = self.boundary(clock2)
        self.run_patched(clock2, lambda: life2.verify_success(engine2))
        self.assertEqual(len(verified2), 3)

    def test_exhausted_forward_deadline_still_fails_closed_at_success_validation(self):
        clock = Clock()
        life, installation, _, verified = self.boundary(clock, tick_seconds=200)
        def scenario():
            life.target_evidence(installation)
            clock.now += 205                              # past the whole forward deadline
            life.validate_success(installation, SimpleNamespace())
        with self.assertRaisesRegex(VerificationError, 'discovery_incomplete'):
            self.run_patched(clock, scenario)
        self.assertEqual(len(verified), 1)



class FailedAvailableRecoveryTests(WindowFixture, unittest.TestCase):
    """An unsealed aborted attempt re-verifies A after A's full stability observation."""

    def recovery(self, clock, *, tick_seconds=14400):
        from deploy.update.baseline import sha
        life, installation, verified = self.lifecycle(clock, tick_seconds=tick_seconds)
        observed = SimpleNamespace(env_bytes=b'env', structure_sha256='s', facts=SimpleNamespace(
            config_fingerprint='c', profile_fingerprint='p', deployment_files={'docker-compose.yml': 'h'}))
        life.head = SimpleNamespace(release='head', config_fingerprint='c', profile_fingerprint='p',
                                    env_sha256=sha(b'env'), structure_sha256='s')
        life.execution = {'files': {'docker-compose.yml': 'h'}}

        def slow(inst, previous, boundary):
            clock.now += inst.runner.policy.stability_period_seconds + 25
            return observed
        life._observation = slow
        life.baseline._observe = lambda observation, previous: None
        finished = []
        # BaselineStore.finish_failed_available re-verifies old.release (baseline.py).
        life.baseline.finish_failed_available = lambda intent, observation: finished.append(
            life.baseline.evidence('head'))
        life.ledger = SimpleNamespace(direction='forward_pending', backup_seal=None,
                                      phase='failed_available', snapshot={'pending': None})
        life.attempt_intent = SimpleNamespace()
        return life, installation, verified, finished

    def test_failed_available_is_finalized_in_a_fresh_window(self):
        clock = Clock()
        life, installation, verified, finished = self.recovery(clock)
        patches = self.patched(clock)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            life.verify_current(installation)
        self.assertEqual(len(finished), 1)
        self.assertEqual(len(verified), 2)
        self.assertGreaterEqual(verified[1][0]-verified[0][0], 180)
        self.assertTrue(all(0 < remaining <= 30 for _, remaining in verified))

    def test_exhausted_recovery_deadline_still_fails_closed_before_finalizing(self):
        clock = Clock()
        life, installation, verified, finished = self.recovery(clock, tick_seconds=120)
        patches = self.patched(clock)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             self.assertRaisesRegex(VerificationError, 'discovery_incomplete'):
            life.verify_current(installation)
        self.assertEqual(finished, [])


if __name__ == '__main__':
    unittest.main()
