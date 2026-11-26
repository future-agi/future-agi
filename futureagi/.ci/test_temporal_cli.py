"""Credential-free checks for the pinned Temporal CLI and how CI caches it."""

import hashlib
import io
import os
import pathlib
import re
import subprocess
import tarfile
import tempfile
import tomllib
import unittest

SCRIPT = pathlib.Path(__file__).with_name("temporal-cli.sh")
ROOT = SCRIPT.parents[2]
WORKFLOW = ROOT / ".github/workflows/backend-ci.yml"
NAME = "temporal_cli_1.10.1_linux_amd64"
PIN = "4c1500e2ce2d4de136e5fe8852d9b7f82eb62f19b6b383b3e0c040519a9d20b1"
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}(?: # v[\d.]+)?$")


class TemporalCliScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.archive = self.root / "fixture.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            for name in ["temporal", "../must-not-extract", "LICENSE"]:
                contents = b"synthetic cli binary\n"
                member = tarfile.TarInfo(name)
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
        # A synthetic archive and digest exercise the real verification and
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

    def test_digest_prints_the_pin_the_cache_is_keyed_by(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "digest"], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout, f"{PIN}\n")

    def test_cache_key_identifies_the_versioned_archive_and_digest(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "cache-key"], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.strip(), f"{NAME}-sha256-{PIN}")

    def test_cache_miss_downloads_the_github_release_and_verifies_it(self):
        result = self.run_script("fetch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cache miss", result.stdout)
        self.assertEqual(self.cached.read_bytes(), self.archive.read_bytes())
        calls = (self.root / "calls").read_text()
        for flag in ["180s curl", "--max-time 60", "--retry 3", "--retry-max-time 120"]:
            self.assertIn(flag, calls)
        self.assertIn(
            f"https://github.com/temporalio/cli/releases/download/v1.10.1/{NAME}.tar.gz",
            calls,
        )
        self.assertNotIn("temporal.download", calls)

    def test_cache_hit_is_verified_without_network(self):
        self.cached.write_bytes(self.archive.read_bytes())
        result = self.run_script("fetch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cache hit", result.stdout)
        self.assertIn(": OK", result.stdout)
        self.assertFalse((self.root / "calls").exists())

    def test_corrupt_cache_hit_fails_without_network(self):
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

    def test_install_only_extracts_the_verified_binary(self):
        self.cached.write_bytes(self.archive.read_bytes())
        result = self.run_script("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        binary = self.bin / "temporal"
        self.assertEqual(binary.read_bytes(), b"synthetic cli binary\n")
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), ["temporal"])
        self.assertFalse((self.root / "must-not-extract").exists())
        self.assertFalse((self.root / "calls").exists())

    def test_cache_hit_with_missing_archive_fails_without_network(self):
        # A cache action can report a hit while restoring no archive. The
        # consumer must fail rather than silently downloading a replacement.
        self.assertNotEqual(self.run_script("install").returncode, 0)
        self.cached.write_bytes(b"corrupt")
        self.assertNotEqual(self.run_script("install").returncode, 0)
        self.assertFalse(self.bin.exists())
        self.assertFalse((self.root / "calls").exists())

    def test_the_header_names_the_locked_temporalio(self):
        lock = tomllib.loads((ROOT / "futureagi/uv.lock").read_text(encoding="utf-8"))
        (locked,) = [p["version"] for p in lock["package"] if p["name"] == "temporalio"]
        pinned = re.search(
            r"pinned for temporalio==(\S+) \(uv\.lock\)", SCRIPT.read_text()
        )
        self.assertIsNotNone(pinned)
        self.assertEqual(pinned.group(1), locked)


def _job(workflow: str, name: str) -> str:
    start = workflow.index(f"\n  {name}:\n")
    end = re.compile(r"\n  [a-z0-9-]+:\n").search(workflow, start + 1)
    return workflow[start : end.start() if end else len(workflow)]


class MixedWorkersJobTest(unittest.TestCase):
    """The required mixed-version job reaches no download proxy on a cache hit."""

    def setUp(self):
        self.job = _job(WORKFLOW.read_text(encoding="utf-8"), "eval-task-mixed-workers")

    def test_the_cli_comes_from_the_digest_keyed_cache_or_the_script(self):
        self.assertNotIn("temporal.download", self.job)
        self.assertNotRegex(self.job, r"\bcurl\b")
        self.assertIn(
            'run: echo "key=$(bash .ci/temporal-cli.sh cache-key)" >> "$GITHUB_OUTPUT"',
            self.job,
        )
        key = "key: ${{ steps.temporal-cli.outputs.key }}"
        self.assertEqual(self.job.count(key), 1)
        order = [
            "id: temporal-cli\n",
            "uses: actions/cache/restore@",
            'bash .ci/temporal-cli.sh fetch "$RUNNER_TEMP/temporal-cli-archive"',
            'bash .ci/temporal-cli.sh install "$RUNNER_TEMP/temporal-cli-archive"',
            "uses: actions/cache/save@",
            "uv run pytest tfc/temporal/eval_tasks/tests/test_mixed_creation_workers.py",
        ]
        positions = [self.job.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))

    def _step(self, marker: str) -> str:
        at = self.job.index(marker)
        start = self.job.rindex("\n      - ", 0, at)
        end = self.job.find("\n      - ", at)
        return self.job[start : end if end != -1 else len(self.job)]

    def test_only_a_miss_saves_and_it_saves_the_restored_key(self):
        restore = self._step("uses: actions/cache/restore@")
        save = self._step("uses: actions/cache/save@")
        fetch = self._step("bash .ci/temporal-cli.sh fetch")
        install = self._step("bash .ci/temporal-cli.sh install")
        self.assertIn("id: temporal-cli-cache\n", restore)
        self.assertIn("if: steps.temporal-cli-cache.outputs.cache-hit != 'true'", fetch)
        self.assertIn("if: steps.temporal-cli-cache.outputs.cache-hit != 'true'", save)
        self.assertNotIn("if: steps.temporal-cli-cache.outputs.cache-hit", install)
        self.assertIn(
            "key: ${{ steps.temporal-cli-cache.outputs.cache-primary-key }}", save
        )
        for step in (restore, save):
            self.assertIn("path: ${{ runner.temp }}/temporal-cli-archive\n", step)

    def test_every_action_the_job_takes_from_outside_is_pinned_by_commit(self):
        uses = re.findall(r"uses: (\S+(?: # \S+)?)", self.job)
        external = [u for u in uses if not u.startswith("./")]
        self.assertTrue(external)
        for action in external:
            with self.subTest(action=action):
                self.assertRegex(action, PINNED_ACTION)


if __name__ == "__main__":
    unittest.main()
