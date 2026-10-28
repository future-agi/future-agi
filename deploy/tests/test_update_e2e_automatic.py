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
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.update_e2e import harness as e2e
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
        self.assertGreaterEqual(len(versions), 2)
        # Both cases use real installations. Release qualification happens with
        # no enrolled stack running. Cleanup is exact and verified between cases.
        with self.subTest(scenario='forward'):
            self.exercise(inputs, versions, restore=False)
        with self.subTest(scenario='failure-after-migration-restore'):
            self.exercise(inputs, versions[:2], restore=True)

    def exercise(self, inputs, versions, *, restore):
        h = e2e.Harness(base=Path(inputs['base']))  # DO NOT call fake-image start().
        with h.automatic_publisher(lane='product', versions=versions, retained_layout=versions[0],
                candidates={v:inputs['candidates'][v] for v in versions},
                deployment_dirs=inputs['deployment_dirs'], run_id=inputs['run_id']) as publisher:
            for version in versions:
                publisher.prepare(version)  # qualify/package entrypoints; hard failure if unimplemented
            publisher.publish(versions[0])
            checkout = publisher.new_checkout('restore' if restore else 'one-opt-in')
            enrolled = False
            try:
                checkout.install()
                verify_data = publisher.seed_and_check(checkout)
                source_images = {c['Config']['Labels']['com.docker.compose.service']: c['Image']
                                 for c in checkout.containers()}
                initial_migrations = checkout.migration_inventory()
                destination = checkout.evidence/'backups'
                # --yes acknowledges precisely the displayed A5 disclosure. No
                # scheduler asset is rendered/installed by this test itself.
                setup = checkout.bin_update('setup','--mode','apply','--yes','--policy','minor',
                    '--window','always','--check-interval','300','--scheduler','cron',
                    '--cron-service','cron.service','--docker-context',checkout.context,
                    '--release-source',publisher.source,'--public-key',str(publisher.public_key),
                    '--ca-file',str(publisher.ca_file),'--backup-destination',str(destination),
                    '--wait-timeout-seconds','4200','--migration-timeout-seconds','4200',
                    '--recovery-startup-timeout-seconds','4200','--tick-timeout-seconds','19800',
                    '--migration-stall-timeout-seconds','300','--stability-period-seconds','60')
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
                for version in versions[1:]:
                    # The ONLY update trigger is publisher visibility. We do not
                    # call run/apply/retry, rewrite policies or re-enroll.
                    publisher.publish(version)
                    if restore:
                        fault = checkout.fail_after_migrations(publisher._prepared[version]['manifest'])
                        self.assertFalse(fault['pending'])
                        e2e.wait_for(lambda: any(r.get('kind') == 'restore' and r.get('status') == 'restored'
                            and r.get('target_version') == version for r in checkout.receipts()),
                            timeout=10800,interval=2,what='installed scheduler to restore source after target migration')
                        self.assertEqual(baseline.current().release,source_head.release)
                        self.assertEqual({c['Config']['Labels']['com.docker.compose.service']:c['Image']
                                          for c in checkout.containers()},source_images)
                        self.assertEqual(checkout.migration_inventory(),initial_migrations)
                        outcome='restored'
                    else:
                        e2e.wait_for(lambda: baseline.current().release.version == version,
                                     timeout=10800,interval=2,what='installed scheduler to advance '+version)
                        outcome='succeeded'
                    receipts=[r for r in checkout.receipts() if r.get('status') == outcome
                              and r.get('target_version') == version]
                    self.assertEqual(len(receipts),1, 'exactly one terminal receipt per update')
                    runs.add(receipts[0]['run_id'])
                    attempts=[store.read_attempt(p.stem) for p in (store.path/'attempts').glob('*.json')]
                    completed=[a for a in attempts if a.target.version == version and a.outcome == outcome]
                    self.assertEqual(len(completed),1)
                    self.assertIsNotNone(completed[0].backup_proof_identity)
                    proofs_after=set(destination.rglob('proof.json'))
                    self.assertEqual(len(proofs_after-proofs_before),1,'exactly one cold backup per automatic update')
                    proofs_before=proofs_after
                    self.assertEqual(store.read_registration(),registration,'no re-enrollment')
                    self.assertEqual(load_setup_configuration(store),configuration,'no policy/source/key/CA changes')
                    verify_data()
                    self.assertTrue(inspect_activation(store,configuration.policy,**configuration.scheduler_options).active)
                self.assertEqual(len(runs),len(versions)-1)
                checkout.record('acceptance-result.json',dict(status='passed',scenario='restore' if restore else 'forward',
                    versions=list(versions),scheduler='setup-installed-cron',setup_calls=1,automatic_runs=len(runs),
                    backups=len(proofs_before),witnesses_intact=True,product_acceptance=True))
            finally:
                # Never race an installed updater with teardown. If safe removal
                # refuses (e.g. recovery still pending), retain the stack/evidence
                # and fail rather than deleting a recovery source underneath it.
                if enrolled or selected_store(checkout.root).path.exists():
                    checkout.bin_update('setup','--deactivate','--yes')
                checkout.collect_logs()
                checkout.cleanup()


if __name__ == '__main__':
    unittest.main()
