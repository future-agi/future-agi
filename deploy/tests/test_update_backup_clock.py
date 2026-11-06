"""Whole-second proof chronology with microsecond attempt reservations."""
from dataclasses import replace
from datetime import timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy.tests import test_update_standalone_backup as fixtures
from deploy.tests.test_update_e2e_backup import observation
from deploy.tests.test_update_standalone_restore import LocalHelpers
from deploy.update import standalone_backup as backup
from deploy.update import standalone_restore as restore
from deploy.update.backup_proof import validate_backup_proof
from deploy.update.recovery import RecoveryLedger


class BackupClockTests(unittest.TestCase):
    def adapter(self, now, reserved, *, deadline=20):
        adapter = object.__new__(backup.StandaloneBackup)
        adapter.clock = mock.Mock(return_value=now)
        adapter.deadline = deadline
        return adapter, SimpleNamespace(reserved_at=reserved)

    def test_fractional_reservation_waits_before_any_backup_mutation(self):
        for cold in (False, True):
            with self.subTest(cold=cold):
                f = fixtures.BackupTests()
                f.setUp()
                try:
                    f.expected = replace(f.expected, reserved_at=fixtures.NOW + timedelta(microseconds=250000))
                    now = [fixtures.NOW + timedelta(microseconds=500000)]
                    f.adapter.clock = lambda: now[0]
                    def wait(seconds):
                        self.assertEqual(f.mutation_calls(), [])
                        self.assertEqual(seconds, .5)
                        now[0] += timedelta(seconds=seconds)
                    with mock.patch.object(backup.time, 'sleep', side_effect=wait) as sleep:
                        if cold:
                            ledger = RecoveryLedger.reserve(f.destination, f.expected,
                                operation_token='token', run_id='run', binding_sha256='2'*64)
                            source = replace(observation(f.binding, dict(zip(f.daemon.images,
                                f.daemon.containers.values())), fixtures.NOW),
                                release_version=f.expected.source_version)
                            LocalHelpers.calls, LocalHelpers.wrong_tree = [], False
                            with mock.patch.object(restore, '_StorageHelpers', LocalHelpers):
                                proof = f.adapter.run_quiesced(f.expected, ledger, source,
                                    proof_directory_fd=f.proof_fd).proof
                        else:
                            proof = f.run_backup()
                    sleep.assert_called_once_with(.5)
                    self.assertEqual(proof.started_at, fixtures.NOW + timedelta(seconds=1))
                    self.assertEqual(validate_backup_proof(proof.raw, f.expected, now=now[0]).raw, proof.raw)
                    self.assertEqual(f.expected.reserved_at.microsecond, 250000)
                finally:
                    f.doCleanups()

    def test_elapsed_whole_second_does_not_sleep(self):
        adapter, expected = self.adapter(fixtures.NOW + timedelta(seconds=1, microseconds=100),
            fixtures.NOW + timedelta(microseconds=250000))
        with mock.patch.object(backup.time, 'monotonic', return_value=10), \
             mock.patch.object(backup.time, 'sleep') as sleep:
            self.assertEqual(adapter._started_at(expected), fixtures.NOW + timedelta(seconds=1))
        sleep.assert_not_called()

    def test_timezone_is_normalized_before_proof_boundary(self):
        instant = fixtures.NOW + timedelta(seconds=1)
        adapter, expected = self.adapter(instant.astimezone(timezone(timedelta(hours=5, minutes=30))), fixtures.NOW)
        with mock.patch.object(backup.time, 'monotonic', return_value=10):
            self.assertEqual(adapter._started_at(expected), instant)

    def test_real_subsecond_clock_regression_is_not_hidden_by_rounding(self):
        adapter, expected = self.adapter(fixtures.NOW + timedelta(microseconds=200000),
            fixtures.NOW + timedelta(microseconds=250000))
        with mock.patch.object(backup.time, 'sleep') as sleep, \
             self.assertRaisesRegex(backup.BackupError, 'backup_clock_skew'):
            adapter._started_at(expected)
        sleep.assert_not_called()

    def test_wait_cannot_spend_an_exhausted_budget(self):
        adapter, expected = self.adapter(fixtures.NOW + timedelta(microseconds=500000),
            fixtures.NOW + timedelta(microseconds=250000), deadline=10.5)
        with mock.patch.object(backup.time, 'monotonic', return_value=10), \
             mock.patch.object(backup.time, 'sleep') as sleep, \
             self.assertRaisesRegex(backup.BackupError, 'backup_timeout'):
            adapter._started_at(expected)
        sleep.assert_not_called()

    def test_stalled_or_regressed_clock_after_wait_refuses(self):
        now = fixtures.NOW + timedelta(microseconds=500000)
        for after in (now, now - timedelta(microseconds=1)):
            with self.subTest(after=after):
                adapter, expected = self.adapter(now, fixtures.NOW + timedelta(microseconds=250000))
                adapter.clock.side_effect = [now, after]
                with mock.patch.object(backup.time, 'monotonic', return_value=10), \
                     mock.patch.object(backup.time, 'sleep'), \
                     self.assertRaisesRegex(backup.BackupError, 'backup_clock_skew'):
                    adapter._started_at(expected)

    def test_late_wakeup_cannot_start_backup(self):
        adapter, expected = self.adapter(fixtures.NOW + timedelta(microseconds=500000),
            fixtures.NOW + timedelta(microseconds=250000), deadline=11)
        adapter.clock.side_effect = [fixtures.NOW + timedelta(microseconds=500000),
                                    fixtures.NOW + timedelta(seconds=2)]
        with mock.patch.object(backup.time, 'monotonic', side_effect=[10, 12]), \
             mock.patch.object(backup.time, 'sleep'), \
             self.assertRaisesRegex(backup.BackupError, 'backup_timeout'):
            adapter._started_at(expected)


if __name__ == '__main__':
    unittest.main()
