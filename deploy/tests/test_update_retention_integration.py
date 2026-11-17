"""Real native envcommit retention with config capture; no real Docker or timer."""
from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from deploy.tests import test_update_standalone_backup as fixture
from deploy.tests.test_update_runner_state import registration
from deploy.update import envcommit, envfile, standalone_backup as backup
from deploy.update.baseline import BaselineStore
from deploy.update.runner_state import RunnerStateStore


class RetentionIntegrationTests(unittest.TestCase):
    temp_prefix = 'fagi-upd-e2e-retention-'
    # Reuse only fixture setup/adapters, not its tests or archive implementation.
    setUp = fixture.BackupTests.setUp
    capture = fixture.BackupTests.capture
    make_adapter = fixture.BackupTests.make_adapter
    run_backup = fixture.BackupTests.run_backup
    mutation_calls = fixture.BackupTests.mutation_calls

    def commit(self, raw=None):
        path = self.root / '.env'
        before = path.read_bytes()
        raw = raw or before.replace(b'v1.51.0', b'v1.52.0')
        operation = envcommit.reference()['forward']
        envcommit.commit(path, path, before, raw, path.stat().st_mode & 0o7777, operation)
        op = envcommit.directory(path, operation)
        self.assertEqual(envcommit.inspect(path, path, operation), 'accepted')
        self.assertEqual(path.stat().st_nlink, 2)
        self.assertEqual(path.stat().st_ino, (op / 'candidate').stat().st_ino)
        return op, raw

    def test_native_commit_config_and_successor_binding(self):
        old = json.loads(self.binding.raw)
        op, raw = self.commit()
        manifest = (op / 'manifest.json').read_bytes()
        self.assertEqual(backup._files(self.root)['.env'], raw)
        new = json.loads(self.capture(previous=self.binding).raw)
        self.assertEqual(new['files']['.env'], hashlib.sha256(raw).hexdigest())
        for key in ('volumes', 'volume_root_ids', 'images', 'root', 'context', 'daemon'):
            self.assertEqual(new[key], old[key])
        self.assertEqual((op / 'manifest.json').read_bytes(), manifest)
        self.assertEqual((self.root / '.env').stat().st_nlink, 2)
        self.assertFalse(self.mutation_calls())

    def test_baseline_reader_independently_accepts_real_retained_link(self):
        store = RunnerStateStore(self.root, self.root / '.env')
        store.register(registration(store))
        _, raw = self.commit()
        self.assertEqual(BaselineStore(store)._env_bytes(), raw)

    def test_native_inverse_commit_uses_a_new_accepted_candidate(self):
        path = self.root / '.env'
        original = path.read_bytes()
        changes = {'FUTURE_AGI_VERSION': 'v1.52.0'}
        recovery = envfile.recovery_record(path, changes)
        envfile.write_changes(path, changes, recovery=recovery)
        forward = envcommit.directory(path, recovery['commit']['forward'])
        self.assertEqual(backup._files(self.root)['.env'], path.read_bytes())
        envfile.restore_changes(path, recovery)
        inverse = envcommit.directory(path, recovery['commit']['restore'])
        self.assertEqual(envfile.check_commit(path, recovery), 'restored')
        self.assertNotEqual(forward, inverse)
        self.assertEqual(backup._files(self.root)['.env'], original)
        self.assertTrue((forward / 'candidate').exists())
        self.assertEqual((inverse / 'candidate').stat().st_nlink, 2)
        self.capture(previous=self.binding)

    def test_native_commit_then_real_backup_preserves_anchor_and_cold_rules(self):
        op, raw = self.commit()
        self.binding = self.capture(previous=self.binding)
        self.adapter = self.make_adapter()
        original = {cid: c['Image'] for cid, c in self.daemon.containers.items()}
        self.run_backup()
        inventory = json.loads((self.destination / self.expected.attempt_id / 'inventory.json').read_bytes())
        self.assertEqual((self.destination / self.expected.attempt_id / 'config-0').read_bytes(), raw)
        self.assertEqual(inventory['binding']['files']['.env'], hashlib.sha256(raw).hexdigest())
        self.assertEqual({cid: c['Image'] for cid, c in self.daemon.containers.items()}, original)
        self.assertTrue(all(c['State']['Running'] for c in self.daemon.containers.values()))
        self.assertEqual((op / 'candidate').stat().st_ino, (self.root / '.env').stat().st_ino)
        self.assertEqual((self.root / '.env').stat().st_nlink, 2)
        self.assertEqual(sum(call[0] == 'run' for call in self.mutation_calls()), 3)

    def refuses(self):
        self.daemon.calls.clear()
        with self.assertRaises(backup.BackupError) as caught:
            self.capture(previous=self.binding)
        self.assertIn(str(caught.exception), ('unsafe_configuration', 'configuration_changed', 'unsafe_backup_path'))
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertFalse(self.mutation_calls())

    def test_unrelated_hardlink_and_candidate_name_without_record_refuse(self):
        path = self.root / '.env'
        other = self.root / 'untrusted-hardlink'
        os.link(path, other)
        self.refuses()
        # A correctly spelled private candidate alone is not transaction proof.
        op = envcommit.directory(path, 'a' * 32)
        op.mkdir(mode=0o700, parents=True)
        for directory in (op.parent, op.parent.parent, op.parent.parent.parent):
            directory.chmod(0o700)
        other.rename(op / 'candidate')
        self.assertTrue(envcommit._known_links(path, path.stat()))
        self.refuses()
        (op / 'manifest.json').write_text('{"phase":"accepted"}\n')
        (op / 'manifest.json').chmod(0o600)
        self.refuses()

    def test_manifest_identity_target_hash_phase_and_original_user_are_required(self):
        op, _ = self.commit()
        manifest = op / 'manifest.json'
        original = manifest.read_bytes()
        body = json.loads(original)
        changes = [
            ('target', str(self.root / 'different.env')), ('id', 'f' * 32),
            ('parent', [body['parent'][0], body['parent'][1] + 1]),
            ('phase', 'preparing'), ('phase', 'armed'), ('phase', 'not_applied'), ('phase', 'conflict'),
            ('format', 2), ('unknown', 'not-a-transaction-field'),
        ]
        for item in ('candidate', 'expected'):
            for key, value in (('identity', [0, 1]), ('sha256', '0' * 64), ('mode', 0o666),
                               ('uid', os.geteuid() + 1), ('gid', os.getegid() + 1), ('links', 3)):
                changed = copy.deepcopy(body[item])
                changed[key] = value
                changes.append((item, changed))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                changed = dict(body, **{key: value})
                manifest.write_text(json.dumps(changed))
                tampered = manifest.read_bytes()
                self.refuses()
                self.assertEqual(manifest.read_bytes(), tampered)  # no conflict rewrite/repair
        manifest.write_bytes(original)
        self.assertEqual(backup._files(self.root)['.env'], (self.root / '.env').read_bytes())

    def test_live_and_displaced_preimage_content_tampering_refuses(self):
        op, raw = self.commit()
        for path in (op / 'candidate', op / 'slot'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original.replace(b'private', b'foreign'))
                self.refuses()
                path.write_bytes(original)
        self.assertEqual(backup._files(self.root)['.env'], raw)

    def test_third_link_and_swapped_anchor_inode_refuse(self):
        op, raw = self.commit()
        extra = self.root / 'extra-link'
        os.link(self.root / '.env', extra)
        self.refuses()
        # Maintain two links but replace the putative candidate with equal bytes
        # on a different inode; neither names nor content alone authorize it.
        (op / 'candidate').unlink()
        (op / 'candidate').write_bytes(raw)
        (op / 'candidate').chmod(0o600)
        self.assertEqual((self.root / '.env').stat().st_nlink, 2)
        self.refuses()

    def test_non_env_configuration_still_requires_one_link(self):
        self.commit()
        for index, name in enumerate(backup.CONFIG_FILES[1:]):
            with self.subTest(name=name):
                extra = self.root / ('extra-' + str(index))
                os.link(self.root / name, extra)
                self.refuses()
                extra.unlink()

    def test_private_directory_modes_and_nofollow_ancestry(self):
        op, _ = self.commit()
        for directory in (op, op.parent, op.parent.parent, op.parent.parent.parent):
            for mode in (0o750, 0o770, 0o777):
                with self.subTest(directory=directory.name, mode=mode):
                    directory.chmod(mode)
                    try:
                        self.refuses()
                    finally:
                        directory.chmod(0o700)
            with self.subTest(symlink=directory.name):
                saved = directory.with_name(directory.name + '-saved')
                directory.rename(saved)
                directory.symlink_to(saved, target_is_directory=True)
                try:
                    self.refuses()
                finally:
                    directory.unlink()
                    saved.rename(directory)

    def test_manifest_slot_and_live_symlink_fifo_and_directory_refuse(self):
        op, _ = self.commit()
        for path in (op / 'manifest.json', op / 'slot', op / 'candidate', self.root / '.env'):
            for kind in ('symlink', 'fifo', 'directory'):
                with self.subTest(path=path.name, kind=kind):
                    saved = path.with_name(path.name + '-saved')
                    path.rename(saved)
                    if kind == 'symlink':
                        path.symlink_to(saved)
                    elif kind == 'fifo':
                        os.mkfifo(path, 0o600)
                    else:
                        path.mkdir(mode=0o700)
                    try:
                        self.refuses()
                    finally:
                        path.rmdir() if kind == 'directory' else path.unlink()
                        saved.rename(path)

    def test_manifest_links_modes_and_size_are_bounded(self):
        op, _ = self.commit()
        manifest = op / 'manifest.json'
        original = manifest.read_bytes()
        for mode in (0o644, 0o660, 0o4600):
            with self.subTest(mode=mode):
                manifest.chmod(mode)
                try:
                    self.refuses()
                finally:
                    manifest.chmod(0o600)
        extra = op / 'manifest-link'
        os.link(manifest, extra)
        self.refuses()
        extra.unlink()
        manifest.write_bytes(b' ' * 65537)
        self.refuses()
        for invalid in (b'{', b'[]', b'null', b'\xff',
                        original.rstrip()[:-1] + b',"phase":"accepted"}',
                        b'{"nested":' * 1200 + b'null' + b'}' * 1200):
            with self.subTest(invalid=invalid[:30]):
                manifest.write_bytes(invalid)
                self.refuses()
        manifest.write_bytes(original)

    def test_qualification_is_read_only_and_bounded(self):
        op, raw = self.commit()
        before = {str(p): (p.read_bytes(), p.stat().st_nlink)
                  for p in (self.root / '.env', op / 'manifest.json', op / 'candidate', op / 'slot')}
        def forbidden(*args, **kwargs):
            raise AssertionError('qualification must not write, sync, unlink or exchange')
        with mock.patch.object(envcommit, 'sync_file', side_effect=forbidden), \
             mock.patch.object(envcommit, 'sync_dir', side_effect=forbidden), \
             mock.patch.object(envcommit, '_json', side_effect=forbidden), \
             mock.patch.object(envcommit, 'exchange', side_effect=forbidden), \
             mock.patch.object(os, 'unlink', side_effect=forbidden):
            self.assertEqual(backup._files(self.root)['.env'], raw)
        self.assertEqual({str(p): (p.read_bytes(), p.stat().st_nlink)
                          for p in (self.root / '.env', op / 'manifest.json', op / 'candidate', op / 'slot')}, before)
        original_read = os.read
        read_sizes = []
        def bounded_read(fd, count):
            read_sizes.append(count)
            self.assertLessEqual(count, 65536)
            return original_read(fd, count)
        fd = os.open(self.root / '.env', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            with mock.patch.object(os, 'read', side_effect=bounded_read):
                envcommit.qualify_retained_env(self.root / '.env', fd, os.fstat(fd), raw, maximum=backup.MAX_METADATA)
        finally:
            os.close(fd)
        self.assertTrue(read_sizes)

    def test_safe_sticky_tmp_and_private_var_layouts(self):
        # Use real canonical host ancestry, never treat /tmp's symlink as safe.
        roots = [Path(tempfile.gettempdir()).resolve()]
        if sys.platform == 'darwin':
            roots.append(Path('/private/tmp'))
        for parent in dict.fromkeys(roots):
            with self.subTest(parent=parent):
                with tempfile.TemporaryDirectory(prefix='fagi-upd-e2e-retention-', dir=parent) as temporary:
                    root = Path(temporary).resolve()
                    path = root / '.env'
                    path.write_bytes(b'FUTURE_AGI_VERSION=v1.51.0\n')
                    path.chmod(0o600)
                    before, after = path.read_bytes(), b'FUTURE_AGI_VERSION=v1.52.0\n'
                    envcommit.commit(path, path, before, after, 0o600, envcommit.reference()['forward'])
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    try:
                        envcommit.qualify_retained_env(path, fd, os.fstat(fd), after, maximum=backup.MAX_METADATA)
                        for mode in (0o770, 0o777, 0o1777):
                            root.chmod(mode)
                            with self.assertRaisesRegex(envcommit.CommitError, '^unqualified retained env link$'):
                                envcommit.qualify_retained_env(path, fd, os.fstat(fd), after, maximum=backup.MAX_METADATA)
                            root.chmod(0o700)
                    finally:
                        root.chmod(0o700)
                        os.close(fd)

    def test_retained_slot_and_live_size_modes_xattrs_and_owner_refuse(self):
        op, _ = self.commit()
        for path in (op / 'slot', self.root / '.env'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                with path.open('r+b') as out:
                    out.truncate(backup.MAX_METADATA + 1)
                self.refuses()
                path.write_bytes(original)
                path.chmod(0o660)
                self.refuses()
                path.chmod(0o600)
        # Inject a different calling user, not counterfeit fstat/recorded file IO.
        uid = os.geteuid()
        with mock.patch.object(os, 'geteuid', return_value=uid + 1):
            self.refuses()
        if sys.platform == 'darwin':
            # CPython on macOS has no os.setxattr; exercise the real native xattr.
            setter = envcommit._libc().setxattr
            setter.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                               ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
            setter.restype = ctypes.c_int
            self.assertEqual(setter(os.fsencode(op / 'candidate'), b'user.retention-test',
                                    b'not-plain', 9, 0, 0), 0)
        else:
            os.setxattr(op / 'candidate', 'user.retention-test', b'not-plain')
        self.refuses()

    def test_operation_scan_is_bounded(self):
        op, _ = self.commit()
        for index in range(envcommit._MAX_OPERATIONS):
            (op.parent / ('unrelated-' + str(index))).touch()
        self.refuses()

    def test_replacement_during_live_read_refuses(self):
        self.commit()
        original = backup.qualify_retained_env
        def replace_live(path, fd, before, raw, **kwargs):
            # Keep the original descriptor/inode unchanged by moving its name.
            path.rename(self.root / 'moved-env')
            path.write_bytes(raw)
            path.chmod(0o600)
            return original(path, fd, before, raw, **kwargs)
        with mock.patch.object(backup, 'qualify_retained_env', side_effect=replace_live):
            self.refuses()

    def test_content_change_during_descriptor_read_refuses(self):
        self.commit()
        original = backup.qualify_retained_env
        def change_live(path, fd, before, raw, **kwargs):
            path.write_bytes(raw.replace(b'private', b'foreign'))
            return original(path, fd, before, raw, **kwargs)
        with mock.patch.object(backup, 'qualify_retained_env', side_effect=change_live):
            self.refuses()

    def test_evidence_replacement_after_inspection_refuses(self):
        for leaf in ('manifest.json', 'slot', 'candidate', 'operation-directory', 'retention-ancestor'):
            with self.subTest(leaf=leaf):
                self.setUp()
                op, _ = self.commit()
                original = envcommit._inspect
                def replace_after(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if leaf in ('operation-directory', 'retention-ancestor'):
                        path = op if leaf == 'operation-directory' else op.parent.parent
                        saved = path.with_name(path.name + '-saved')
                        path.rename(saved)
                        path.mkdir(mode=0o700)
                    else:
                        path = op / leaf
                        replacement = op / 'replacement'
                        replacement.write_bytes(path.read_bytes())
                        replacement.chmod(0o600)
                        os.replace(replacement, path)
                    return result
                with mock.patch.object(envcommit, '_inspect', side_effect=replace_after):
                    self.refuses()

    def test_armed_postimage_requires_real_engine_reconciliation(self):
        class Crash(BaseException):
            pass
        path = self.root / '.env'
        before = path.read_bytes()
        after = before.replace(b'v1.51.0', b'v1.52.0')
        operation = envcommit.reference()['forward']
        original = envcommit._json
        def interrupt(fd, name, body):
            if body['phase'] == 'accepted':
                raise Crash()
            return original(fd, name, body)
        with mock.patch.object(envcommit, '_json', side_effect=interrupt):
            with self.assertRaises(Crash):
                envcommit.commit(path, path, before, after, 0o600, operation)
        manifest = envcommit.directory(path, operation) / 'manifest.json'
        self.assertEqual(json.loads(manifest.read_bytes())['phase'], 'armed')
        self.assertEqual(envcommit.inspect(path, path, operation), 'accepted')
        self.refuses()
        self.assertEqual(json.loads(manifest.read_bytes())['phase'], 'armed')
        self.assertEqual(envcommit.inspect(path, path, operation, durable=True), 'accepted')
        self.assertEqual(backup._files(self.root)['.env'], after)

    def test_link_fix_does_not_waive_latest_runtime_environment_mismatch(self):
        # Actual backup adapter + finite daemon: setup's other blocker stays red.
        self.daemon.model['services']['app']['environment']['FUTURE_AGI_VERSION'] = 'latest'
        self.daemon.find('app')['Config']['Env'].append('FUTURE_AGI_VERSION=latest')
        (self.root / '.env').write_bytes(b'FUTURE_AGI_VERSION=latest\nSECRET_KEY=private\n')
        self.binding = self.capture()
        self.commit(b'FUTURE_AGI_VERSION=v1.51.0\nSECRET_KEY=private\n')
        self.daemon.model['services']['app']['environment']['FUTURE_AGI_VERSION'] = 'v1.51.0'
        self.assertEqual(len(backup._files(self.root)), len(backup.CONFIG_FILES))
        with self.assertRaisesRegex(backup.BackupError, '^runtime_configuration_changed$'):
            self.capture(previous=self.binding)
        self.assertFalse(self.mutation_calls())
