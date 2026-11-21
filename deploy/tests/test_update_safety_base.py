"""The comparison base of the update-safety gate, per GitHub event (stdlib only)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/update-safety-base.sh"
SHA = "1" * 40
ZERO = "0" * 40
GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def resolve(event, cwd=None, **env):
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**GIT_ENV, "EVENT_NAME": event, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode, result.stdout.strip()


class Repo:
    """A throwaway repository: commits, release tags and where origin/main points."""

    def __init__(self, path):
        self.path = path
        self.git("init", "-q", "-b", "work")

    def git(self, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=GIT_ENV,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return result.stdout.strip()

    def commit(self, tag=None):
        self.git("commit", "-q", "--allow-empty", "-m", "c")
        if tag:
            self.git("tag", tag)
        return self.git("rev-parse", "HEAD")

    def origin_main(self, sha):
        self.git("update-ref", "refs/remotes/origin/main", sha)


class EventBaseTest(unittest.TestCase):
    def test_each_event_uses_its_own_base_commit(self):
        self.assertEqual(resolve("pull_request", PR_BASE=SHA), (0, SHA))
        self.assertEqual(resolve("push", PUSH_BEFORE=SHA), (0, SHA))
        self.assertEqual(resolve("merge_group", MERGE_GROUP_BASE=SHA), (0, SHA))

    def test_the_event_name_is_required(self):
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env=GIT_ENV,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_both_workflows_resolve_the_base_through_the_script(self):
        # One definition: a second copy in a workflow is how the two drifted.
        for workflow in ("update-ci.yml", "backend-ci.yml"):
            text = (ROOT / ".github/workflows" / workflow).read_text()
            self.assertIn("scripts/update-safety-base.sh", text, workflow)
            self.assertNotIn("DEFAULT_BASE", text, workflow)
            for name in (
                "EVENT_NAME: ${{ github.event_name }}",
                "PR_BASE: ${{ github.event.pull_request.base.sha }}",
                "PUSH_BEFORE: ${{ github.event.before }}",
                "MERGE_GROUP_BASE: ${{ github.event.merge_group.base_sha }}",
            ):
                self.assertIn(name, text, workflow)


class ReleasedLineTest(unittest.TestCase):
    """Runs with no change of their own: a schedule, a dispatch, a new branch."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repo(tmp.name)

    def resolve(self, event, **env):
        return resolve(event, cwd=self.repo.path, **env)

    def test_a_scheduled_run_on_the_release_commit_compares_with_the_release_before(
        self,
    ):
        # A scheduled run checks out main. Against origin/main the diff is
        # empty, so the gate passed whatever the release had changed.
        self.repo.commit(tag="v1.0.0")
        self.repo.commit(tag="v1.9.0")
        released = self.repo.commit(tag="v1.10.0")
        self.repo.origin_main(released)
        for event in ("schedule", "workflow_dispatch"):
            self.assertEqual(self.resolve(event), (0, "v1.9.0"), event)

    def test_commits_on_main_after_a_release_compare_with_that_release(self):
        self.repo.commit(tag="v1.0.0")
        self.repo.commit(tag="v1.1.0")
        self.repo.origin_main(self.repo.commit())
        self.assertEqual(self.resolve("schedule"), (0, "v1.1.0"))

    def test_only_release_tags_count(self):
        self.repo.commit(tag="v1.0.0")
        self.repo.commit(tag="0.5.10")
        self.repo.commit(tag="nightly")
        self.repo.origin_main(self.repo.commit(tag="v1.1.0"))
        self.assertEqual(self.resolve("schedule"), (0, "v1.0.0"))

    def test_a_checkout_of_main_with_no_earlier_release_fails(self):
        # No honest base exists; "origin/main" here would be the empty diff again.
        self.repo.origin_main(self.repo.commit(tag="v1.0.0"))
        code, base = self.resolve("schedule")
        self.assertNotEqual(code, 0)
        self.assertEqual(base, "")

    def test_a_branch_ahead_of_main_compares_with_main(self):
        self.repo.origin_main(self.repo.commit(tag="v1.0.0"))
        self.repo.commit()
        self.assertEqual(self.resolve("workflow_dispatch"), (0, "origin/main"))
        # A newly pushed branch has no previous commit.
        self.assertEqual(self.resolve("push", PUSH_BEFORE=ZERO), (0, "origin/main"))
        self.assertEqual(self.resolve("push"), (0, "origin/main"))

    def test_an_event_ignores_the_other_events_values(self):
        # A merge group carries no pull request and no "before".
        self.repo.origin_main(self.repo.commit(tag="v1.0.0"))
        self.repo.commit()
        self.assertEqual(
            self.resolve("merge_group", PR_BASE=SHA, PUSH_BEFORE=SHA),
            (0, "origin/main"),
        )
        self.assertEqual(
            self.resolve(
                "schedule", PR_BASE=SHA, PUSH_BEFORE=SHA, MERGE_GROUP_BASE=SHA
            ),
            (0, "origin/main"),
        )


if __name__ == "__main__":
    unittest.main()
