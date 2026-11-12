"""Offline controls for the source-mirror downloader (run on the build VM)."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("download-source.sh")
ARCHIVE = b"pinned-archive-fixture\n"
DIGEST = hashlib.sha256(ARCHIVE).hexdigest()
MIRRORS = [
    "https://ffmpeg.org/releases",
    "https://www.ffmpeg.org/releases",
    "https://ffmpeg.mirror.garr.it/releases",
    "https://www.mirrorservice.org/sites/ffmpeg.org/releases",
]
FAKE_CURL = """#!/bin/sh
set -eu
while [ "$#" -gt 1 ]; do
    if [ "$1" = --output ]; then output=$2; shift; fi
    shift
done
printf '%s\\n' "$1" >> "$CALLS"
case "$MODE:$1" in
    first:*|fallback:https://www.ffmpeg.org/*|bad-first:https://www.ffmpeg.org/*)
        printf 'pinned-archive-fixture\\n' > "$output" ;;
    bad-first:*) printf 'wrong-archive\\n' > "$output" ;;
    *) printf 'partial\\n' > "$output"; exit 22 ;;
esac
"""


class DownloadSourceTests(unittest.TestCase):
    def run_download(self, mode, digest=DIGEST):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curl = root / "curl"
            curl.write_text(FAKE_CURL)
            curl.chmod(0o755)
            calls = root / "calls"
            destination = root / "archive.tar.xz"
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       CALLS=str(calls), MODE=mode)
            result = subprocess.run(["sh", str(SCRIPT), "7.1.5", digest, str(destination)],
                                    env=env, capture_output=True, timeout=10)
            return (result.returncode, destination.read_bytes() if destination.exists() else None,
                    calls.read_text().splitlines() if calls.exists() else [],
                    list(root.glob("archive.tar.xz.*")))

    def test_first_verified_mirror_stops_the_search(self):
        rc, body, calls, partials = self.run_download("first")
        self.assertEqual((rc, body, calls, partials),
                         (0, ARCHIVE, [MIRRORS[0] + "/ffmpeg-7.1.5.tar.xz"], []))

    def test_failed_download_tries_next_mirror(self):
        rc, body, calls, partials = self.run_download("fallback")
        self.assertEqual((rc, body, partials), (0, ARCHIVE, []))
        self.assertEqual(calls, [url + "/ffmpeg-7.1.5.tar.xz" for url in MIRRORS[:2]])

    def test_checksum_mismatch_also_tries_next_mirror(self):
        rc, body, calls, partials = self.run_download("bad-first")
        self.assertEqual((rc, body, partials), (0, ARCHIVE, []))
        self.assertEqual(calls, [url + "/ffmpeg-7.1.5.tar.xz" for url in MIRRORS[:2]])

    def test_all_bad_checksums_fail_closed(self):
        rc, body, calls, partials = self.run_download("first", "0" * 64)
        self.assertEqual((rc, body, partials), (1, None, []))
        self.assertEqual(calls, [url + "/ffmpeg-7.1.5.tar.xz" for url in MIRRORS])

    def test_all_failed_downloads_fail_closed(self):
        rc, body, calls, partials = self.run_download("failed")
        self.assertEqual((rc, body, partials), (1, None, []))
        self.assertEqual(calls, [url + "/ffmpeg-7.1.5.tar.xz" for url in MIRRORS])

    def test_invalid_checksum_never_downloads(self):
        self.assertEqual(self.run_download("first", "not-a-digest"), (2, None, [], []))


if __name__ == "__main__":
    unittest.main()
