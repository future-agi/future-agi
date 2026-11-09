"""The issuer key read from a Kubernetes Secret volume (READINESS L09, lane PLATFORM-HELM).

The kubelet's atomic writer lays a Secret volume out as `<dir>/<name> -> ..data/<name>`, with
`..data -> ..<timestamp>/`, and with `fsGroup` set the files are owned by root, group fsGroup, mode
0440. These tests build that layout on disk and simulate the root owner, since a test process cannot
chown to root. The kind and staging installs prove the same path against a real kubelet.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from accounts import omega_session
from accounts.omega_session import DelegationUnavailable, HostAssertionSigner

NAME = "signing.pem"


def pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def stat_result(*, mode, uid, gid, size=1700):
    # os.stat_result takes the first ten fields in this order.
    return os.stat_result((mode, 1, 1, 1, uid, gid, size, 0, 0, 0))


class SecretVolumeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.mount = Path(self.directory.name) / "omega-issuer"
        self.mount.mkdir(mode=0o755)
        self.snapshot = self.write_snapshot("..2026_10_19_12_00_00.000000001", self.key)
        (self.mount / "..data").symlink_to(self.snapshot.name)
        (self.mount / NAME).symlink_to(os.path.join("..data", NAME))
        self.environ = {
            "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example/omega",
            "OMEGA_SQUAD_DELEGATION_KEY_ID": "host-2026-10",
            "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": str(self.mount / NAME),
        }

    def write_snapshot(self, name, key, mode=0o400):
        snapshot = self.mount / name
        snapshot.mkdir(mode=0o755)
        (snapshot / NAME).write_bytes(pem(key))
        (snapshot / NAME).chmod(mode)
        return snapshot

    def signer(self):
        return HostAssertionSigner.from_environment(self.environ)

    def public_numbers(self, signer):
        return signer.private_key.public_key().public_numbers()

    def as_root_owned(self, *, file_mode, in_group=True):
        """Present the snapshot file as root-owned with the pod's fsGroup as its group."""
        file = self.snapshot / NAME
        file.chmod(file_mode)
        info = file.stat()
        foreign_uid = info.st_uid + 4242
        groups = [info.st_gid] if in_group else [info.st_gid + 4242]
        return [
            mock.patch.object(omega_session, "_SECRET_VOLUME_OWNER_UID", info.st_uid),
            mock.patch.object(omega_session.os, "geteuid", return_value=foreign_uid),
            mock.patch.object(
                omega_session.os, "getegid", return_value=info.st_gid + 4243
            ),
            mock.patch.object(omega_session.os, "getgroups", return_value=groups),
        ]

    def run_patched(self, patches):
        for patch in patches:
            patch.start()
        try:
            return self.signer()
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_the_atomic_writer_layout_is_followed_to_the_key(self):
        signer = self.signer()
        self.assertEqual(
            self.public_numbers(signer), self.key.public_key().public_numbers()
        )

    def test_root_owned_key_readable_through_fsgroup_is_accepted(self):
        for mode in (0o440, 0o400):
            with self.subTest(mode=oct(mode)):
                signer = self.run_patched(self.as_root_owned(file_mode=mode))
                self.assertEqual(
                    self.public_numbers(signer), self.key.public_key().public_numbers()
                )

    def test_root_owned_key_is_refused_when_others_can_read_or_group_can_write(self):
        for mode in (0o444, 0o460, 0o644, 0o470, 0o441):
            with self.subTest(mode=oct(mode)):
                with self.assertRaises(DelegationUnavailable):
                    self.run_patched(self.as_root_owned(file_mode=mode))

    def test_root_owned_key_is_refused_outside_the_process_groups(self):
        with self.assertRaises(DelegationUnavailable):
            self.run_patched(self.as_root_owned(file_mode=0o440, in_group=False))

    def test_a_rotated_secret_is_read_without_restarting(self):
        self.assertEqual(
            self.public_numbers(self.signer()), self.key.public_key().public_numbers()
        )
        # The kubelet writes a new snapshot, then swaps ..data to it in one rename.
        fresh = self.write_snapshot("..2026_10_19_13_00_00.000000002", self.rotated)
        (self.mount / "..data_tmp").symlink_to(fresh.name)
        os.rename(self.mount / "..data_tmp", self.mount / "..data")
        self.assertEqual(
            self.public_numbers(self.signer()),
            self.rotated.public_key().public_numbers(),
        )

    def test_other_links_are_refused(self):
        outside = Path(self.directory.name) / "outside.pem"
        outside.write_bytes(pem(self.key))
        outside.chmod(0o400)
        cases = {
            "absolute target": lambda: self.relink(NAME, str(outside)),
            "relative target outside ..data": lambda: self.relink(
                NAME, os.path.join("..", "outside.pem")
            ),
            "another file name": lambda: self.relink(
                NAME, os.path.join("..data", "other.pem")
            ),
            "..data pointing outside the mount": lambda: self.relink(
                "..data", str(self.snapshot)
            ),
            "..data pointing up": lambda: self.relink("..data", ".."),
            "..data to a name without the dot prefix": lambda: self.rename_snapshot(
                "2026_10_19"
            ),
            "..data to a snapshot that is itself a link": lambda: self.snapshot_as_link(),
        }
        for label, arrange in cases.items():
            with self.subTest(label=label):
                self.setUp()
                arrange()
                with self.assertRaises(DelegationUnavailable):
                    self.signer()

    def test_a_snapshot_directory_others_can_write_is_refused(self):
        for mode in (0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                self.snapshot.chmod(mode)
                with self.assertRaises(DelegationUnavailable):
                    self.signer()
        self.snapshot.chmod(0o755)
        self.signer()

    def test_the_snapshot_file_must_itself_be_private(self):
        (self.snapshot / NAME).chmod(0o644)
        with self.assertRaises(DelegationUnavailable):
            self.signer()

    def test_errors_never_name_the_key_path(self):
        self.relink(NAME, "/etc/passwd")
        with self.assertRaisesRegex(
            DelegationUnavailable, "^Omega delegation is unavailable$"
        ):
            self.signer()

    def relink(self, name, target):
        link = self.mount / name
        link.unlink()
        link.symlink_to(target)

    def rename_snapshot(self, name):
        renamed = self.mount / name
        self.snapshot.rename(renamed)
        self.relink("..data", name)

    def snapshot_as_link(self):
        hidden = self.mount / "..hidden"
        self.snapshot.rename(hidden)
        (self.mount / "..2026_link").symlink_to(hidden)
        self.relink("..data", "..2026_link")


class KeyFileOwnershipTests(unittest.TestCase):
    """The ownership and mode rule on its own, over the cases a real kubelet can produce."""

    EUID = 1000
    GROUPS = {1000}

    def private(self, mode, uid, gid):
        info = stat_result(mode=stat.S_IFREG | mode, uid=uid, gid=gid)
        return omega_session._key_file_is_private(info, self.EUID, self.GROUPS)

    def test_owned_by_the_process(self):
        self.assertTrue(self.private(0o600, 1000, 1000))
        self.assertTrue(self.private(0o400, 1000, 5))
        for mode in (0o640, 0o604, 0o660, 0o644):
            with self.subTest(mode=oct(mode)):
                self.assertFalse(self.private(mode, 1000, 1000))

    def test_root_owned_through_fsgroup(self):
        self.assertTrue(self.private(0o440, 0, 1000))
        self.assertTrue(self.private(0o400, 0, 1000))
        # Only root can write it, and root is already trusted.
        self.assertTrue(self.private(0o640, 0, 1000))
        for mode in (0o444, 0o460, 0o450, 0o441):
            with self.subTest(mode=oct(mode)):
                self.assertFalse(self.private(mode, 0, 1000))
        self.assertFalse(self.private(0o440, 0, 2000))

    def test_owned_by_another_user_is_refused(self):
        for uid in (1, 999, 1001, 65534):
            with self.subTest(uid=uid):
                self.assertFalse(self.private(0o400, uid, 1000))

    def test_not_a_regular_file_or_too_large(self):
        directory = stat_result(mode=stat.S_IFDIR | 0o500, uid=1000, gid=1000)
        self.assertFalse(
            omega_session._key_file_is_private(directory, self.EUID, self.GROUPS)
        )
        large = stat_result(mode=stat.S_IFREG | 0o400, uid=1000, gid=1000, size=16385)
        self.assertFalse(
            omega_session._key_file_is_private(large, self.EUID, self.GROUPS)
        )


if __name__ == "__main__":
    unittest.main()
