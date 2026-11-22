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


class UpdateSafetyBaseTest(unittest.TestCase):
    def test_each_event_uses_its_own_base_commit(self):
        self.assertEqual(resolve("pull_request", PR_BASE=SHA), (0, SHA))
        self.assertEqual(resolve("push", PUSH_BEFORE=SHA), (0, SHA))
        self.assertEqual(resolve("merge_group", MERGE_GROUP_BASE=SHA), (0, SHA))

    def test_an_event_ignores_the_other_events_values(self):
        # A merge group carries no pull request and no "before".
        self.assertNotEqual(resolve("merge_group", PR_BASE=SHA, PUSH_BEFORE=SHA)[0], 0)

    def test_candidate_bases_must_be_immutable_commits(self):
        for event, key in (
            ("pull_request", "PR_BASE"),
            ("merge_group", "MERGE_GROUP_BASE"),
        ):
            for invalid in ("", ZERO, "origin/dev", "-HEAD", SHA[:12]):
                self.assertNotEqual(resolve(event, **{key: invalid})[0], 0)

    def test_the_event_name_is_required(self):
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            check=False,
            timeout=30,
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
    """Runs with no change of their own: a schedule, a dispatch, a new branch.

    In a throwaway repository: the answer depends on where the checkout is,
    so the repository these tests run in must not decide it.
    """

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
        # empty, so the gate passed whatever the release had changed. (And it
        # must not be origin/dev: a release fix that only main carried then
        # reads as "existing migration changed/deleted".)
        self.repo.commit(tag="v1.0.0")
        self.repo.commit(tag="v1.9.0")
        self.repo.origin_main(self.repo.commit(tag="v1.10.0"))
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

    def test_a_preview_build_tag_is_not_a_release(self):
        # The real history at v1.34.0 holds v1.33.1-pr2483.11b8626a0, a PR
        # build that sorts above v1.33.0. Comparing with it would hide what the
        # release changed since v1.33.0.
        self.repo.commit(tag="v1.32.0")
        self.repo.commit(tag="v1.33.0")
        self.repo.commit(tag="v1.33.1-pr2483.11b8626a0")
        self.repo.commit(tag="v1.33.1-rc1")
        self.repo.origin_main(self.repo.commit(tag="v1.34.0"))
        self.assertEqual(self.resolve("schedule"), (0, "v1.33.0"))

    def test_only_preview_tags_before_the_release_fails(self):
        self.repo.commit(tag="v1.0.1-pr1.abc")
        self.repo.origin_main(self.repo.commit(tag="v1.1.0"))
        code, base = self.resolve("schedule")
        self.assertNotEqual(code, 0)
        self.assertEqual(base, "")

    def test_the_real_release_history_gives_the_release_before(self):
        # Replays the repository's own tags when they are present: v1.34.0
        # must compare with v1.33.0 and v1.59.1 with v1.59.0.
        for release, expected in (("v1.34.0", "v1.33.0"), ("v1.59.1", "v1.59.0")):
            sha = subprocess.run(
                ["git", "rev-parse", "-q", "--verify", release + "^{commit}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if not sha:
                self.skipTest("release tags not fetched")
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(
                    ["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), tmp],
                    env=GIT_ENV,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "checkout", "-q", "--detach", sha],
                    cwd=tmp,
                    env=GIT_ENV,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "update-ref", "refs/remotes/origin/main", sha],
                    cwd=tmp,
                    env=GIT_ENV,
                    check=True,
                    capture_output=True,
                )
                self.assertEqual(resolve("schedule", cwd=tmp), (0, expected), release)

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
        # A schedule ignores the values of the other events.
        self.assertEqual(
            self.resolve(
                "schedule", PR_BASE=SHA, PUSH_BEFORE=SHA, MERGE_GROUP_BASE=SHA
            ),
            (0, "origin/main"),
        )


if __name__ == "__main__":
    unittest.main()
