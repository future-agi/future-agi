"""The host-events secret read from a Kubernetes Secret volume.

The chart mounts `omega.hostEvents.existingSecret` like the issuer key: defaultMode 0440 with the
pod's fsGroup, so the kubelet's atomic writer presents `<dir>/secret -> ..data/secret`, with
`..data -> ..<timestamp>/`, and the file is owned by root with the fsGroup as its group.
`accounts/host_events/config.py` must accept exactly what the issuer key's reader
(`accounts/omega_session.py`) accepts and refuse everything else, or the sender stays
MISCONFIGURED and membership removals never reach Omega EE.

A test process cannot chown to root, so the root owner is simulated the way
`test_omega_session_secret_volume.py` does: the volume owner uid becomes this process's uid and
the process pretends to be another user whose groups include (or not) the file's group.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from accounts import omega_session
from accounts.host_events import config as channel

NAME = "secret"
SECRET = b"k" * 48


class HostEventsSecretVolumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.mount = Path(self.directory.name) / "omega-host-events"
        self.mount.mkdir(mode=0o755)
        self.snapshot = self.write_snapshot("..2026_10_31_12_00_00.000000001", SECRET)
        (self.mount / "..data").symlink_to(self.snapshot.name)
        (self.mount / NAME).symlink_to(os.path.join("..data", NAME))

    def write_snapshot(self, name, content, mode=0o400):
        snapshot = self.mount / name
        snapshot.mkdir(mode=0o755)
        (snapshot / NAME).write_bytes(content)
        (snapshot / NAME).chmod(mode)
        return snapshot

    def environ(self, secret_file=None):
        return {
            "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example/omega",
            "OMEGA_HOST_EVENTS_URL": "https://omega-os.omega.svc:8443/host/events",
            "OMEGA_HOST_EVENTS_KEY_ID": "fa-host-events-1",
            "OMEGA_HOST_EVENTS_SECRET_FILE": str(secret_file or self.mount / NAME),
        }

    def load(self, secret_file=None):
        return channel.load_config(self.environ(secret_file))

    def as_root_owned(self, *, file_mode, in_group=True, path=None):
        """Present the file as root-owned with the pod's fsGroup as its group."""
        file = path or self.snapshot / NAME
        file.chmod(file_mode)
        info = file.stat()
        foreign_uid = info.st_uid + 4242
        groups = [info.st_gid] if in_group else [info.st_gid + 4242]
        return [
            mock.patch.object(omega_session, "_SECRET_VOLUME_OWNER_UID", info.st_uid),
            mock.patch.object(os, "geteuid", return_value=foreign_uid),
            mock.patch.object(os, "getegid", return_value=info.st_gid + 4243),
            mock.patch.object(os, "getgroups", return_value=groups),
        ]

    def load_patched(self, patches, secret_file=None):
        for patch in patches:
            patch.start()
        try:
            return self.load(secret_file)
        finally:
            for patch in reversed(patches):
                patch.stop()

    def assert_ready(self, config, secret=SECRET):
        self.assertEqual((config.state, config.problem), (channel.READY, ""))
        self.assertEqual(config.secret, secret)

    def assert_refused(self, config, problem):
        self.assertEqual(config.state, channel.MISCONFIGURED)
        self.assertEqual(config.problem, problem)
        self.assertEqual(config.secret, b"")

    # -- accepted -----------------------------------------------------------------------------

    def test_the_atomic_writer_layout_is_followed_to_the_secret(self):
        self.assert_ready(self.load())

    def test_a_secret_volume_file_root_owned_readable_through_fsgroup_is_accepted(self):
        for mode in (0o440, 0o400):
            with self.subTest(mode=oct(mode)):
                self.assert_ready(self.load_patched(self.as_root_owned(file_mode=mode)))

    def test_a_plain_file_owned_by_this_user_with_mode_0600_is_accepted(self):
        plain = Path(self.directory.name) / "plain.secret"
        plain.write_bytes(SECRET)
        plain.chmod(0o600)
        self.assert_ready(self.load(plain))

    def test_a_rotated_secret_is_read_without_restarting(self):
        self.assert_ready(self.load())
        rotated = b"r" * 40
        # The kubelet writes a new snapshot, then swaps ..data to it in one rename.
        fresh = self.write_snapshot("..2026_10_31_13_00_00.000000002", rotated)
        (self.mount / "..data_tmp").symlink_to(fresh.name)
        os.rename(self.mount / "..data_tmp", self.mount / "..data")
        self.assert_ready(self.load(), rotated)

    # -- refused: modes and owners ------------------------------------------------------------

    def test_a_world_readable_secret_is_refused(self):
        for mode in (0o444, 0o404, 0o644):
            with self.subTest(owner="root", mode=oct(mode)):
                self.assert_refused(
                    self.load_patched(self.as_root_owned(file_mode=mode)),
                    "secret_file_unsafe",
                )
        plain = Path(self.directory.name) / "plain.secret"
        plain.write_bytes(SECRET)
        for mode in (0o604, 0o644, 0o640):
            with self.subTest(owner="this user", mode=oct(mode)):
                plain.chmod(mode)
                self.assert_refused(self.load(plain), "secret_file_unsafe")

    def test_a_group_or_other_writable_secret_is_refused(self):
        for mode in (0o460, 0o470, 0o450, 0o442, 0o441):
            with self.subTest(owner="root", mode=oct(mode)):
                self.assert_refused(
                    self.load_patched(self.as_root_owned(file_mode=mode)),
                    "secret_file_unsafe",
                )
        for mode in (0o620, 0o602, 0o660):
            with self.subTest(owner="this user", mode=oct(mode)):
                (self.snapshot / NAME).chmod(mode)
                self.assert_refused(self.load(), "secret_file_unsafe")

    def test_a_root_owned_secret_outside_the_process_groups_is_refused(self):
        self.assert_refused(
            self.load_patched(self.as_root_owned(file_mode=0o440, in_group=False)),
            "secret_file_unsafe",
        )

    def test_a_snapshot_directory_others_can_write_is_refused(self):
        for mode in (0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                self.snapshot.chmod(mode)
                self.assert_refused(self.load(), "secret_file_unreadable")
        self.snapshot.chmod(0o755)
        self.assert_ready(self.load())

    # -- refused: links -----------------------------------------------------------------------

    def test_links_outside_the_atomic_writer_layout_are_refused(self):
        outside = Path(self.directory.name) / "outside.secret"
        outside.write_bytes(SECRET)
        outside.chmod(0o400)
        cases = {
            "absolute target": lambda: self.relink(NAME, str(outside)),
            "relative target outside ..data": lambda: self.relink(
                NAME, os.path.join("..", "outside.secret")
            ),
            "another file name": lambda: self.relink(
                NAME, os.path.join("..data", "other")
            ),
            "a chain through another link": lambda: self.chain(),
            "..data pointing outside the mount": lambda: self.relink(
                "..data", str(self.snapshot)
            ),
            "..data pointing up": lambda: self.relink("..data", ".."),
            "..data to a name without the dot prefix": lambda: self.rename_snapshot(
                "2026_10_31"
            ),
            "..data to a snapshot that is itself a link": lambda: (
                self.snapshot_as_link()
            ),
            "the snapshot file itself a link": lambda: self.snapshot_file_as_link(
                outside
            ),
        }
        for label, arrange in cases.items():
            with self.subTest(label=label):
                self.setUp()
                arrange()
                self.assert_refused(self.load(), "secret_file_unreadable")

    # -- refused: size ------------------------------------------------------------------------

    def test_an_oversized_secret_is_refused(self):
        (self.snapshot / NAME).chmod(0o600)
        (self.snapshot / NAME).write_bytes(b"x" * 4097)
        (self.snapshot / NAME).chmod(0o400)
        self.assert_refused(self.load(), "secret_length")
        (self.snapshot / NAME).chmod(0o600)
        (self.snapshot / NAME).write_bytes(b"x" * 20000)
        (self.snapshot / NAME).chmod(0o400)
        config = self.load()
        self.assertEqual(config.state, channel.MISCONFIGURED)
        self.assertEqual(config.secret, b"")

    def test_problems_never_name_the_path_or_the_secret(self):
        self.relink(NAME, "/etc/passwd")
        config = self.load()
        self.assert_refused(config, "secret_file_unreadable")
        self.assertNotIn(str(self.mount), repr(config))
        self.assertNotIn(SECRET.decode(), repr(self.load(self.snapshot / NAME)))

    # -- helpers ------------------------------------------------------------------------------

    def relink(self, name, target):
        link = self.mount / name
        link.unlink()
        link.symlink_to(target)

    def chain(self):
        (self.mount / "hop").symlink_to(os.path.join("..data", NAME))
        self.relink(NAME, "hop")

    def rename_snapshot(self, name):
        renamed = self.mount / name
        self.snapshot.rename(renamed)
        self.relink("..data", name)

    def snapshot_as_link(self):
        hidden = self.mount / "..hidden"
        self.snapshot.rename(hidden)
        (self.mount / "..2026_link").symlink_to(hidden)
        self.relink("..data", "..2026_link")

    def snapshot_file_as_link(self, target):
        (self.snapshot / NAME).unlink()
        (self.snapshot / NAME).symlink_to(target)


if __name__ == "__main__":
    unittest.main()
