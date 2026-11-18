"""Credential-free checks for the asset script, including fail-closed paths."""

import hashlib
import io
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("temporal-test-server.sh")
NAME = "temporal-test-server_1.40.0_linux_amd64"
PIN = "524465a96274831e004326f6e2f2a5f3ef9aaed72c2069cf16c1d64b8b1ec3ca"


class TemporalAssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.archive = self.root / "fixture.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            for name in [f"{NAME}/temporal-test-server", "../must-not-extract"]:
                contents = b"synthetic test binary\n"
                member = tarfile.TarInfo(name)
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
        # Use a synthetic archive/digest to exercise the actual verification and
        # extraction code without downloading or executing a vendor binary.
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.script = self.root / "script.sh"
        self.script.write_text(SCRIPT.read_text().replace(PIN, digest))
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.cached = self.cache / f"{NAME}.tar.gz"
        self.bin = self.root / "bin"
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.env = dict(os.environ, PATH=f"{self.tools}:{os.environ['PATH']}")
        self.env.update(FIXTURE=str(self.archive), CALLS=str(self.root / "calls"))
        self.tool(
            "timeout", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nshift\nexec "$@"\n'
        )
        self.tool(
            "curl",
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
            'while [ "$1" != "--output" ]; do shift; done\ncp "$FIXTURE" "$2"\n',
        )

    def tool(self, name, source):
        path = self.tools / name
        path.write_text(source)
        path.chmod(0o755)

    def run_script(self, mode):
        return subprocess.run(
            ["bash", str(self.script), mode, str(self.cache), str(self.bin)],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cold_fetch_is_verified_and_transport_is_bounded(self):
        result = self.run_script("fetch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cached.read_bytes(), self.archive.read_bytes())
        calls = (self.root / "calls").read_text()
        for flag in ["180s curl", "--max-time 30", "--retry 3", "--retry-max-time 120"]:
            self.assertIn(flag, calls)
        self.assertIn("github.com/temporalio/sdk-java/releases/download/v1.40.0", calls)

    def test_warm_fetch_checks_digest_without_network(self):
        self.cached.write_bytes(self.archive.read_bytes())
        result = self.run_script("fetch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cache hit", result.stdout)
        self.assertFalse((self.root / "calls").exists())

    def test_corrupt_cached_archive_fails_without_network(self):
        self.cached.write_bytes(b"corrupt")
        self.assertNotEqual(self.run_script("fetch").returncode, 0)
        self.assertFalse((self.root / "calls").exists())

    def test_transfer_failure_does_not_publish_partial_archive(self):
        self.tool("curl", "#!/bin/sh\nexit 22\n")
        self.assertNotEqual(self.run_script("fetch").returncode, 0)
        self.assertFalse(self.cached.exists())
        self.assertFalse(pathlib.Path(f"{self.cached}.partial").exists())

    def test_wrong_download_digest_is_not_cached(self):
        self.archive.write_bytes(b"wrong asset")
        self.assertNotEqual(self.run_script("fetch").returncode, 0)
        self.assertFalse(self.cached.exists())
        self.assertFalse(pathlib.Path(f"{self.cached}.partial").exists())

    def test_install_only_extracts_verified_expected_file(self):
        self.cached.write_bytes(self.archive.read_bytes())
        result = self.run_script("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        binary = self.bin / "temporal-test-server"
        self.assertEqual(binary.read_bytes(), b"synthetic test binary\n")
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertFalse((self.root / "must-not-extract").exists())
        self.assertFalse((self.root / "calls").exists())

    def test_missing_or_corrupt_artifact_never_falls_back_to_download(self):
        self.assertNotEqual(self.run_script("install").returncode, 0)
        self.cached.write_bytes(b"corrupt")
        self.assertNotEqual(self.run_script("install").returncode, 0)
        self.assertFalse(self.bin.exists())
        self.assertFalse((self.root / "calls").exists())


if __name__ == "__main__":
    unittest.main()
