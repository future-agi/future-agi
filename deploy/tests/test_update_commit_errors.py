"""Commit error/reconciliation behaviours on real APFS/ext4 fixture files.

Faults replace individual syscall results, never the protocol or filesystem
qualification. Linux-only mount tests still call native fstatfs; they do not
qualify another filesystem for integration tests.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy.update import envcommit  # noqa: E402


class CommitErrorPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='commit-errors-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.target = self.root / '.env'
        self.before = b'V=v1\nPRIVATE=synthetic-retention-secret\n'
        self.after = self.before.replace(b'V=v1', b'V=v2')
        self.target.write_bytes(self.before)
        self.target.chmod(0o640)
        self.op_id = envcommit.reference()['forward']
        self.op = envcommit.directory(self.target, self.op_id)

    def commit(self, path=None):
        envcommit.commit(path or self.target, self.target, self.before, self.after, 0o640, self.op_id)

    def inspect(self, **kwargs):
        return envcommit.inspect(self.target, self.target, self.op_id, **kwargs)

    def manifest(self):
        return json.loads((self.op / 'manifest.json').read_text())

    def arm(self):
        real = envcommit.exchange

        def stop(src_fd, src, dst_fd, dst):
            if src == 'slot':
                raise SystemExit(137)
            return real(src_fd, src, dst_fd, dst)

        with mock.patch.object(envcommit, 'exchange', side_effect=stop):
            with self.assertRaises(SystemExit):
                self.commit()
        self.assertEqual(self.manifest()['phase'], 'armed')
        self.assertTrue(os.path.samefile(self.op / 'candidate', self.op / 'slot'))
        self.assertEqual(self.target.read_bytes(), self.before)

    def prepare(self):
        real = envcommit._json

        def stop(fd, name, body):
            real(fd, name, body)
            if body.get('phase') == 'preparing':
                raise SystemExit(137)

        with mock.patch.object(envcommit, '_json', side_effect=stop):
            with self.assertRaises(SystemExit):
                self.commit()
        self.assertEqual(self.manifest()['phase'], 'preparing')
        self.assertFalse((self.op / 'candidate').exists())

    def preserved(self, error):
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertNotIn('synthetic-retention-secret', str(error))

    def test_invalid_operation_ids_never_create_retention(self):
        for op_id in (None, 42, '', '../escape', 'A' * 32, '0' * 31):
            with self.subTest(op_id=op_id), self.assertRaisesRegex(envcommit.CommitError, 'operation id'):
                envcommit.commit(self.target, self.target, self.before, self.after, 0o640, op_id)
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse((self.root / '.futureagi-updates').exists())

    def test_closed_descriptor_fstatfs_reports_native_error(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        os.close(fd)
        with self.assertRaises(OSError) as caught:
            envcommit._filesystem(fd)
        self.assertEqual(caught.exception.errno, errno.EBADF)
        self.preserved(caught.exception)

    def test_native_nonlocal_filesystem_is_refused(self):
        # These native virtual mounts cannot be used as commit targets.
        path = Path('/proc' if sys.platform == 'linux' else '/dev')
        with envcommit._dir(path) as fd:
            with self.assertRaisesRegex(envcommit.CommitError, 'no replacement fallback'):
                envcommit._filesystem(fd)
        self.assertFalse(self.op.exists())

    def test_native_exchange_errno_does_not_replace_entries(self):
        with self.assertRaises(OSError) as caught:
            envcommit.exchange(-1, 'slot', -1, '.env')
        self.assertEqual(caught.exception.errno, errno.EBADF)
        self.preserved(caught.exception)

    def test_missing_exchange_symbol_leaves_preparing_candidate_private(self):
        native = envcommit._libc()

        class WithoutExchange:
            def __getattr__(self, name):
                if name in ('renameat2', 'renameatx_np'):
                    raise AttributeError(name)
                return getattr(native, name)

        with mock.patch.object(envcommit, '_libc', return_value=WithoutExchange()):
            with self.assertRaisesRegex(envcommit.CommitError, 'unavailable') as caught:
                self.commit()
        self.preserved(caught.exception)
        self.assertEqual(self.manifest()['phase'], 'preparing')
        self.assertEqual((self.op / 'candidate').read_bytes(), self.after)
        self.assertEqual(stat.S_IMODE(self.op.stat().st_mode), 0o700)
        self.assertEqual(self.inspect(), 'untouched')
        with self.assertRaisesRegex(envcommit.CommitError, 'incomplete env preparation'):
            self.commit()

    def test_private_directory_mode_rejected_during_creation_and_inspection(self):
        state = self.root / '.futureagi-updates'
        state.mkdir(mode=0o750)
        with self.assertRaisesRegex(envcommit.CommitError, 'not private') as caught:
            self.commit()
        self.preserved(caught.exception)
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o750)
        state.chmod(0o700)
        self.arm()
        self.op.chmod(0o750)
        with self.assertRaisesRegex(envcommit.CommitError, 'mode 0700') as caught:
            self.inspect(durable=True)
        self.preserved(caught.exception)
        self.assertEqual(self.manifest()['phase'], 'armed')
        self.assertEqual(stat.S_IMODE(self.op.stat().st_mode), 0o750)

    def test_foreign_private_directory_owner_is_not_repaired(self):
        self.arm()
        with mock.patch.object(envcommit.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(envcommit.CommitError, 'owned by this user') as caught:
                self.inspect()
        self.preserved(caught.exception)
        self.assertEqual(self.manifest()['phase'], 'armed')

    def test_inspection_detects_real_write_through_old_descriptor(self):
        real = os.read
        edited = []
        with self.target.open('ab') as editor:
            def read(fd, count):
                data = real(fd, count)
                if data and not edited:
                    editor.write(b'EDITOR=must-survive\n')
                    editor.flush()
                    edited.append(True)
                return data

            with envcommit._dir(self.root) as fd, mock.patch.object(os, 'read', side_effect=read):
                with self.assertRaisesRegex(envcommit.CommitError, 'changed while being inspected') as caught:
                    envcommit._snapshot(fd, self.target.name)
        self.assertEqual(edited, [True])
        self.assertEqual(self.target.read_bytes(), self.before + b'EDITOR=must-survive\n')
        self.assertNotIn('synthetic-retention-secret', str(caught.exception))

    def test_manifest_permissions_are_rejected_not_chmodded(self):
        self.arm()
        manifest = self.op / 'manifest.json'
        manifest.chmod(0o644)
        before = manifest.read_bytes()
        with self.assertRaisesRegex(envcommit.CommitError, 'manifest metadata') as caught:
            self.inspect(durable=True)
        self.preserved(caught.exception)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o644)

    def test_nonobject_manifest_is_rejected_without_exposing_contents(self):
        self.arm()
        manifest = self.op / 'manifest.json'
        manifest.write_text('["synthetic-retention-secret"]')
        with self.assertRaisesRegex(envcommit.CommitError, 'invalid private env manifest') as caught:
            self.inspect(durable=True)
        self.preserved(caught.exception)
        self.assertEqual(manifest.read_text(), '["synthetic-retention-secret"]')

    def test_malformed_snapshot_fields_block_reconciliation_without_writes(self):
        self.arm()
        valid = self.manifest()
        path = self.op / 'manifest.json'
        corruptions = (('identity', None), ('identity', [1]), ('identity', [True, 1]),
                       ('identity', [-1, 1]), ('mode', '0640'), ('uid', -1), ('gid', False),
                       ('links', None), ('sha256', 'synthetic-retention-secret'), ('sha256', 42))
        for key in ('expected', 'candidate'):
            for field, value in corruptions:
                with self.subTest(key=key, field=field, value=value):
                    body = json.loads(json.dumps(valid))
                    body[key][field] = value
                    path.write_text(json.dumps(body))
                    before = path.read_bytes()
                    with self.assertRaisesRegex(envcommit.CommitError, 'incomplete private') as caught:
                        self.inspect(durable=True)
                    self.preserved(caught.exception)
                    self.assertEqual(path.read_bytes(), before)
        for value in (None, {}, {'unknown': 'synthetic-retention-secret'}):
            body = dict(valid, candidate=value)
            path.write_text(json.dumps(body))
            with self.subTest(candidate=value), self.assertRaisesRegex(envcommit.CommitError, 'incomplete private'):
                self.inspect()

    def test_preparing_live_edit_becomes_sticky_conflict(self):
        self.prepare()
        edited = self.before + b'EDITOR=preserve\n'
        self.target.write_bytes(edited)
        with self.assertRaisesRegex(envcommit.CommitError, 'changed during preparation'):
            self.inspect(durable=True)
        self.assertEqual(self.manifest()['phase'], 'conflict')
        manifest = (self.op / 'manifest.json').read_bytes()
        # A conflict during preparing has no candidate snapshot. Its incomplete
        # record must remain blocked too, not just fully armed conflicts.
        for _ in range(2):
            with self.assertRaises(envcommit.CommitError):
                self.commit()
            self.assertEqual((self.op / 'manifest.json').read_bytes(), manifest)
        self.assertEqual(self.target.read_bytes(), edited)
        self.assertFalse((self.op / 'candidate').exists())

    def test_preparing_cancel_is_durable_but_never_replayable(self):
        self.prepare()
        self.assertEqual(self.inspect(durable=True, cancel=True), 'untouched')
        self.assertEqual(self.manifest()['phase'], 'not_applied')
        self.assertEqual(self.inspect(), 'untouched')
        with self.assertRaisesRegex(envcommit.CommitError, 'incomplete env preparation'):
            self.commit()
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_operation_created_after_existence_check_is_not_overwritten(self):
        real = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path == self.op:
                real(path, mode=0o700)
                (path / 'operator').write_bytes(b'keep-private')
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, 'mkdir', mkdir):
            with self.assertRaisesRegex(envcommit.CommitError, 'operation already exists') as caught:
                self.commit()
        self.preserved(caught.exception)
        self.assertEqual((self.op / 'operator').read_bytes(), b'keep-private')
        self.assertFalse((self.op / 'manifest.json').exists())

    def test_existing_ignore_policy_is_not_overwritten(self):
        base = self.op.parent.parent
        base.mkdir(parents=True, mode=0o700)
        base.parent.chmod(0o700)
        policy = base / '.gitignore'
        policy.write_bytes(b'!operator-private-file\n')
        with self.assertRaisesRegex(envcommit.CommitError, 'ignore policy changed') as caught:
            self.commit()
        self.preserved(caught.exception)
        self.assertEqual(policy.read_bytes(), b'!operator-private-file\n')
        self.assertFalse((self.op / 'candidate').exists())

    def test_short_ignore_policy_write_prevents_staging(self):
        real = os.write

        def short(fd, data):
            return real(fd, data[:1]) if data == b'*\n' else real(fd, data)

        with mock.patch.object(os, 'write', side_effect=short):
            with self.assertRaisesRegex(envcommit.CommitError, 'incomplete retention ignore') as caught:
                self.commit()
        self.preserved(caught.exception)
        self.assertEqual((self.op.parent.parent / '.gitignore').read_bytes(), b'*')
        self.assertFalse((self.op / 'candidate').exists())

    def test_space_reservation_refuses_without_allocating_large_files(self):
        with mock.patch.object(os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=0, f_frsize=4096)):
            with self.assertRaisesRegex(envcommit.CommitError, 'insufficient space') as caught:
                self.commit()
        self.preserved(caught.exception)
        self.assertFalse((self.op / 'candidate').exists())

    def test_three_links_and_unrelated_retention_cannot_authorize_commit(self):
        alias = self.root / 'external-alias'
        extra = self.root / 'external-extra'
        os.link(self.target, alias)
        os.link(self.target, extra)
        with self.assertRaisesRegex(envcommit.CommitError, 'hard links changed/unsupported'):
            self.commit()
        extra.unlink()
        base = self.op.parent
        base.mkdir(parents=True, mode=0o700)
        # A non-ID name, a missing candidate, and a different candidate identity
        # must not be mistaken for the live inode's private hardlink anchor.
        for name in ('not-an-operation', 'a' * 32, 'b' * 32):
            (base / name).mkdir(mode=0o700)
        (base / ('b' * 32) / 'candidate').write_bytes(self.before)
        with self.assertRaisesRegex(envcommit.CommitError, 'hard links changed/unsupported') as caught:
            self.commit()
        self.preserved(caught.exception)
        self.assertTrue(os.path.samefile(alias, self.target))
        self.assertFalse(self.op.exists())

    def test_initial_alias_retarget_is_refused_by_commit_itself(self):
        other = self.root / 'other.env'
        other.write_bytes(self.before)
        alias = self.root / 'selected.env'
        alias.symlink_to(other)
        with self.assertRaisesRegex(envcommit.CommitError, 'env target changed') as caught:
            self.commit(alias)
        self.preserved(caught.exception)
        self.assertEqual(other.read_bytes(), self.before)
        self.assertFalse(self.op.exists())

    def test_stale_parent_descriptor_does_not_authorize_new_path(self):
        self.arm()
        body = self.manifest()
        moved = self.root / 'moved'
        moved.mkdir()
        with envcommit._dir(moved) as stale, envcommit._dir(self.op, True) as opfd:
            with self.assertRaisesRegex(envcommit.CommitError, 'path or parent changed'):
                envcommit._inspect(self.target, self.target, stale, opfd, body, durable=False)
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertEqual(self.manifest()['phase'], 'armed')

    def test_resumed_exchange_retains_operator_edit_and_records_conflict(self):
        self.arm()
        real = envcommit.exchange
        edited = self.before + b'EDITOR=synthetic-retention-secret\n'

        def race(src_fd, src, dst_fd, dst):
            self.assertEqual(src, 'slot')
            self.target.write_bytes(edited)
            return real(src_fd, src, dst_fd, dst)

        with mock.patch.object(envcommit, 'exchange', side_effect=race):
            with self.assertRaisesRegex(envcommit.CommitError, 'displaced at commit') as caught:
                self.commit()
        self.assertEqual((self.op / 'slot').read_bytes(), edited)
        self.assertEqual(self.target.read_bytes(), self.after)
        self.assertEqual(self.manifest()['phase'], 'conflict')
        self.assertNotIn('synthetic-retention-secret', str(caught.exception))
        for _ in range(2):
            with self.assertRaisesRegex(envcommit.CommitError, 'previously recorded conflict'):
                self.commit()
        self.assertEqual((self.op / 'slot').read_bytes(), edited)

    def test_uncertain_native_completion_reconciles_without_second_exchange(self):
        self.arm()
        real = envcommit.exchange

        def uncertain(*args):
            real(*args)
            raise OSError(errno.EINTR, 'synthetic-retention-secret')

        with mock.patch.object(envcommit, 'exchange', side_effect=uncertain):
            with self.assertRaises(envcommit.CommitError) as caught:
                self.commit()
        self.assertNotIn('synthetic-retention-secret', str(caught.exception))
        self.assertEqual(self.manifest()['phase'], 'armed')
        self.assertEqual(self.target.read_bytes(), self.after)
        identities = [p.stat().st_ino for p in (self.target, self.op / 'candidate', self.op / 'slot')]
        before = (self.op / 'manifest.json').read_bytes()
        with mock.patch.object(envcommit, 'exchange', wraps=envcommit.exchange) as exchange:
            envcommit.check_resume(self.target, self.target, self.op_id,
                                   hashlib.sha256(self.before).hexdigest(), hashlib.sha256(self.after).hexdigest())
            self.assertEqual((self.op / 'manifest.json').read_bytes(), before, 'preview must not publish accepted')
            self.commit()
            self.commit()
        exchange.assert_not_called()
        self.assertEqual(self.manifest()['phase'], 'accepted')
        self.assertEqual([p.stat().st_ino for p in (self.target, self.op / 'candidate', self.op / 'slot')], identities)
        self.assertEqual((self.op / 'slot').read_bytes(), self.before)

    def test_changed_anchor_after_armed_is_never_used_for_replay(self):
        self.arm()
        (self.op / 'candidate').write_bytes(b'operator-anchor-private')
        with mock.patch.object(envcommit, 'exchange', wraps=envcommit.exchange) as exchange:
            with self.assertRaisesRegex(envcommit.CommitError, 'retained candidate changed'):
                self.commit()
        exchange.assert_not_called()
        self.assertEqual((self.op / 'slot').read_bytes(), b'operator-anchor-private')
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertEqual(self.manifest()['phase'], 'conflict')

    def test_candidate_chown_failure_is_private_and_does_not_exchange(self):
        with mock.patch.object(os, 'fchown', side_effect=PermissionError(errno.EPERM, 'synthetic-retention-secret')):
            with self.assertRaises(envcommit.CommitError) as caught:
                self.commit()
        self.preserved(caught.exception)
        self.assertEqual(self.manifest()['phase'], 'preparing')
        self.assertEqual(stat.S_IMODE((self.op / 'candidate').stat().st_mode), 0o600)
        self.assertFalse((self.op / 'slot').exists())


@unittest.skipUnless(sys.platform == 'linux', 'native Linux mountinfo/fstatfs qualification')
class LinuxMountQualificationTests(unittest.TestCase):
    def test_native_ext4_and_longest_escaped_mount_match(self):
        with tempfile.TemporaryDirectory(prefix='mount with space-') as temporary:
            path = Path(temporary).resolve()
            with envcommit._dir(path) as fd:
                self.assertEqual(envcommit._filesystem(fd), 'ext4', 'requires real ext4 temp space')
                encoded = str(path).replace(' ', r'\040')
                lines = ('1 0 8:1 / / rw - ext4 disk rw\n'
                         '2 1 8:2 / /unrelated rw - ext3 other rw\n'
                         '3 1 8:3 / ' + encoded + ' rw - ext3 disk rw\n')
                with mock.patch.object(Path, 'read_text', return_value=lines):
                    with self.assertRaisesRegex(envcommit.CommitError, 'no replacement fallback'):
                        envcommit._filesystem(fd)
                # A child of that mount is rejected too, not just the exact mountpoint.
                child = path / 'child'
                child.mkdir()
                with envcommit._dir(child) as childfd, mock.patch.object(Path, 'read_text', return_value=lines):
                    with self.assertRaises(envcommit.CommitError):
                        envcommit._filesystem(childfd)
                for mountinfo in ('', '1 0 8:1 / /unrelated rw - ext4 disk rw\n'):
                    with self.subTest(mountinfo=mountinfo), mock.patch.object(Path, 'read_text', return_value=mountinfo):
                        with self.assertRaises(envcommit.CommitError):
                            envcommit._filesystem(fd)

    def test_native_proc_magic_is_rejected_even_if_mountinfo_claims_ext4(self):
        with envcommit._dir(Path('/proc')) as fd, mock.patch.object(
                Path, 'read_text', return_value='1 0 8:1 / / rw - ext4 disk rw\n'):
            with self.assertRaisesRegex(envcommit.CommitError, 'no replacement fallback'):
                envcommit._filesystem(fd)


@unittest.skipUnless(sys.platform == 'darwin', 'native macOS extended ACLs')
class DarwinAclQualificationTests(unittest.TestCase):
    def test_real_extended_acl_is_refused_on_files_and_private_directories(self):
        import pwd
        with tempfile.TemporaryDirectory(prefix='commit-acl-') as temporary:
            root = Path(temporary).resolve()
            target = root / '.env'
            target.write_bytes(b'V=v1\n')
            private = root / 'private'
            private.mkdir(mode=0o700)
            user = pwd.getpwuid(os.geteuid()).pw_name
            for path in (target, private):
                subprocess.run(['/bin/chmod', '+a', 'user:' + user + ' allow read', str(path)], check=True,
                               capture_output=True)
                fd = os.open(path, os.O_RDONLY)
                try:
                    with self.subTest(path=path.name), self.assertRaisesRegex(envcommit.CommitError, 'extended ACLs'):
                        envcommit._plain_metadata(fd)
                finally:
                    os.close(fd)
            self.assertEqual(target.read_bytes(), b'V=v1\n')

    def test_acl_query_error_is_not_mistaken_for_no_acl(self):
        native = envcommit._libc()
        query = mock.Mock()

        def failed(*args):
            ctypes.set_errno(errno.EIO)
            return None

        query.side_effect = failed

        class FailedAclQuery:
            acl_get_fd_np = query

            def __getattr__(self, name):
                return getattr(native, name)

        with tempfile.TemporaryFile() as stream, mock.patch.object(envcommit, '_libc', return_value=FailedAclQuery()):
            with self.assertRaisesRegex(envcommit.CommitError, 'cannot establish private ACL-free'):
                envcommit._plain_metadata(stream.fileno())
        query.assert_called_once()
