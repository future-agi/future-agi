"""The comparison base of the update-safety gate, per GitHub event (stdlib only)."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/update-safety-base.sh"
SHA = "1" * 40
ZERO = "0" * 40


def resolve(event, **env):
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin", "EVENT_NAME": event, **env},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30,
    )
    return result.returncode, result.stdout.strip()


class UpdateSafetyBaseTest(unittest.TestCase):
    def test_each_event_uses_its_own_base_commit(self):
        self.assertEqual(resolve("pull_request", PR_BASE=SHA), (0, SHA))
        self.assertEqual(resolve("push", PUSH_BEFORE=SHA), (0, SHA))
        self.assertEqual(resolve("merge_group", MERGE_GROUP_BASE=SHA), (0, SHA))

    def test_an_event_ignores_the_other_events_values(self):
        # A merge group carries no pull request and no "before".
        self.assertEqual(resolve("merge_group", PR_BASE=SHA, PUSH_BEFORE=SHA), (0, "origin/main"))
        self.assertEqual(resolve("schedule", PR_BASE=SHA, PUSH_BEFORE=SHA, MERGE_GROUP_BASE=SHA),
                         (0, "origin/main"))

    def test_runs_without_a_change_compare_with_the_released_line(self):
        # A scheduled run checks out main. origin/dev made it fail on a release
        # fix that only main carried ("existing migration changed/deleted").
        for event in ("schedule", "workflow_dispatch"):
            self.assertEqual(resolve(event), (0, "origin/main"), event)

    def test_a_new_branch_push_has_no_previous_commit(self):
        self.assertEqual(resolve("push", PUSH_BEFORE=ZERO), (0, "origin/main"))
        self.assertEqual(resolve("push"), (0, "origin/main"))

    def test_the_event_name_is_required(self):
        result = subprocess.run(["bash", str(SCRIPT)], env={"PATH": "/usr/bin:/bin"},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertNotEqual(result.returncode, 0)

    def test_both_workflows_resolve_the_base_through_the_script(self):
        # One definition: a second copy in a workflow is how the two drifted.
        for workflow in ("update-ci.yml", "backend-ci.yml"):
            text = (ROOT / ".github/workflows" / workflow).read_text()
            self.assertIn("scripts/update-safety-base.sh", text, workflow)
            self.assertNotIn("DEFAULT_BASE", text, workflow)
            for name in ("EVENT_NAME: ${{ github.event_name }}",
                         "PR_BASE: ${{ github.event.pull_request.base.sha }}",
                         "PUSH_BEFORE: ${{ github.event.before }}",
                         "MERGE_GROUP_BASE: ${{ github.event.merge_group.base_sha }}"):
                self.assertIn(name, text, workflow)


if __name__ == "__main__":
    unittest.main()
