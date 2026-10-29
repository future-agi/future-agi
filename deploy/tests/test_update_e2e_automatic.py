"""Real product, one opt-in, installed scheduler forward and restore acceptance.

Only the pre-existing explicit Docker opt-in gates this suite. No feature skips,
manual ticks, forged host facts, image fixtures or synthetic proof fallback.
Native CI creates frozen inputs via update_e2e/acceptance.py. Product proof
production must pass before any acceptance stack is installed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.update_e2e import harness as e2e
from deploy.tests.update_e2e.real_automatic import acceptance_policy, policy_cli_arguments, read_attempt_ledger
from deploy.update.baseline import BaselineStore
from deploy.update.enrollment import AUTOMATIC_UPDATE_DISCLOSURE
from deploy.update.host_facts import selected_store
from deploy.update.scheduler_install import inspect_activation
from deploy.update.enrollment import load_setup_configuration


@unittest.skipUnless(os.environ.get("FUTURE_AGI_UPDATE_E2E") == "1", "real E2E opt-in required")
class AutomaticLifecycleE2E(unittest.TestCase):
    def test_one_opt_in_scheduled_A_B_C(self):
        path = os.environ.get('FAGI_UPDATE_ACCEPTANCE_INPUTS')
        self.assertTrue(path, 'native acceptance.py must supply frozen real release inputs')
        inputs = json.loads(Path(path).read_bytes())
        versions = tuple(inputs['versions'])
        if inputs['mode'] == 'a-b-c':
            self.assertEqual(versions, ('v1.53.0', 'v1.54.0', 'v1.55.0'))
        else:
            self.assertEqual(inputs['mode'], 'upgrade')
            self.assertEqual(len(versions), 2)
        h = e2e.Harness(base=Path(inputs['base']))  # DO NOT call fake-image start().
        with h.automatic_publisher(lane='product', versions=versions, retained_layout=versions[0],
                candidates={v: inputs['candidates'][v] for v in versions},
                deployment_dirs=inputs['deployment_dirs'], run_id=inputs['run_id']) as publisher:
            # Qualify the immutable graph ONCE, without any enrolled installation.
            # Repeating full product qualification between cases used up the job
            # deadline without testing any additional installed-scheduler path.
            for version in versions:
                publisher.prepare(version)
            transitions = {target: publisher.transition(source, target)
                           for source, target in zip(versions, versions[1:])}
            policy = acceptance_policy(list(transitions.values()))
            for restore in (False, True):
                cleaned_up = False
                with self.subTest(scenario='failure-after-migration-restore' if restore else 'forward'):
                    # The preceding case must fully deactivate/clean up before
                    # this independent channel view and fresh A installation.
                    publisher.hide_releases()
                    self.exercise(inputs, versions[:2] if restore else versions,
                                  publisher, transitions, policy, restore=restore)
                    cleaned_up = True
                if not cleaned_up:
                    # subTest records the failure but suppresses its exception.
                    # Do not start another stack beside uncertain/pending recovery.
                    return

    def exercise(self, inputs, versions, publisher, transitions, policy, *, restore):
        deadline = inputs['deadline_monotonic']
        def remaining_timeout():
            # Never start a tick whose protected recovery reserve cannot fit in
            # the remaining job allowance. Qualification/boot share this bound.
            required = policy.tick_timeout_seconds + policy.check_interval_seconds + 60
            self.assertGreater(deadline-time.monotonic(), required,
                               'measured acceptance budgets exceed remaining native job time')
            return required
        publisher.publish(versions[0])
        checkout = publisher.new_checkout('restore' if restore else 'one-opt-in')
        enrolled = False
        failure = None
        try:
            checkout.install()
            verify_data = publisher.seed_and_check(checkout)
            source_images = {c['Config']['Labels']['com.docker.compose.service']: c['Image']
                             for c in checkout.containers()}
            initial_migrations = checkout.migration_inventory()
            destination = checkout.evidence/'backups'
            # --yes acknowledges precisely the displayed A5 disclosure. No
            # scheduler asset is rendered/installed by this test itself.
            remaining_timeout()
            checkout.record('acceptance-budgets.json', dict(required_tick_seconds=policy.required_tick_budget_seconds,
                configured_tick_seconds=policy.tick_timeout_seconds, cli=list(policy_cli_arguments(policy))))
            setup = checkout.bin_update('setup', '--mode', 'apply', '--yes', '--policy', 'minor',
                '--window', 'always', '--check-interval', '300', '--scheduler', 'cron',
                '--cron-service', 'cron.service', '--docker-context', checkout.context,
                '--release-source', publisher.source, '--public-key', str(publisher.public_key),
                '--ca-file', str(publisher.ca_file), '--backup-destination', str(destination),
                *policy_cli_arguments(policy))
            self.assertIn(AUTOMATIC_UPDATE_DISCLOSURE, setup.stdout.decode())
            enrolled = True
            store = selected_store(checkout.root)
            baseline = BaselineStore(store)
            self.assertEqual(baseline.current().release.version, versions[0])
            registration = store.read_registration()
            configuration = load_setup_configuration(store)
            self.assertTrue(inspect_activation(store, configuration.policy, **configuration.scheduler_options).active)
            source_head = baseline.current()
            runs = set()
            proofs_before = set(destination.rglob('proof.json'))
            previous_migrations = initial_migrations
            previous_app = source_images['app']
            for position, version in enumerate(versions[1:], 1):
                timeout = remaining_timeout()
                row = transitions[version]
                migration = row['migration_evidence']
                self.assertEqual(previous_migrations['inventory_sha256'], migration['source_inventory_sha256'])
                self.assertEqual(previous_migrations['applied_sha256'], migration['source_applied_set_sha256'])
                # The ONLY update trigger is publisher visibility. We do not
                # call run/apply/retry, rewrite policies or re-enroll.
                publisher.publish(version)
                if restore:
                    fault = checkout.fail_after_migrations(publisher._prepared[version]['manifest'], timeout=timeout)
                    self.assertFalse(fault['pending'])
                    self.assertEqual(fault['inventory_sha256'], migration['target_inventory_sha256'])
                    self.assertEqual(fault['applied_sha256'], migration['target_applied_set_sha256'])
                    e2e.wait_for(lambda: any(r.get('kind') == 'restore' and r.get('status') == 'restored'
                        and r.get('target_version') == version for r in checkout.receipts()),
                        timeout=min(timeout, deadline-time.monotonic()), interval=2,
                        what='installed scheduler to restore source after target migration')
                    self.assertEqual(baseline.current().release, source_head.release)
                    self.assertEqual({c['Config']['Labels']['com.docker.compose.service']: c['Image']
                                      for c in checkout.containers()}, source_images)
                    self.assertEqual(checkout.migration_inventory(), initial_migrations)
                    checkout.verify_candidate_only_absent()
                    outcome = 'restored'
                else:
                    e2e.wait_for(lambda: baseline.current().release.version == version and any(
                        r.get('kind') == 'apply' and r.get('status') == 'succeeded' and r.get('target_version') == version
                        for r in checkout.receipts()), timeout=min(timeout, deadline-time.monotonic()), interval=2,
                        what='installed scheduler to advance '+version+' and publish its receipt')
                    outcome = 'succeeded'
                receipts = [r for r in checkout.receipts() if r.get('status') == outcome
                            and r.get('target_version') == version]
                self.assertEqual(len(receipts), 1, 'exactly one terminal receipt per update')
                if not restore:
                    before_names = set(map(tuple, previous_migrations['applied']))
                    previous_migrations = checkout.verify_installed_release(
                        publisher._prepared[version]['manifest'], receipts[0], migration)
                    new_names = set(map(tuple, previous_migrations['applied'])) - before_names
                    pending_names = {(m['app_label'], m['name']) for m in row['migration_measurements']}
                    self.assertEqual(new_names, pending_names,
                        'qualified pending migrations must actually be applied by the target')
                    self.assertEqual(len(new_names), migration['target_pending_plan_count'])
                    self.assertNotEqual(receipts[0]['target_image_ids']['app'], previous_app,
                                        'each successor must actually replace the preceding app image')
                    previous_app = receipts[0]['target_image_ids']['app']
                    dependencies = {c['Config']['Labels']['com.docker.compose.service']: c['Image']
                                    for c in checkout.containers()
                                    if c['Config']['Labels']['com.docker.compose.service'] != 'app'}
                    self.assertEqual(dependencies, {s: i for s, i in source_images.items() if s != 'app'},
                                     'the retained dependency images must not change')
                self.assertTrue(receipts[0]['run_id'])
                self.assertNotIn(receipts[0]['run_id'], runs)
                runs.add(receipts[0]['run_id'])
                attempts = [store.read_attempt(p.stem) for p in (store.path/'attempts').glob('*.json')]
                completed = [a for a in attempts if a.target.version == version and a.outcome == outcome]
                self.assertEqual(len(completed), 1)
                ledger, _ = read_attempt_ledger(destination/completed[0].attempt_id)
                self.assertEqual(ledger['run_id'], receipts[0]['run_id'])
                self.assertIsNotNone(completed[0].backup_proof_identity)
                proofs_after = set(destination.rglob('proof.json'))
                self.assertEqual(len(proofs_after-proofs_before), 1, 'exactly one cold backup per automatic update')
                proofs_before = proofs_after
                self.assertEqual(store.read_registration(), registration, 'no re-enrollment')
                self.assertEqual(load_setup_configuration(store), configuration, 'no policy/source/key/CA changes')
                verify_data()
                if not restore and position < len(versions)-1:
                    # Only after B's independent installed-image oracle passes.
                    # This checker retains BOTH A and B generations through C.
                    verify_data = checkout.seed_and_check('acceptance-'+version)
                self.assertTrue(inspect_activation(store, configuration.policy, **configuration.scheduler_options).active)
            self.assertEqual(len(runs), len(versions)-1)
            checkout.record('acceptance-result.json', dict(status='passed', scenario='restore' if restore else 'forward',
                versions=list(versions), scheduler='setup-installed-cron', setup_calls=1, automatic_runs=len(runs),
                backups=len(proofs_before), witness_generations=list(checkout._witness_generations),
                witnesses_intact=True, product_acceptance=True))
        except BaseException as error:
            failure = error
            raise
        finally:
            # Diagnostics are read-only and bounded. Never let an evidence error
            # hide the original failure or race an installed updater with cleanup.
            checkout.collect_diagnostics(failure)
            log_failure = None
            try:
                checkout.collect_logs()
            except Exception as error:
                log_failure = error
                checkout.collect_diagnostics(failure or error)
            # A refusal retains the stack and recovery source; do not clean up.
            if enrolled or selected_store(checkout.root).path.exists():
                checkout.bin_update('setup', '--deactivate', '--yes')
            checkout.cleanup()
            if failure is None and log_failure is not None:
                raise log_failure


if __name__ == '__main__':
    unittest.main()
