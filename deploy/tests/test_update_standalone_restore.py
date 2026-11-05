"""Synthetic-daemon safety tests; real archive/proof/ledger bytes, no product claim."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.tests import test_update_standalone_backup as fixtures
from deploy.tests.test_update_e2e_backup import observation
from deploy.update import standalone_backup as backup
from deploy.update import standalone_restore as restore
from deploy.update.archive_inventory import verify_archive
from deploy.update.recovery import RecoveryLedger, RecoveryError, VOLUME_PHASES

REAL_HELPERS = restore._StorageHelpers


class LocalHelpers:
    """Explicit model of helper postconditions. Never qualification evidence."""
    calls = []
    wrong_tree = False

    def __init__(self, installation, binding, ledger, *, policy, helper_image_id=None):
        self.installation, self.b, self.ledger, self.policy = installation, binding, ledger, policy
        self.deadline = time.monotonic() + 60

    def command(self, *args):
        return backup._command(self.installation, self.b['context'], *args)

    def retire_helpers(self):
        if self.ledger.snapshot['pending'] is not None:
            raise restore.RestoreError('restore_child_ambiguous')

    def assert_no_running_consumers(self, key):
        source = {c['Id']: c for c in self.ledger.snapshot.get('containers', {}).values()}
        source.update({c['Id']: c for c in self.ledger.snapshot.get('target_containers', {}).values()})
        for c in self.installation.runner.containers.values():
            if any(m.get('Name') == self.b['volumes'][key]['Name'] for m in c['Mounts']):
                if c['Id'] not in source or backup._identity(c) != source[c['Id']] or c['State']['Running']:
                    raise restore.RestoreError('restore_child_ambiguous')

    def inspect(self, key):
        return restore.VolumeStorage(self.b['volume_root_ids'][key], 'fixture-device', 10 ** 12, 10 ** 7)

    def execute(self, key, purpose, *, writable=False, tar=False, archive_fd=None, output_fd=None, max_output=None, archive_identity=None):
        self.calls.append((key, purpose))
        self.assert_no_running_consumers(key)
        op = self.ledger.begin(purpose if purpose in ('clear', 'extract') else 'helper_start', identity={'key': key, 'purpose': purpose})
        if purpose == 'archive':
            payload = self.installation.runner.tar
            backup._write_all(output_fd, payload)
            result = SimpleNamespace(returncode=0, size=len(payload), sha256=backup._hash(payload), stderr=b'')
        else:
            result = SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        self.ledger.complete(op, {'done': True})
        return result

    def inventory(self, key, fd):
        raw = backup._read(fd, key + '.members.jsonl')
        return {'sha256': 'f' * 64 if self.wrong_tree else backup._hash(raw), 'size': len(raw)}


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.expected = f.expected
        self.ledger = RecoveryLedger.reserve(f.destination, f.expected, operation_token='token', run_id='run', binding_sha256='2' * 64)
        self.source = replace(observation(f.binding, {s: c for s, c in zip(f.daemon.images, f.daemon.containers.values())}, fixtures.NOW),
                              release_version=f.expected.source_version)
        LocalHelpers.calls, LocalHelpers.wrong_tree = [], False
        patch = mock.patch.object(restore, '_StorageHelpers', LocalHelpers)
        patch.start()
        self.addCleanup(patch.stop)
        self.artifact = f.adapter.run_quiesced(f.expected, self.ledger, self.source, proof_directory_fd=f.proof_fd)
        self.ledger.decide_restore()
        self.adapter = restore.StandaloneRestore(f.install, f.binding, f.destination, policy=f.adapter.policy, clock=lambda: fixtures.NOW)
        self.addCleanup(self.adapter.close)
        self.adapter.quiesce_attempt(self.expected, self.ledger)
        self.adapter.verify_inputs(self.expected, self.ledger)
        LocalHelpers.calls = []

    def test_legacy_run_cannot_restart_a_for_the_new_policy(self):
        f = self.fixture
        f.daemon.policy = replace(f.daemon.policy, qualification_policy='standalone-cold-restore-v1', tick_timeout_seconds=172800)
        f.daemon.calls.clear()
        with self.assertRaisesRegex(backup.BackupError, 'automatic_recovery_pending'):
            f.adapter.run(f.expected, proof_directory_fd=f.proof_fd)
        self.assertEqual(f.daemon.calls, [])

    def test_unclean_stop_no_implicit_restart_and_only_proven_preseal_abort(self):
        f = fixtures.BackupTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        ledger = RecoveryLedger.reserve(f.destination, f.expected, operation_token='token', run_id='run', binding_sha256='2' * 64)
        source = replace(observation(f.binding, {s: c for s, c in zip(f.daemon.images, f.daemon.containers.values())}, fixtures.NOW),
                         release_version=f.expected.source_version)
        f.daemon.after_stop = lambda c: c['State'].update(ExitCode=137)
        with self.assertRaisesRegex(backup.BackupError, 'original_stop_unclean'):
            f.adapter.run_quiesced(f.expected, ledger, source, proof_directory_fd=f.proof_fd)
        self.assertIsNone(ledger.backup_seal)
        self.assertFalse(any(c[0] == 'start' for c in f.daemon.calls))
        self.assertFalse((f.proof_dir / 'proof.json').exists())
        self.assertEqual(f.adapter.abort_quiescence(f.expected, ledger).state, 'failed_available')
        self.assertTrue(all(c['State']['Running'] for c in f.daemon.containers.values()))

    def test_unexpected_source_start_invalidates_hold_without_authorizing_a_clear(self):
        c = self.fixture.daemon.containers['1' * 64]
        c['State'].update(Running=True, StartedAt='operator-start')
        with self.assertRaises(backup.BackupError) as caught:
            self.fixture.adapter.verify_hold(self.ledger, self.source)
        self.assertTrue(caught.exception.recovery_required)
        self.assertEqual(self.ledger.snapshot['volumes'], {})

    def test_same_service_source_and_target_are_both_known_by_full_id(self):
        f = self.fixture
        target = copy.deepcopy(f.daemon.containers['1' * 64])
        target['Id'], target['Name'] = 'a' * 64, '/candidate-app'
        f.daemon.containers[target['Id']] = target
        self.ledger.checkpoint(target_containers={'app': backup._identity(target)})
        helper = object.__new__(REAL_HELPERS)
        helper.installation, helper.context, helper.b, helper.ledger = f.install, 'test-backup', json.loads(f.binding.raw), self.ledger
        helper.assert_no_running_consumers('app-data')
        foreign = copy.deepcopy(target)
        foreign['Id'] = 'b' * 64
        f.daemon.containers[foreign['Id']] = foreign
        with self.assertRaises(restore.RestoreError):
            helper.assert_no_running_consumers('app-data')

    def test_identical_bytes_in_a_replaced_env_inode_are_not_silently_adopted(self):
        path = self.fixture.root / '.env'
        other = path.with_name('operator-replacement')
        other.write_bytes(path.read_bytes())
        other.chmod(0o600)
        os.replace(other, path)
        with self.assertRaises(restore.RestoreError):
            self.adapter.restore_volumes()
        self.assertFalse(any(p == 'clear' for _, p in LocalHelpers.calls))

    def registered_helper(self):
        f = self.fixture
        row = copy.deepcopy(f.daemon.containers['1' * 64])
        row['Id'], row['Name'] = 'e' * 64, '/completed-helper'
        f.daemon.containers[row['Id']] = row
        self.ledger.checkpoint(helpers={row['Id']: {'identity': backup._identity(row), 'key': 'app-data', 'purpose': 'inventory'}})
        return row, REAL_HELPERS(f.install, json.loads(f.binding.raw), self.ledger, policy=f.adapter.policy)

    def test_completed_helpers_detach_but_immutable_identity_evidence_is_retained(self):
        row, helper = self.registered_helper()
        helper.retire_helpers()
        self.assertNotIn(row['Id'], self.fixture.daemon.containers)
        self.assertIn(row['Id'], self.ledger.snapshot['helpers'])
        self.assertIsNone(self.ledger.snapshot['pending'])
        self.assertTrue(any(c == ['rm', row['Id']] for c in self.fixture.daemon.calls))
        self.assertFalse(any('-v' in c or '--force' in c for c in self.fixture.daemon.calls))

    def test_pending_metadata_only_removal_reconciles_absence_without_any_store_mutation(self):
        row, helper = self.registered_helper()
        with mock.patch.object(self.ledger, 'complete', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                helper.retire_helpers()
        self.assertNotIn(row['Id'], self.fixture.daemon.containers)
        self.assertEqual(self.ledger.snapshot['pending']['kind'], 'helper_remove')
        reopened = RecoveryLedger.open(self.ledger.directory)
        replacement = REAL_HELPERS(self.fixture.install, json.loads(self.fixture.binding.raw), reopened, policy=self.fixture.adapter.policy)
        self.fixture.daemon.calls.clear()
        replacement.retire_helpers()
        self.assertIsNone(reopened.snapshot['pending'])
        self.assertFalse(any(c[0] in ('rm', 'start', 'stop', 'run') for c in self.fixture.daemon.calls))

    def test_running_helper_is_never_removed_or_used_as_a_reason_to_clear_data(self):
        row, helper = self.registered_helper()
        row['State'].update(Running=True, Status='running')
        self.fixture.daemon.calls.clear()
        with self.assertRaises(restore.RestoreError):
            helper.retire_helpers()
        self.assertIn(row['Id'], self.fixture.daemon.containers)
        self.assertFalse(any(c[0] == 'rm' for c in self.fixture.daemon.calls))
        self.assertEqual(self.ledger.snapshot['volumes'], {})

    def test_v2_cold_hold_never_restarts_a_and_has_no_inventory_proof_cycle(self):
        f = self.fixture
        self.assertFalse(any(c['State']['Running'] for c in f.daemon.containers.values()))
        self.assertFalse(any(c[0] == 'start' for c in f.daemon.calls))
        inventory = self.adapter.inventory
        self.assertNotIn('proof_sha256', inventory)
        self.assertEqual(self.artifact.seal.inventory_sha256, backup._hash((self.ledger.directory / 'inventory.json').read_bytes()))
        hold = f.adapter.verify_hold(self.ledger, self.source)
        self.assertEqual(hold.snapshot_at, self.artifact.seal.snapshot_at)
        self.assertEqual(hold.source_observation_sha256, self.source.sha256)

    def test_restore_all_stores_and_config_before_start_and_verified_resume_never_reextracts(self):
        self.adapter.restore_volumes()
        self.assertEqual(sum(p == 'clear' for _, p in LocalHelpers.calls), 3)
        self.assertEqual(sum(p == 'extract' for _, p in LocalHelpers.calls), 3)
        LocalHelpers.calls = []
        self.adapter.restore_volumes()
        self.assertFalse(any(p in ('clear', 'extract') for _, p in LocalHelpers.calls))
        self.adapter.restore_configuration()
        self.adapter.verify_restored()
        self.ledger.arm_previous_start()
        with self.assertRaises(RecoveryError):
            self.adapter.restore_volumes()

    def test_wrong_volume_identity_refuses_without_clear(self):
        self.fixture.daemon.volumes['test-backup_app-data']['CreatedAt'] = 'replacement'
        with self.assertRaises(restore.RestoreError):
            self.adapter.restore_volumes()
        self.assertFalse(any(p == 'clear' for _, p in LocalHelpers.calls))

    def test_wrong_root_identity_and_bytes_or_inode_capacity_refuse_without_clear(self):
        original = self.adapter.helpers.inspect('app-data')
        for measurement in (replace(original, root_identity='1:999'), replace(original, free_bytes=1),
                            replace(original, free_inodes=1), None):
            self.adapter.storage_inspector = lambda key, value=measurement: value
            with self.subTest(measurement=measurement), self.assertRaises(restore.RestoreError):
                self.adapter.restore_volumes()
            self.assertFalse(any(p == 'clear' for _, p in LocalHelpers.calls))

    def test_no_clear_while_known_writer_runs_and_foreign_stopped_attachment_also_refuses(self):
        container = self.fixture.daemon.containers['1' * 64]
        container['State']['Running'] = True
        with self.assertRaises(restore.RestoreError):
            self.adapter.restore_volumes()
        self.assertFalse(any(p == 'clear' for _, p in LocalHelpers.calls))
        self.adapter.quiesce_attempt(self.expected, self.ledger)  # exact identity can stop it
        self.assertFalse(container['State']['Running'])
        other = copy.deepcopy(container)
        other['Id'] = 'f' * 64
        self.fixture.daemon.containers[other['Id']] = other
        with self.assertRaises(restore.RestoreError):
            self.adapter.restore_volumes()
        self.assertFalse(any(p == 'clear' for _, p in LocalHelpers.calls))

    def test_concurrent_nonenv_config_edit_is_never_overwritten(self):
        path = self.fixture.root / backup.CONFIG_FILES[2]
        path.write_bytes(b'operator edit')
        with self.assertRaises(restore.RestoreError):
            self.adapter.restore_volumes()
        self.assertFalse(any(p == 'clear' for _, p in LocalHelpers.calls))
        self.assertEqual(path.read_bytes(), b'operator edit')

    def test_lexical_env_inverse_preserves_native_retained_links(self):
        from deploy.update import envfile
        env = self.fixture.root / '.env'
        record = envfile.recovery_record(env, {'FUTURE_AGI_VERSION': 'v1.54.0'})
        envfile.write_changes(env, {'FUTURE_AGI_VERSION': 'v1.54.0'}, recovery=record)
        self.adapter.verify_inputs(self.expected, self.ledger, env_recovery=record)
        self.adapter.restore_volumes()
        self.adapter.restore_configuration(env_recovery=record)
        self.adapter.verify_restored()
        self.assertEqual(backup._hash(env.read_bytes()), self.adapter.inventory['config_files'][0]['sha256'])
        self.assertEqual(backup._files(self.fixture.root)['.env'], env.read_bytes())

    def test_complete_tree_mismatch_blocks_before_any_source_start(self):
        LocalHelpers.wrong_tree = True
        with self.assertRaisesRegex(restore.RestoreError, 'restore_verification_failed'):
            self.adapter.restore_volumes()
        self.assertEqual(self.ledger.snapshot['volumes']['app-data'], 'extracted')
        self.assertFalse(self.ledger.snapshot['previous_start_armed'])

    def test_pending_remote_child_is_never_cleared_by_a_fresh_supervisor(self):
        self.ledger.begin('extract', identity={'id': 'a' * 64})
        replacement = restore.StandaloneRestore(self.fixture.install, self.fixture.binding, self.fixture.destination,
            policy=self.fixture.adapter.policy, clock=lambda: fixtures.NOW)
        self.addCleanup(replacement.close)
        with self.assertRaisesRegex(restore.RestoreError, 'restore_child_ambiguous'):
            replacement.verify_inputs(self.expected, RecoveryLedger.open(self.ledger.directory))
        self.assertFalse(any(p == 'clear' for _, p in LocalHelpers.calls))

    def test_old_sealed_snapshot_recovers_without_timestamp_refresh(self):
        self.adapter.clock = lambda: fixtures.NOW + timedelta(days=30)
        self.adapter.verify_inputs(self.expected, self.ledger)
        self.assertEqual(self.ledger.backup_seal.proof_sha256, self.artifact.proof.sha256)
        self.assertEqual(self.ledger.backup_seal.snapshot_at, self.artifact.seal.snapshot_at)

    def test_every_damaged_archive_or_member_inventory_refuses_before_clear(self):
        for leaf in ('app-data.tar', 'postgres-data.tar', 'clickhouse-data.tar', 'app-data.members.jsonl', 'inventory.json'):
            path = self.ledger.directory / leaf
            original = path.read_bytes()
            path.write_bytes(original[:100])
            try:
                with self.subTest(leaf=leaf), self.assertRaises((RuntimeError, ValueError, tarfile.TarError)):
                    self.adapter.verify_inputs(self.expected, self.ledger)
                self.assertEqual(self.ledger.snapshot['volumes'], {})
            finally:
                path.write_bytes(original)


class PresealAvailabilityRecoveryTests(unittest.TestCase):
    def fixture(self):
        f = fixtures.BackupTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        ledger = RecoveryLedger.reserve(f.destination, f.expected, operation_token='token', run_id='run', binding_sha256='2' * 64)
        source = replace(observation(f.binding, {s: c for s, c in zip(f.daemon.images, f.daemon.containers.values())}, fixtures.NOW),
                         release_version=f.expected.source_version)
        return f, ledger, source

    def test_acknowledged_source_stop_and_bookkeeping_survive_each_completion_crash_edge(self):
        for edge in ('after_result_fsync', 'after_completion'):
            for stop in (1, 2, 3):
                with self.subTest(edge=edge, stop=stop):
                    f, ledger, source = self.fixture()
                    real_complete, real_save = ledger.complete, ledger._save

                    def complete(sequence, result):
                        real_complete(sequence, result)
                        if sequence == stop:
                            raise SystemExit('crash after completed stop, before adapter continues')

                    def save(document):
                        pending = ledger.snapshot['pending']
                        if pending is not None and pending['sequence'] == stop and document['pending'] is None:
                            raise SystemExit('crash after immutable result, before completion checkpoint')
                        real_save(document)

                    if edge == 'after_result_fsync':
                        ledger._save = save
                    else:
                        ledger.complete = complete
                    with self.assertRaises(SystemExit):
                        f.adapter.run_quiesced(f.expected, ledger, source, proof_directory_fd=f.proof_fd)
                    fresh = RecoveryLedger.open(ledger.directory, expected=f.expected)
                    self.assertIsNone(fresh.snapshot['pending'])
                    self.assertEqual(fresh.snapshot['stopped_container_ids'], [str(i) * 64 for i in range(1, stop + 1)])
                    self.assertIsNone(fresh.backup_seal)
                    self.assertFalse((f.proof_dir / 'proof.json').exists())
                    f.daemon.calls.clear()
                    self.assertEqual(f.adapter.abort_quiescence(f.expected, fresh).state, 'failed_available')
                    self.assertTrue(all(c['State']['Running'] for c in f.daemon.containers.values()))
                    self.assertEqual(sum(c[0] == 'start' for c in f.daemon.calls), stop)
                    f.daemon.calls.clear()
                    fresh = RecoveryLedger.open(ledger.directory, expected=f.expected)
                    self.assertEqual(f.adapter.abort_quiescence(f.expected, fresh).state, 'failed_available')
                    self.assertFalse(any(c[0] in ('start', 'stop', 'run') for c in f.daemon.calls))

    def test_unacknowledged_source_stop_still_blocks_availability_restart(self):
        for stop in (1, 2, 3):
            with self.subTest(stop=stop):
                f, ledger, source = self.fixture()
                real_complete = ledger.complete

                def complete(sequence, result):
                    if sequence == stop:
                        raise SystemExit('no durable independently inspected completion')
                    real_complete(sequence, result)

                ledger.complete = complete
                with self.assertRaises(SystemExit):
                    f.adapter.run_quiesced(f.expected, ledger, source, proof_directory_fd=f.proof_fd)
                fresh = RecoveryLedger.open(ledger.directory, expected=f.expected)
                self.assertIsNotNone(fresh.snapshot['pending'])
                f.daemon.calls.clear()
                with self.assertRaises(RuntimeError):
                    f.adapter.abort_quiescence(f.expected, fresh)
                self.assertFalse(any(c[0] in ('start', 'stop', 'run') for c in f.daemon.calls))


class ArchiveInventoryV2Tests(unittest.TestCase):
    def verify(self, members, *, root='/data', complete=False):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for member, payload in members:
                archive.addfile(member, io.BytesIO(payload) if payload is not None else None)
        with tempfile.NamedTemporaryFile() as source, tempfile.NamedTemporaryFile() as target:
            source.write(stream.getvalue())
            source.flush()
            result = verify_archive(source.fileno(), target.fileno(), backup.BackupPolicy(reserve_bytes=1),
                deadline=time.monotonic() + 5, volume_root=root, complete_tree=complete)
            target.seek(0)
            return result, [json.loads(row) for row in target.read().splitlines()]

    def member(self, name, kind=tarfile.REGTYPE, link='', pax=None):
        m = tarfile.TarInfo(name)
        m.type, m.linkname = kind, link
        m.mode, m.uid, m.gid, m.mtime = 0o600, 999, 999, 1700000000
        m.pax_headers = pax or {}
        m.size = 1 if kind == tarfile.REGTYPE else 0
        return m, b'x' if kind == tarfile.REGTYPE else None

    def test_normalizes_nanosecond_mtime_acl_xattr_and_hardlink_equivalence(self):
        import base64
        pax = {'mtime': '1700000000.123456789', 'SCHILY.xattr.user.fixture': 'value',
               'SCHILY.acl.access': 'user::rw-,group::r--,other::---'}
        rows = [self.member('./file', pax=pax), self.member('link', tarfile.LNKTYPE, './file', pax=pax)]
        _, normalized = self.verify(rows)
        self.assertEqual(normalized[0]['mtime_ns'], 1700000000123456789)
        self.assertEqual(normalized[0]['xattrs']['user.fixture'], base64.b64encode(b'value').decode())
        self.assertEqual(normalized[0]['hardlinks'], ['file', 'link'])
        self.assertEqual(normalized[0]['sha256'], normalized[1]['sha256'])
        self.assertEqual(normalized[0]['acls']['access'], [['group', None, 4], ['other', None, 0], ['user', None, 6]])

    def test_unicode_paths_links_hardlinks_and_xattr_names_share_exact_tree_encoding(self):
        from deploy.update.probes.restore_fs import canonical
        cases = (
            [self.member('café.txt')],
            [self.member('file'), self.member('link', tarfile.SYMTYPE, '資料')],
            [self.member('file', pax={'SCHILY.xattr.user.café': 'value'})],
            [self.member('資料'), self.member('hardlink', tarfile.LNKTYPE, '資料')],
        )
        for members in cases:
            with self.subTest(names=[member.name for member, _ in members]):
                result, rows = self.verify([self.member('.', tarfile.DIRTYPE), *members], complete=True)
                helper_bytes = b''.join(canonical(row) + b'\n' for row in rows)
                self.assertEqual(result['members_sha256'], hashlib.sha256(helper_bytes).hexdigest())
                self.assertEqual(result['members_bytes'], len(helper_bytes))

    def test_legacy_inventory_unicode_encoding_remains_unchanged(self):
        result, rows = self.verify([self.member('café.txt')])
        legacy_bytes = b''.join(backup.canonical_json(row) + b'\n' for row in rows)
        self.assertEqual(result['members_sha256'], hashlib.sha256(legacy_bytes).hexdigest())
        self.assertEqual(result['members_bytes'], len(legacy_bytes))

    def test_gnu_newline_text_and_binary_posix_acl_records_must_agree(self):
        import struct
        binary = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in
            ((1, 6, 4294967295), (2, 4, 1234), (4, 4, 4294967295), (16, 4, 4294967295), (32, 0, 4294967295)))
        text = 'user::rw-\nuser:1234:r--\ngroup::r--\nmask::r--\nother::---'
        pax = {'SCHILY.acl.access': text, 'SCHILY.xattr.system.posix_acl_access': binary.decode('utf-8', 'surrogateescape')}
        _, rows = self.verify([self.member('file', pax=pax)])
        self.assertIn(['user', 1234, 4], rows[0]['acls']['access'])
        pax['SCHILY.acl.access'] = text.replace('user:1234:r--', 'user:1234:rw-')
        with self.assertRaises(backup.BackupError):
            self.verify([self.member('file', pax=pax)])

    def test_duplicate_canonical_names_link_ancestors_and_outside_hardlinks_refuse(self):
        cases = ([self.member('file'), self.member('./file')],
                 [self.member('dir/child'), self.member('dir', tarfile.SYMTYPE, 'file')],
                 [self.member('link', tarfile.LNKTYPE, '../file')],
                 [self.member('link', tarfile.LNKTYPE, 'missing')],
                 [self.member('link', tarfile.LNKTYPE, 'file'), self.member('file')],
                 [self.member('dir/../file')])
        for rows in cases:
            with self.subTest(names=[m.name for m, _ in rows]), self.assertRaises(backup.BackupError):
                self.verify(rows)

    def test_unknown_pax_and_security_metadata_are_not_silently_lost(self):
        for pax in ({'SCHILY.xattr.security.capability': 'danger'}, {'unknown.record': 'x'},
                    {'mtime': '1.1234567891'}, {'SCHILY.acl.access': 'user:operator:rwx'}):
            with self.subTest(pax=pax), self.assertRaises(backup.BackupError):
                self.verify([self.member('file', pax=pax)])

    def test_complete_tree_requires_root_and_parent_directories(self):
        with self.assertRaises(backup.BackupError):
            self.verify([self.member('file')], complete=True)
        with self.assertRaises(backup.BackupError):
            self.verify([self.member('.', tarfile.DIRTYPE), self.member('missing/file')], complete=True)
        result, rows = self.verify([self.member('.', tarfile.DIRTYPE), self.member('file')], complete=True)
        self.assertEqual(result['inventory_version'], 2)
        self.assertEqual(rows[0]['name'], '.')

    def test_absolute_symlink_qualification_is_clickhouse_only(self):
        _, rows = self.verify([self.member('link', tarfile.SYMTYPE, '/var/lib/clickhouse/store/a')], root='/var/lib/clickhouse')
        self.assertEqual(rows[0]['link'], '/var/lib/clickhouse/store/a')
        for root in ('/data', '/var/lib/postgresql/data'):
            with self.assertRaises(backup.BackupError):
                self.verify([self.member('link', tarfile.SYMTYPE, root + '/other')], root=root)


class RestoreScanHardlinkTests(unittest.TestCase):
    def scan(self, root, **limits):
        from deploy.update.probes import restore_fs
        reads = []
        real_read = os.read

        def counted(fd, size):
            chunk = real_read(fd, size)
            reads.append(len(chunk))
            return chunk
        with mock.patch.object(restore_fs, 'metadata', return_value=({}, {})), \
                mock.patch.object(restore_fs.os, 'read', side_effect=counted):
            return restore_fs.scan(root, **limits), sum(reads)

    def test_hardlink_group_is_read_once_and_capped_on_logical_bytes(self):
        payload = os.urandom(4096)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'links'
            root.mkdir()
            (root / 'f00').write_bytes(payload)
            names = ['f%02d' % index for index in range(64)]
            for name in names[1:]:
                os.link(root / 'f00', root / name)
            rows, read = self.scan(root, max_bytes=len(payload))
        self.assertEqual(read, len(payload))
        files = [row for row in rows if row['type'] == 'file']
        self.assertEqual([row['name'] for row in files], names)
        self.assertEqual({row['sha256'] for row in files}, {hashlib.sha256(payload).hexdigest()})
        self.assertTrue(all(row['hardlinks'] == names for row in files))

    def test_distinct_inodes_still_count_toward_the_read_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'copies'
            root.mkdir()
            for name in ('a', 'b'):
                (root / name).write_bytes(b'x' * 4096)
            from deploy.update.probes.restore_fs import FilesystemError
            with self.assertRaises(FilesystemError):
                self.scan(root, max_bytes=4096)
            rows, read = self.scan(root, max_bytes=8192)
        self.assertEqual(read, 8192)
        self.assertEqual([row['hardlinks'] for row in rows if row['type'] == 'file'], [[], []])
