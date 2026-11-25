"""Real filesystem/Updater commit-boundary regressions (no Docker mocks in the commit)."""
from __future__ import annotations

import errno
import io
import json
import os
from pathlib import Path
import stat
import unittest
from unittest import mock

import test_update_apply as fixtures
from deploy.update import envfile as envfile_module

UpdaterCase = fixtures.UpdaterCase

try:
    from deploy.update import envcommit
except ImportError:  # The same regression runs against the saved pre-fix source.
    envcommit = None


class CommitBoundaryTests(UpdaterCase):
    interrupt_apply = fixtures.CrashRecoveryPreservationTests.interrupt_apply

    def boundary(self, edit):
        if envcommit is None:
            real = os.replace
            def replace(src, dst, *args, **kwargs):
                if os.path.realpath(dst) == os.path.realpath(self.env_path):
                    edit()
                return real(src, dst, *args, **kwargs)
            return mock.patch.object(envfile_module.os, 'replace', side_effect=replace)
        real = envcommit.exchange
        def exchange(src_fd, src, dst_fd, dst):
            if dst == self.env_path.name:
                edit()
            return real(src_fd, src, dst_fd, dst)
        return mock.patch.object(envcommit, 'exchange', side_effect=exchange)

    def race(self, *, recovering, rename):
        self.build(env_text='\ufeffFUTURE_AGI_VERSION=v0.9.0\r\nMIDDLE=${FUTURE_AGI_VERSION}\r\n'
                            'FUTURE_AGI_VERSION=v1.0.0\r\nODD="\\$X"\r\nLAST=no-newline')
        if recovering:
            self.interrupt_apply()
        before = self.env_bytes()
        candidate = self.original if recovering else before.replace(b'=v0.9.0', b'=v1.1.0').replace(
            b'=v1.0.0', b'=v1.1.0')
        operator = before + b'\r\nUNRECOGNIZED=operator-secret-6ed44700\r\n'
        injected = []
        def edit():
            if injected:
                return
            if rename:
                staged = self.env_path.with_name('editor-publish')
                staged.write_bytes(operator)
                os.replace(staged, self.env_path)
            else:
                self.env_path.write_bytes(operator)
            injected.append(self.env_path.stat().st_ino)
        # In the pre-fix source the editor's own os.replace must not recurse.
        if envcommit is None and rename:
            replace = os.replace
            def edit():
                if not injected:
                    staged = self.env_path.with_name('editor-publish')
                    staged.write_bytes(operator)
                    replace(staged, self.env_path)
                    injected.append(self.env_path.stat().st_ino)
        del self.log[:]
        caught = None
        with self.boundary(edit):
            try:
                self.updater().recover() if recovering else self.apply()
            except Exception as exc:
                caught = exc
        files = [self.env_path] + list(self.env_path.parent.glob('.futureagi-updates/env-commits/*/*/slot'))
        files += list(self.env_path.parent.glob('.futureagi-updates/env-commits/*/*/candidate'))
        contents = [p.read_bytes() for p in files if p.is_file()]
        observed = (bool(injected), operator in contents, candidate in contents,
                    self.journal() is not None, bool(caught), not self.receipts())
        self.assertEqual(observed, (True, True, True, True, True, True),
                         'injected, operator retained, candidate retained, journal kept, conflict, no receipt')
        self.assertFalse(any(call[0] in ('pull', 'up') for call in self.compose_calls()))
        snapshot = self.env_bytes()
        for _ in range(2):
            with self.assertRaises(Exception):
                self.updater().recover()
            self.assertEqual(self.env_bytes(), snapshot)
            self.assertIsNotNone(self.journal())
            self.assertFalse(self.receipts())
        self.assertNotIn('operator-secret-6ed44700', self.out.getvalue())
        self.assertNotIn('operator-secret-6ed44700', str(caught))

    def test_forward_in_place_at_final_commit(self):
        self.race(recovering=False, rename=False)

    def test_forward_rename_at_final_commit(self):
        self.race(recovering=False, rename=True)

    def test_recovery_in_place_at_final_commit(self):
        self.race(recovering=True, rename=False)

    def test_recovery_rename_at_final_commit(self):
        self.race(recovering=True, rename=True)


class ProtocolTests(UpdaterCase):
    interrupt_apply = fixtures.CrashRecoveryPreservationTests.interrupt_apply
    boundary = CommitBoundaryTests.boundary

    def operation(self, direction='forward'):
        recovery = self.journal()['env_recovery']
        return envcommit.directory(Path(recovery['target']), recovery['commit'][direction])

    def test_same_bytes_new_inode_at_boundary_is_a_conflict(self):
        before = self.env_bytes()
        def edit():
            staged = self.root / 'editor'
            staged.write_bytes(before)
            os.replace(staged, self.env_path)
        with self.boundary(edit), self.assertRaises(Exception):
            self.apply()
        self.assertEqual((self.operation() / 'slot').read_bytes(), before)
        for _ in range(2):
            with self.assertRaises(Exception):
                self.updater().recover()
        self.assertFalse(self.receipts())

    def test_changed_mode_at_boundary_is_retained_without_chmod(self):
        with self.boundary(lambda: self.env_path.chmod(0o600)), self.assertRaises(Exception):
            self.apply()
        slot = self.operation() / 'slot'
        self.assertEqual(stat.S_IMODE(slot.stat().st_mode), 0o600)
        self.assertEqual(slot.read_bytes(), self.original)
        self.assertIsNotNone(self.journal())

    def test_symlink_substitution_is_captured_not_followed(self):
        other = self.root / 'do-not-change'
        other.write_bytes(b'operator-only-secret')
        def edit():
            self.env_path.unlink()
            self.env_path.symlink_to(other)
        with self.boundary(edit), self.assertRaises(Exception):
            self.apply()
        self.assertTrue((self.operation() / 'slot').is_symlink())
        self.assertEqual(other.read_bytes(), b'operator-only-secret')
        for _ in range(2):
            with self.assertRaises(Exception):
                self.updater().recover()
        self.assertFalse(self.receipts())

    def test_deleted_target_is_not_recreated(self):
        with self.boundary(self.env_path.unlink), self.assertRaises(Exception):
            self.apply()
        self.assertFalse(os.path.lexists(self.env_path))
        self.assertTrue((self.operation() / 'candidate').is_file())
        with self.assertRaises(Exception):
            self.updater().recover()
        self.assertFalse(os.path.lexists(self.env_path))
        self.assertIsNotNone(self.journal())

    def test_post_exchange_arrival_is_never_swapped_back(self):
        real = envcommit.exchange
        arrival = b'FUTURE_AGI_VERSION=v9\nUNRECOGNIZED=third-writer\n'
        def exchange(a, b, c, d):
            real(a, b, c, d)
            if d == self.env_path.name:
                staged = self.root / 'arrival'
                staged.write_bytes(arrival)
                os.replace(staged, self.env_path)
        with mock.patch.object(envcommit, 'exchange', side_effect=exchange), self.assertRaises(Exception):
            self.apply()
        self.assertEqual(self.env_bytes(), arrival)
        self.assertEqual((self.operation() / 'slot').read_bytes(), self.original)
        self.assertIn(b'v1.1.0', (self.operation() / 'candidate').read_bytes())
        for _ in range(2):
            with self.assertRaises(Exception):
                self.updater().recover()
            self.assertEqual(self.env_bytes(), arrival)

    def test_old_descriptor_survives_receipt_and_a_later_update(self):
        with self.env_path.open('r+b') as editor:
            self.apply()
            refs = list(self.root.glob('.futureagi-updates/env-commits/*/*/slot'))
            self.assertEqual(len(refs), 1)
            editor.seek(0, os.SEEK_END)
            editor.write(b'\nODD_NAME=late-editor-secret\n')
            editor.flush()
            os.fsync(editor.fileno())
            self.assertIn(b'late-editor-secret', refs[0].read_bytes())
            self.apply(self.plan(target='v1.2.0'))
            self.assertIn(b'late-editor-secret', refs[0].read_bytes())
            self.assertNotIn('late-editor-secret', self.out.getvalue())
            self.assertNotIn('late-editor-secret', json.dumps(self.receipts()))

    def test_private_containment_and_receipt_allowlist(self):
        self.interrupt_apply()
        op = self.operation()
        for entry in (op, op.parent, op.parent.parent, op.parent.parent.parent):
            self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((op / 'manifest.json').stat().st_mode), 0o600)
        record = self.journal()
        record['unrecognized_private_extension'] = {'payload': 'secret-only-private'}
        receipt = self.state.write_receipt(record)
        public = receipt.read_text()
        self.assertNotIn('secret-only-private', public)
        self.assertNotIn('env_recovery', public)
        self.assertNotIn(record['env_recovery']['before_sha256'], public)
        self.assertEqual(len(list(self.state.path.glob('env-retained-*.json'))), 1)

    def test_conflict_cannot_be_cleared_by_a_matching_receipt(self):
        with self.boundary(lambda: self.env_path.write_bytes(self.original + b'\nOPERATOR=x\n')):
            with self.assertRaises(Exception):
                self.apply()
        record = self.journal()
        record['status'] = 'succeeded'
        self.state.write_receipt(record)
        for _ in range(2):
            with self.assertRaises(Exception):
                self.updater().recover()
            self.assertIsNotNone(self.journal())
        from deploy.update.apply import preview_recovery
        with self.assertRaises(Exception):
            preview_recovery(self.inst, record, self.receipts())

    def test_missing_or_invalid_sidecars_block_without_mutation(self):
        import shutil
        for corruption in ('manifest', 'candidate', 'slot', 'unknown-format', 'truncated', 'traversal', 'all'):
            case = ProtocolTests()
            case.setUp()
            try:
                case.interrupt_apply()
                op = case.operation()
                if corruption in ('candidate', 'slot', 'manifest'):
                    (op / ('manifest.json' if corruption == 'manifest' else corruption)).unlink()
                elif corruption == 'all':
                    shutil.rmtree(op)
                elif corruption == 'truncated':
                    (op / 'manifest.json').write_text('{')
                elif corruption == 'unknown-format':
                    data = json.loads((op / 'manifest.json').read_text())
                    data['format'] = 99
                    (op / 'manifest.json').write_text(json.dumps(data))
                else:
                    data = case.journal()
                    data['env_recovery']['commit']['forward'] = '../../outside'
                    case.state.write_journal(data)
                before = case.env_bytes()
                for _ in range(2):
                    with self.subTest(corruption=corruption), self.assertRaises(Exception):
                        case.updater().recover()
                    self.assertEqual(case.env_bytes(), before)
                    self.assertIsNotNone(case.journal())
                    self.assertFalse(case.receipts())
            finally:
                case.doCleanups()

    def test_valid_legacy_inverse_gets_durable_reference_before_write(self):
        self.interrupt_apply()
        record = self.journal()
        del record['env_recovery']['commit']
        record['journal_version'] = 1
        self.state.write_journal(record)
        real = envcommit.exchange
        seen = []
        def exchange(a, b, c, d):
            if d == self.env_path.name:
                seen.append(self.journal()['env_recovery']['commit'])
                self.assertEqual(self.journal()['journal_version'], 2)
            return real(a, b, c, d)
        with mock.patch.object(envcommit, 'exchange', side_effect=exchange):
            self.updater().recover()
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.env_bytes(), self.original)

    def test_unsupported_backend_and_filesystem_never_mutate_target(self):
        for point in ('_libc', '_filesystem'):
            with self.subTest(point=point):
                with mock.patch.object(envcommit, point, side_effect=envcommit.CommitError('unsupported platform')):
                    with self.assertRaises(Exception):
                        self.apply()
                self.assertEqual(self.env_bytes(), self.original)
                self.assertFalse(self.receipts())
                # No env commit existed. Recovery validates the untouched endpoint.
                self.updater().recover()
                self.state.receipts()[-1].unlink()  # exact fixture-owned receipt only

    def test_external_hard_link_and_retention_budget_are_refused(self):
        alias = self.root / 'external-hard-link'
        os.link(self.env_path, alias)
        with self.assertRaises(Exception):
            self.apply()
        self.assertEqual(alias.read_bytes(), self.original)
        alias.unlink()
        self.updater().recover()
        with mock.patch.object(envcommit, '_MAX_OPERATIONS', 0), self.assertRaises(Exception):
            self.apply()
        self.assertEqual(self.env_bytes(), self.original)

    def test_alias_retarget_before_preparation_never_commits_to_the_other_file(self):
        alias = self.root / 'selected.env'
        alias.symlink_to(self.env_path)
        self.inst.env_alias = alias
        self.inst.env_target = self.env_path.resolve()
        other = self.root / 'other.env'
        other.write_bytes(self.original)
        alias.unlink()
        alias.symlink_to(other)
        before = other.stat().st_ino
        with mock.patch.object(envcommit, 'exchange', wraps=envcommit.exchange) as exchanged:
            with self.assertRaises(Exception):
                self.apply()
        exchanged.assert_not_called()
        self.assertEqual(other.stat().st_ino, before)
        self.assertEqual(other.read_bytes(), self.original)
        self.assertEqual(self.env_bytes(), self.original)

    def test_stable_alias_and_boundary_retarget(self):
        alias = self.root / 'selected.env'
        alias.symlink_to(self.env_path)
        self.inst.env_alias = alias
        other = self.root / 'other.env'
        other.write_bytes(self.original)
        def edit():
            alias.unlink()
            alias.symlink_to(other)
        with self.boundary(edit), self.assertRaises(Exception):
            self.apply()
        self.assertEqual(other.read_bytes(), self.original)
        self.assertEqual((self.operation() / 'slot').read_bytes(), self.original)
        with self.assertRaises(Exception):
            self.updater().recover()
        self.assertIsNotNone(self.journal())

    def test_incomplete_main_journal_never_authorizes_a_write(self):
        for value in ('{', '[]', '{"journal_version":99}', '{"journal_version":2}'):
            self.state.ensure()
            self.state.journal_path.write_text(value)
            with self.assertRaises(Exception):
                self.updater().recover()
            self.assertEqual(self.env_bytes(), self.original)
            self.assertEqual(self.state.journal_path.read_text(), value)

    def test_interrupted_rollback_conflict_stays_blocked(self):
        receipt = self.apply()
        self.inst.failures['config'] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.rollback(receipt)
        del self.inst.failures['config']
        before = self.env_bytes()
        with self.boundary(lambda: self.env_path.write_bytes(before + b'\nEDITOR=keep\n')):
            with self.assertRaises(Exception):
                self.updater().recover()
        op = self.operation('restore')
        self.assertIn(b'EDITOR=keep', (op / 'slot').read_bytes())
        for _ in range(2):
            with self.assertRaises(Exception):
                self.updater().recover()
            self.assertIsNotNone(self.journal())
        self.assertEqual(len(self.receipts()), 1)

    def test_conflict_namespace_barriers_precede_the_durable_conflict_marker(self):
        from contextlib import ExitStack
        exchanged = []
        synced = []
        markers = []
        real_exchange, real_sync, real_json = envcommit.exchange, envcommit.sync_dir, envcommit._json
        def exchange(a, b, c, d):
            if b == 'slot':
                self.env_path.write_bytes(self.original + b'\nBOUNDARY=operator\n')
            result = real_exchange(a, b, c, d)
            if b == 'slot':
                exchanged.append({a, c})
                synced.clear()
            return result
        def sync(fd):
            result = real_sync(fd)
            if exchanged:
                synced.append(fd)
            return result
        def publish(fd, name, body):
            if body.get('phase') == 'conflict':
                markers.append(set(synced))
            return real_json(fd, name, body)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(envcommit, 'exchange', side_effect=exchange))
            stack.enter_context(mock.patch.object(envcommit, 'sync_dir', side_effect=sync))
            stack.enter_context(mock.patch.object(envcommit, '_json', side_effect=publish))
            with self.assertRaises(Exception):
                self.apply()
        self.assertEqual(len(exchanged), 1)
        self.assertEqual(len(markers), 1)
        self.assertTrue(exchanged[0].issubset(markers[0]), 'both changed directories must precede conflict publication')
        self.assertIsNotNone(self.journal())
        self.assertFalse(self.receipts())

    def test_visible_receipt_is_synced_again_before_journal_cleanup(self):
        with mock.patch.object(self.state, 'clear_journal', side_effect=SystemExit(137)):
            with self.assertRaises(SystemExit):
                self.apply()
        receipt = self.state.receipts()[-1]
        number = receipt.stat().st_ino
        current = self.env_bytes()
        real = envcommit.sync_file
        def sync(fd):
            if os.fstat(fd).st_ino == number:
                raise OSError(errno.EIO, 'fixture receipt barrier')
            return real(fd)
        with mock.patch.object(envcommit, 'sync_file', side_effect=sync):
            with self.assertRaises(Exception):
                self.updater().recover()
        self.assertIsNotNone(self.journal())
        self.assertEqual(self.env_bytes(), current)
        self.updater().recover()
        self.assertIsNone(self.journal())
        self.assertEqual(self.env_bytes(), current)
        self.assertEqual(len(self.receipts()), 1)

    def test_missing_env_bootstrap_diagnostics_are_read_only(self):
        import contextlib
        from deploy.update import __main__ as cli
        self.interrupt_apply()
        journal = self.state.journal_path.read_bytes()
        self.env_path.unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                mock.patch.object(cli, 'Updater', side_effect=AssertionError('no mutation')):
            code = cli.main(['--root', str(self.root), 'rollback'])
        self.assertEqual(code, 2)
        self.assertIn('read-only', output.getvalue())
        self.assertIn(str(self.state.journal_path), output.getvalue())
        self.assertFalse(self.env_path.exists())
        self.assertEqual(self.state.journal_path.read_bytes(), journal)
        self.assertNotIn(fixtures.SECRET, output.getvalue())

    def test_unpreserved_xattrs_are_rejected_before_exchange(self):
        import ctypes
        import sys
        fd = os.open(str(self.env_path), os.O_RDWR)
        try:
            if sys.platform == 'darwin':
                setter = ctypes.CDLL(None, use_errno=True).fsetxattr
                setter.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
                setter.restype = ctypes.c_int
                value = ctypes.create_string_buffer(b'private-xattr-content')
                self.assertEqual(setter(fd, b'test.fagi', value, 21, 0, 0), 0)
            else:
                os.setxattr(fd, 'user.fagi', b'private-xattr-content')
        finally:
            os.close(fd)
        with self.assertRaises(Exception) as caught:
            self.apply()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertNotIn('private-xattr-content', str(caught.exception))
        self.assertFalse(self.receipts())
        self.assertFalse(list(self.root.glob('.futureagi-updates/env-commits/*/*/slot')))

    def test_nonregular_boundary_entries_are_retained_without_reading_them(self):
        for kind in ('directory', 'fifo'):
            case = ProtocolTests()
            case.setUp()
            try:
                def edit():
                    case.env_path.unlink()
                    if kind == 'directory':
                        case.env_path.mkdir()
                        (case.env_path / 'private').write_bytes(b'never-read-this-secret')
                    else:
                        os.mkfifo(case.env_path)
                with self.subTest(kind=kind), case.boundary(edit), self.assertRaises(Exception) as caught:
                    case.apply()
                slot = case.operation() / 'slot'
                self.assertTrue(slot.is_dir() if kind == 'directory' else stat.S_ISFIFO(slot.lstat().st_mode))
                self.assertIsNotNone(case.journal())
                self.assertFalse(case.receipts())
                self.assertNotIn('never-read-this-secret', str(caught.exception))
            finally:
                case.doCleanups()

    def test_unsupported_platform_is_rejected_without_a_native_call(self):
        with mock.patch.object(envcommit.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(envcommit.CommitError, 'no replacement fallback'):
                envcommit.exchange(-1, 'a', -1, 'b')

    def test_missing_native_symbol_has_no_replace_fallback(self):
        with mock.patch.object(envcommit, '_libc', return_value=object()), \
                mock.patch.object(os, 'replace', side_effect=AssertionError('unsafe fallback')):
            with self.assertRaisesRegex(envcommit.CommitError, 'unavailable'):
                envcommit.exchange(-1, 'a', -1, 'b')

    def test_exchange_requires_armed_manifest_and_durable_journal_references(self):
        real = envcommit.exchange
        seen = []
        def exchange(a, b, c, d):
            if b == 'slot':
                record = self.journal()
                self.assertEqual(record['journal_version'], 2)
                ref = record['env_recovery']['commit']
                envcommit.validate_reference(ref)
                op = self.operation()
                manifest = json.loads((op / 'manifest.json').read_text())
                self.assertTrue(os.path.samefile(op / 'candidate', op / 'slot'))
                seen.append(manifest['phase'])
            return real(a, b, c, d)
        with mock.patch.object(envcommit, 'exchange', side_effect=exchange):
            self.apply()
        self.assertEqual(seen, ['armed'])

    def test_native_errors_and_link_failures_are_preserving(self):
        for operation in ('exchange', 'link'):
            for code in (errno.EIO, errno.EXDEV, errno.EBUSY, errno.EPERM, errno.EROFS,
                         errno.EINTR, errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                case = ProtocolTests()
                case.setUp()
                try:
                    owner = envcommit if operation == 'exchange' else os
                    real = getattr(owner, operation)
                    def failing(*args, **kwargs):
                        if operation == 'link' or args[1] == 'slot':
                            raise OSError(code, 'unknown-secret-must-not-leak')
                        return real(*args, **kwargs)
                    with self.subTest(operation=operation, errno=code), mock.patch.object(
                            owner, operation, side_effect=failing):
                        with self.assertRaises(Exception) as caught:
                            case.apply()
                    self.assertEqual(case.env_bytes(), case.original)
                    self.assertIsNotNone(case.journal())
                    self.assertFalse(case.receipts())
                    self.assertNotIn('unknown-secret-must-not-leak', str(caught.exception))
                finally:
                    case.doCleanups()

    def test_inverse_retention_keeps_a_late_old_descriptor(self):
        self.interrupt_apply()
        with self.env_path.open('r+b') as editor:
            ref = self.journal()['env_recovery']
            self.updater().recover()
            editor.seek(0, os.SEEK_END)
            editor.write(b'\nLATE_INVERSE=keep-this-secret\n')
            editor.flush()
            os.fsync(editor.fileno())
            op = envcommit.directory(Path(ref['target']), ref['commit']['restore'])
            self.assertIn(b'LATE_INVERSE=keep-this-secret', (op / 'slot').read_bytes())
            self.assertEqual(self.env_bytes(), self.original)
            self.assertIsNone(self.journal())
            self.assertNotIn('keep-this-secret', json.dumps(self.receipts()))


class DurabilityMatrixTests(unittest.TestCase):
    """Crash each actual protocol call, not a synthetic reimplementation.

    Partial preparation may require manual recovery; a complete armed exchange
    must reconcile. All cases retain data and never turn a conflict into success.
    """
    def instrument(self, stack, events, fault=None, error=None):
        for owner, name in ((envcommit, 'sync_file'), (envcommit, 'sync_dir'), (envcommit, '_json'),
                            (envcommit, 'exchange'), (envcommit.os, 'link'),
                            (fixtures.receipt_module, '_write_json'), (fixtures.receipt_module, '_fsync_dir')):
            real = getattr(owner, name)
            def wrapper(*args, _real=real, _name=name, **kwargs):
                events.append((_name, 'before'))
                if len(events) == fault:
                    raise error
                result = _real(*args, **kwargs)
                events.append((_name, 'after'))
                if len(events) == fault:
                    raise error
                return result
            stack.enter_context(mock.patch.object(owner, name, side_effect=wrapper))

    def matrix(self, recovering=False, fault_errors=False):
        from contextlib import ExitStack
        def fixture():
            case = ProtocolTests()
            case.setUp()
            if recovering:
                case.interrupt_apply()
            return case
        case = fixture()
        events = []
        try:
            with ExitStack() as stack:
                self.instrument(stack, events)
                case.updater().recover() if recovering else case.apply()
        finally:
            case.doCleanups()
        for number, step in enumerate(events, 1):
            # Fault every durable step; scoped errno failures do not fill/disrupt a host.
            case = fixture()
            try:
                error = OSError(errno.ENOSPC, 'fixture injected') if fault_errors else SystemExit(137)
                with self.subTest(recovering=recovering, step=step, number=number), ExitStack() as stack:
                    self.instrument(stack, [], number, error)
                    with self.assertRaises(BaseException):
                        case.updater().recover() if recovering else case.apply()
                current = case.env_bytes()
                self.assertIn(current, (case.original, case.with_versions({'FUTURE_AGI_VERSION': 'v1.1.0'})))
                journal = case.journal()
                completed = case.receipts()
                if journal is None:
                    # Before the first journal publication nothing may change;
                    # after durable receipt + unlink, only completed runs may remain.
                    self.assertTrue(current == case.original or completed)
                expected = current if ((journal or {}).get('stage') == 'rollout' or
                                       completed and completed[-1]['status'] == 'succeeded') else case.original
                try:
                    case.updater().recover()
                except Exception:
                    self.assertIsNotNone(case.journal())
                    stable = case.env_bytes()
                    with self.assertRaises(Exception):
                        case.updater().recover()
                    self.assertEqual(case.env_bytes(), stable)
                else:
                    self.assertEqual(case.env_bytes(), expected)
                    self.assertIsNone(case.journal())
                    self.assertIsNone(case.updater().recover())
                # Every successfully displaced original inode is still named.
                slots = list(case.root.glob('.futureagi-updates/env-commits/*/*/slot'))
                if current != case.original:
                    self.assertIn(case.original, [p.read_bytes() for p in slots])
            finally:
                case.doCleanups()

    def test_kills_before_and_after_forward_protocol_steps(self):
        self.matrix()

    def test_kills_before_and_after_inverse_protocol_steps(self):
        self.matrix(recovering=True)

    def test_enospc_at_forward_protocol_steps(self):
        self.matrix(fault_errors=True)

    def test_enospc_at_inverse_protocol_steps(self):
        self.matrix(recovering=True, fault_errors=True)
