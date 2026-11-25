"""Cloud-only policy regressions; run with python3 -I .github/merge-queue/test_cloud_preflight.py."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(".github/merge-queue/cloud-preflight.sh")


class CloudPreflightTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.worker = Path(tmp.name)
        self.repo = self.worker / "candidate"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.whitespace", "trailing-space")
        self.git("config", "user.name", "Test Worker")
        self.git("config", "user.email", "worker@example.invalid")
        (self.repo / SCRIPT).parent.mkdir(parents=True)
        (self.repo / SCRIPT).write_bytes((ROOT / SCRIPT).read_bytes())
        (self.repo / "CONTRIBUTING.md").write_text("### Cloud worker preflight\n")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.preflight = self.worker / "preflight.sh"
        self.preflight.write_text(self.git("show", f"{self.base}:{SCRIPT}").stdout)

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=True
        )

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def run_preflight(self, **env):
        return subprocess.run(
            ["bash", str(self.preflight)],
            cwd=self.repo,
            env={**os.environ, "BASE_OID": self.base, "LC_ALL": "C", **env},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_candidate_section_still_runs_protected_check(self):
        (self.repo / "CONTRIBUTING.md").unlink()
        (self.repo / SCRIPT).unlink()
        (self.repo / "notes.txt").write_text("new line with trailing whitespace \n")
        self.commit()
        proc = self.run_preflight()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("notes.txt:1: trailing whitespace", proc.stdout + proc.stderr)

    def test_untracked_files_are_not_a_clean_checkout(self):
        self.git("config", "status.showUntrackedFiles", "no")
        (self.repo / "untracked.txt").touch()
        proc = self.run_preflight()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("clean candidate checkout", proc.stderr)

    def test_failed_status_with_no_stdout_is_not_clean(self):
        wrapper = self.worker / "git"
        wrapper.write_text('#!/bin/sh\n[ "$1" = status ] && exit 73\nexit 99\n')
        wrapper.chmod(0o700)
        proc = self.run_preflight(PATH=f"{self.worker}:{os.environ['PATH']}")
        self.assertEqual(proc.returncode, 73)


class PushConcurrencyTests(unittest.TestCase):
    def test_shared_sha_on_distinct_refs_uses_distinct_workflow_run_ids(self):
        # Assert the actual Actions expression: on push neither the PR nor
        # merge-group payload is present, so only the run ID can select a slot.
        for name in (
            "api-contracts",
            "agentcc-gateway-ci",
            "fi-collector-ci",
            "update-postgres-probe-ci",
        ):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
                group = workflow.split("\n  group: >-\n", 1)[1].split("\n  cancel-in-progress:", 1)[0]
                self.assertEqual(
                    " ".join(group.split()),
                    name + "-${{ github.event_name }}-${{ "
                    "github.event_name == 'pull_request' && github.run_attempt == '1' && "
                    "format('pr-{0}', github.event.pull_request.number) || "
                    + ("github.event.merge_group.head_sha || " if name == "fi-collector-ci" else "")
                    + "format('run-{0}', github.run_id) }}",
                )


if __name__ == "__main__":
    unittest.main()
