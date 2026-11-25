"""Real private-file/fsync and subprocess-death tests; no product qualification."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update.backup_proof import BackupExpectation
from deploy.update.contracts import BackupSeal
from deploy.update.recovery import RecoveryError, RecoveryLedger, PHASES, VOLUME_PHASES
from deploy.update.standalone_backup import DOMAINS, create_destination

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def expected(attempt='1' * 32):
    return BackupExpectation(attempt, '2' * 64, '3' * 64, 'v1.53.0', NOW, DOMAINS)


def seal_for(ledger):
    doc = ledger.snapshot
    return BackupSeal(doc['attempt_id'], doc['operation_token'], doc['run_id'], doc['binding_sha256'],
                      '3' * 64, 'v1.53.0', '4' * 64, '5' * 64, '6' * 64, '7' * 64, NOW, NOW)


class RecoveryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fagi-restore-ledger-unit-', dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)
        self.destination.chmod(0o700)
        self.expected = expected()
        self.ledger = RecoveryLedger.reserve(self.destination, self.expected,
            operation_token='token', run_id='run', binding_sha256='8' * 64, old_head='head')

    def reopen(self):
        return RecoveryLedger.open(self.ledger.directory, expected=self.expected,
                                   operation_token='token', run_id='run', binding_sha256='8' * 64)

    def prepare_restore(self):
        self.ledger.seal_backup(seal_for(self.ledger))
        self.ledger.decide_restore()

    def test_exact_attempt_token_run_binding_no_reuse_and_defensive_snapshot(self):
        doc = self.ledger.snapshot
        doc['direction'] = 'success_decided'
        self.assertEqual(self.reopen().direction, 'forward_pending')
        for change in ({'operation_token': 'other'}, {'run_id': 'other'}, {'binding_sha256': '9' * 64}):
            with self.subTest(change=change), self.assertRaises(RecoveryError):
                RecoveryLedger.open(self.ledger.directory, **change)
        with self.assertRaises(FileExistsError):
            RecoveryLedger.reserve(self.destination, self.expected, operation_token='token', run_id='run', binding_sha256='8' * 64)

    def test_intent_and_result_durable_and_pending_never_retried_blindly(self):
        operation = self.ledger.begin('stop', identity={'id': 'a' * 64})
        self.assertEqual(self.reopen().snapshot['pending']['identity']['id'], 'a' * 64)
        self.assertTrue((self.ledger.directory / 'op-0001-intent.json').exists())
        with self.assertRaises(RecoveryError):
            self.reopen().begin('stop')
        self.ledger.complete(operation, {'stopped': True})
        self.assertIsNone(self.reopen().snapshot['pending'])
        self.assertTrue((self.ledger.directory / 'op-0001-result.json').exists())

    def test_direction_monotonic_success_and_restore_are_mutually_exclusive(self):
        self.prepare_restore()
        with self.assertRaises(RecoveryError):
            self.ledger.decide_success({'observation': '9' * 64})
        self.assertEqual(self.reopen().direction, 'restore_decided')
        self.ledger.decide_restore('does_not_change_original_decision')
        other = RecoveryLedger.reserve(self.destination, expected('a' * 32), operation_token='t', run_id='r', binding_sha256='8' * 64)
        other.seal_backup(seal_for(other))
        other.decide_success({'observation': '9' * 64})
        other.decide_success({'observation': '9' * 64})
        with self.assertRaises(RecoveryError):
            other.decide_restore()
        with self.assertRaises(RecoveryError):
            other.decide_success({'observation': 'a' * 64})
        self.assertEqual(other.resume_direction(), 'success_decided')

    def test_same_instance_signal_gap_after_immutable_decision_never_changes_direction(self):
        self.ledger.seal_backup(seal_for(self.ledger))
        with mock.patch.object(self.ledger, '_save', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.ledger.decide_success({'observation': '9' * 64})
        # The checkpoint call never ran, so poisoning inside _save is not enough.
        # A caller catching the interrupt must still not restore through this
        # exact same stale object after the success decision became durable.
        with self.assertRaises(RecoveryError):
            self.ledger.decide_restore()
        self.assertFalse((self.ledger.directory / 'restore-decision.json').exists())
        self.assertEqual(self.reopen().direction, 'success_decided')
        other = RecoveryLedger.reserve(self.destination, expected('b' * 32), operation_token='t', run_id='r', binding_sha256='8' * 64)
        other.seal_backup(seal_for(other))
        with mock.patch.object(other, '_save', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                other.decide_restore()
        with self.assertRaises(RecoveryError):
            other.decide_success({'observation': '9' * 64})
        self.assertFalse((other.directory / 'success-decision.json').exists())
        self.assertEqual(RecoveryLedger.open(other.directory).direction, 'restore_decided')

    def test_interrupted_sealed_forward_only_selects_restore(self):
        self.assertEqual(self.ledger.resume_direction(), 'forward_pending')
        self.ledger.seal_backup(seal_for(self.ledger))
        self.assertEqual(self.reopen().resume_direction(), 'restore_decided')

    def test_once_start_armed_all_old_and_new_instances_forbid_clear_and_extract(self):
        self.prepare_restore()
        for volume in ('app-data', 'postgres-data', 'clickhouse-data'):
            for phase in VOLUME_PHASES:
                self.ledger.volume_phase(volume, phase)
        self.ledger.checkpoint('restore_data_committed')
        stale = self.reopen()
        self.ledger.arm_previous_start()
        for ledger in (self.ledger, stale, self.reopen()):
            for action in (lambda: ledger.assert_data_mutable(),
                           lambda: ledger.begin('clear'), lambda: ledger.begin('extract'),
                           lambda: ledger.volume_phase('app-data', 'clear_intent', restart=True)):
                with self.assertRaises(RecoveryError):
                    action()
        self.reopen().checkpoint('previous_verifying')
        self.assertTrue(self.reopen().snapshot['previous_start_armed'])

    def test_verified_store_never_restarts_extraction_and_partial_store_may_restart(self):
        self.prepare_restore()
        self.ledger.volume_phase('app-data', 'clear_intent')
        self.ledger.volume_phase('app-data', 'empty')
        self.ledger.volume_phase('app-data', 'clear_intent', restart=True)
        for phase in VOLUME_PHASES[1:]:
            self.ledger.volume_phase('app-data', phase)
        with self.assertRaises(RecoveryError):
            self.ledger.volume_phase('app-data', 'clear_intent', restart=True)

    def test_checkpoint_cas_and_fsync_failure_do_not_allow_further_mutation(self):
        other = self.reopen()
        self.ledger.checkpoint('quiescing')
        with self.assertRaisesRegex(RecoveryError, 'recovery_checkpoint_conflict'):
            other.checkpoint('quiesced')
        with mock.patch('deploy.update.standalone_backup.sync_file', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                self.ledger.checkpoint('quiesced')
        with self.assertRaises(RecoveryError):
            self.ledger.checkpoint('quiesced')
        self.assertEqual(self.reopen().phase, 'quiescing')

    def test_immutable_decisions_and_start_barrier_win_crash_before_checkpoint(self):
        # Fault AFTER immutable evidence fsync, BEFORE checkpoint replacement.
        for method in ('seal', 'restore', 'barrier'):
            with self.subTest(method=method):
                attempt = hashlib.md5(method.encode()).hexdigest()
                ledger = RecoveryLedger.reserve(self.destination, expected(attempt), operation_token='t', run_id='r', binding_sha256='8' * 64)
                if method != 'seal':
                    ledger.seal_backup(seal_for(ledger))
                if method == 'barrier':
                    ledger.decide_restore()
                    for volume in ('app-data', 'postgres-data', 'clickhouse-data'):
                        for phase in VOLUME_PHASES:
                            ledger.volume_phase(volume, phase)
                    ledger.checkpoint('restore_data_committed')
                with mock.patch.object(ledger, '_save', side_effect=SystemExit):
                    with self.assertRaises(SystemExit):
                        if method == 'seal':
                            ledger.seal_backup(seal_for(ledger))
                        elif method == 'restore':
                            ledger.decide_restore()
                        else:
                            ledger.arm_previous_start()
                reopened = RecoveryLedger.open(ledger.directory)
                if method == 'seal':
                    self.assertIsNotNone(reopened.backup_seal)
                elif method == 'restore':
                    self.assertEqual(reopened.direction, 'restore_decided')
                else:
                    with self.assertRaises(RecoveryError):
                        reopened.assert_data_mutable()

    def test_durable_operation_result_is_reconciled_after_checkpoint_write_crash(self):
        operation = self.ledger.begin('stop', identity={'id': 'a' * 64})
        with mock.patch.object(self.ledger, '_save', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.ledger.complete(operation, {'stopped': True})
        reopened = self.reopen()
        self.assertIsNone(reopened.snapshot['pending'])
        self.assertEqual(reopened.snapshot['sequence'], operation)

    def test_source_stop_completion_cannot_use_mismatched_or_unproven_results(self):
        identity = {'Id': 'a' * 64, 'Image': 'sha256:' + 'b' * 64}
        self.ledger.checkpoint('quiescing', containers={'app': identity}, stopped_container_ids=[])
        sequence = self.ledger.begin('stop', identity=identity)
        for result in ({'id': 'c' * 64, 'stopped': True}, {'id': identity['Id'], 'stopped': False},
                       {'id': identity['Id'], 'stopped': 1}, {'stopped': True}):
            with self.subTest(result=result), self.assertRaises(RecoveryError):
                self.ledger.complete(sequence, result)
            fresh = self.reopen()
            self.assertIsNotNone(fresh.snapshot['pending'])
            self.assertEqual(fresh.snapshot['stopped_container_ids'], [])
            self.assertFalse((fresh.directory / 'op-0001-result.json').exists())
        self.ledger.complete(sequence, {'id': identity['Id'], 'stopped': True})
        self.assertEqual(self.reopen().snapshot['stopped_container_ids'], [identity['Id']])

    def test_foreign_stop_completion_does_not_grant_source_restart_authority(self):
        identity = {'Id': 'a' * 64, 'Image': 'sha256:' + 'b' * 64}
        self.ledger.checkpoint('quiescing', containers={'app': identity}, stopped_container_ids=[])
        foreign = dict(identity, Image='sha256:' + 'c' * 64)
        sequence = self.ledger.begin('stop', identity=foreign)
        self.ledger.complete(sequence, {'id': identity['Id'], 'stopped': True})
        self.assertEqual(self.reopen().snapshot['stopped_container_ids'], [])

    def test_real_sigkill_before_and_after_every_authoritative_intent_result_and_barrier(self):
        root = str(ROOT)
        for scenario in ('begin', 'complete', 'seal', 'restore', 'success', 'barrier'):
            for method in ('_evidence', '_save'):
                for edge in ('before', 'after'):
                    key = '-'.join((scenario, method, edge))
                    attempt = hashlib.md5(key.encode()).hexdigest()
                    code = '''import os,sys,signal
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from deploy.tests.test_update_recovery_ledger import expected,seal_for
from deploy.update.recovery import RecoveryLedger,VOLUME_PHASES
l=RecoveryLedger.reserve(Path(sys.argv[2]),expected(sys.argv[3]),operation_token='t',run_id='r',binding_sha256='8'*64)
scenario,method,edge=sys.argv[4:]
if scenario in ('restore','success','barrier','begin','complete'): l.seal_backup(seal_for(l))
if scenario in ('barrier','begin','complete'): l.decide_restore()
if scenario=='barrier':
 for volume in ('app-data','postgres-data','clickhouse-data'):
  for p in VOLUME_PHASES: l.volume_phase(volume,p)
 l.checkpoint('restore_data_committed')
if scenario=='complete': op=l.begin('clear',identity={'id':'a'*64})
real=getattr(l,method)
def die(*args,**kwargs):
 if edge=='before': os.kill(os.getpid(),signal.SIGKILL)
 result=real(*args,**kwargs)
 os.kill(os.getpid(),signal.SIGKILL)
setattr(l,method,die)
if scenario=='begin': l.begin('clear',identity={'id':'a'*64})
elif scenario=='complete': l.complete(op,{'empty':True})
elif scenario=='seal': l.seal_backup(seal_for(l))
elif scenario=='restore': l.decide_restore()
elif scenario=='success': l.decide_success({'observation':'9'*64})
else: l.arm_previous_start()
'''
                    with self.subTest(boundary=key):
                        child = subprocess.run([sys.executable, '-I', '-B', '-c', code, root, str(self.destination), attempt,
                                                scenario, method, edge], capture_output=True)
                        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
                        ledger = RecoveryLedger.open(self.destination / attempt)
                        if scenario == 'barrier' and (method == '_save' or edge == 'after'):
                            with self.assertRaises(RecoveryError):
                                ledger.assert_data_mutable()
                        if scenario == 'success' and (method == '_save' or edge == 'after'):
                            self.assertEqual(ledger.direction, 'success_decided')
                        if scenario == 'restore' and (method == '_save' or edge == 'after'):
                            self.assertEqual(ledger.direction, 'restore_decided')

    def test_block_keeps_verified_progress_and_sanitizes_reason(self):
        self.ledger.checkpoint('quiesced')
        self.ledger.block('restore_child_ambiguous')
        self.assertEqual(self.reopen().phase, 'quiesced')
        self.assertEqual(self.reopen().snapshot['last_verified_phase'], 'quiesced')
        with self.assertRaises(RecoveryError):
            self.ledger.block('/private/data secret')

    def test_real_sigkill_at_each_checkpoint_boundary_preserves_direction_and_barrier(self):
        # Enumerate the amended lifecycle + EVERY per-volume boundary. Each child
        # actually dies by SIGKILL after a durable checkpoint; a fresh process
        # reads it. These are filesystem/control-plane tests, not Docker evidence.
        root = str(Path(__file__).resolve().parents[2])
        boundaries = list(PHASES)
        boundaries += ['volume:' + volume + ':' + phase for volume in ('app-data', 'postgres-data', 'clickhouse-data') for phase in VOLUME_PHASES]
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                attempt = hashlib.md5(boundary.encode()).hexdigest()
                code = '''import sys,os,signal
sys.path.insert(0,sys.argv[1])
from deploy.tests.test_update_recovery_ledger import expected,seal_for
from deploy.update.recovery import RecoveryLedger,VOLUME_PHASES,PHASES
from pathlib import Path
l=RecoveryLedger.reserve(Path(sys.argv[2]),expected(sys.argv[3]),operation_token='t',run_id='r',binding_sha256='8'*64)
b=sys.argv[4]
if b in PHASES[:PHASES.index('backup_sealed')]+('abort_quiescence','failed_available'): l.checkpoint(b)
else:
 l.seal_backup(seal_for(l))
 if b in PHASES[PHASES.index('env_commit_pending'):PHASES.index('success_decided')]: l.checkpoint(b)
 elif b in ('success_decided','success_metadata_committed','succeeded'):
  l.decide_success({'observation':'9'*64})
  if b!='success_decided': l.checkpoint(b)
 elif b!='backup_sealed':
  l.decide_restore()
  if b.startswith('volume:'):
   _,volume,phase=b.split(':')
   for p in VOLUME_PHASES[:VOLUME_PHASES.index(phase)+1]: l.volume_phase(volume,p)
  elif b in ('previous_start_armed','previous_starting','previous_verifying','restore_verified','restore_metadata_committed','restored'):
   for volume in ('app-data','postgres-data','clickhouse-data'):
    for p in VOLUME_PHASES: l.volume_phase(volume,p)
   l.checkpoint('restore_data_committed'); l.arm_previous_start()
   if b!='previous_start_armed': l.checkpoint(b)
  elif b!='restore_decided': l.checkpoint(b)
os.kill(os.getpid(),signal.SIGKILL)
'''
                child = subprocess.run([sys.executable, '-I', '-B', '-c', code, root, str(self.destination), attempt, boundary], capture_output=True)
                self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
                ledger = RecoveryLedger.open(self.destination / attempt)
                if boundary.startswith('volume:'):
                    _, volume, phase = boundary.split(':')
                    self.assertEqual(ledger.snapshot['volumes'][volume], phase)
                else:
                    self.assertEqual(ledger.phase, boundary)
                if ledger.snapshot['previous_start_armed']:
                    with self.assertRaises(RecoveryError):
                        ledger.assert_data_mutable()
                elif ledger.backup_seal is not None:
                    expected_direction = 'success_decided' if ledger.direction == 'success_decided' else 'restore_decided'
                    self.assertEqual(ledger.resume_direction(), expected_direction)
