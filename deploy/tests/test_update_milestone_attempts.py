"""Repeated producer attempts use create-only proof leaves, never overwrite one."""
from contextlib import nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy.tests import test_update_standalone_backup as fixtures
from deploy.tests.test_update_e2e_backup import observation
from deploy.tests.test_update_standalone_restore import LocalHelpers
from deploy.update import standalone_restore
from scripts import update_rehearsal_milestone as milestone


class MilestoneAttemptProofTests(unittest.TestCase):
    def test_two_actual_driver_snapshots_retain_distinct_attempt_proofs(self):
        # Real backup, archives, proof validation and ledger; explicit synthetic
        # Docker/storage fixture. This is not a product qualification result.
        f = fixtures.BackupTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        driver = object.__new__(milestone.SnapshotMilestone)
        driver.root = driver.directory = f.root
        driver.installation, driver.binding = f.install, f.binding
        driver.context, driver.daemon, driver.project = 'test-backup', 'daemon-1', 'test-backup'
        driver.registration = SimpleNamespace(binding=SimpleNamespace(installation_fingerprint='2'*64))
        driver.image_inspections = {service: {'Id': image} for service, image in f.daemon.images.items()}
        driver.execution = {'files': {key: value for key, value in json.loads(f.binding.raw)['files'].items()
                                      if key != '.env'}}
        driver.descriptor = {'fixture': 'synthetic-source'}
        driver.source_observation = replace(observation(f.binding, dict(zip(f.daemon.images,
            f.daemon.containers.values())), fixtures.NOW), release_version=milestone.VERSION)
        driver.locks = nullcontext
        LocalHelpers.calls, LocalHelpers.wrong_tree = [], False
        retained = {}
        with mock.patch.object(standalone_restore, '_StorageHelpers', LocalHelpers):
            for index in range(2):
                driver.output = f.root / ('attempt-output-' + str(index))
                driver.output.mkdir(mode=0o700)
                # Model the preceding recovery's healthy source without issuing
                # real Docker commands or mutating any external installation.
                for container in f.daemon.containers.values():
                    container['State']['Running'] = True
                driver.take_snapshot()
                proof = driver.proofs / (driver.expected.attempt_id + '.json')
                self.assertEqual(json.loads(proof.read_bytes())['attempt_id'], driver.expected.attempt_id)
                self.assertEqual(proof.read_bytes(), (driver.ledger.directory / 'proof.json').read_bytes())
                retained[proof.name] = proof.read_bytes()
                for name, raw in retained.items():
                    self.assertEqual((driver.proofs / name).read_bytes(), raw)
        self.assertEqual(len(retained), 2)
        self.assertEqual({path.name for path in driver.proofs.iterdir()}, set(retained))
        self.assertFalse((driver.proofs / 'proof.json').exists())


if __name__ == '__main__':
    unittest.main()
