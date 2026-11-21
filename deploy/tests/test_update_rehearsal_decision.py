"""When Update CI runs its two Real rehearsals (stdlib only)."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/update-rehearsal-decision.sh"
WORKFLOW = ROOT / ".github/workflows/update-ci.yml"
QUIET = {"LABELLED": "false", "TOOLING_CHANGED": "false", "MIGRATIONS_CHANGED": "false"}


def decide(event, **env):
    result = subprocess.run(
        ["bash", str(SCRIPT)], env={"PATH": "/usr/bin:/bin", "EVENT_NAME": event, **env},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30,
    )
    if result.returncode != 0:
        return result.returncode
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


BOTH = {"upgrade": "true", "abc": "true"}
NEITHER = {"upgrade": "false", "abc": "false"}


class RehearsalDecisionTest(unittest.TestCase):
    def test_scheduled_and_dispatched_runs_always_rehearse(self):
        # The nightly is the gate that keeps these rehearsals mandatory.
        self.assertEqual(decide("schedule"), BOTH)
        self.assertEqual(decide("workflow_dispatch"), BOTH)

    def test_a_push_and_a_merge_group_never_rehearse(self):
        # Three hours per job would stall a merge queue.
        loud = {"LABELLED": "true", "TOOLING_CHANGED": "true", "MIGRATIONS_CHANGED": "true", "BASE_REF": "main"}
        self.assertEqual(decide("push", **loud), NEITHER)
        self.assertEqual(decide("merge_group", **loud), NEITHER)

    def test_an_ordinary_pull_request_does_not_rehearse(self):
        self.assertEqual(decide("pull_request", BASE_REF="dev", **QUIET), NEITHER)
        self.assertEqual(decide("pull_request", BASE_REF="feat/stacked", **QUIET), NEITHER)

    def test_a_release_a_label_or_a_tooling_change_runs_both(self):
        self.assertEqual(decide("pull_request", BASE_REF="main", **QUIET), BOTH)
        self.assertEqual(decide("pull_request", BASE_REF="dev", **{**QUIET, "LABELLED": "true"}), BOTH)
        self.assertEqual(decide("pull_request", BASE_REF="dev", **{**QUIET, "TOOLING_CHANGED": "true"}), BOTH)

    def test_a_migration_runs_the_upgrade_from_the_latest_release_only(self):
        self.assertEqual(decide("pull_request", BASE_REF="dev", **{**QUIET, "MIGRATIONS_CHANGED": "true"}),
                         {"upgrade": "true", "abc": "false"})

    def test_a_pull_request_with_an_unknown_input_fails_instead_of_skipping(self):
        # The job then fails, and the workflow runs both rehearsals.
        for name in QUIET:
            for value in ("", "yes"):
                self.assertEqual(decide("pull_request", BASE_REF="dev", **{**QUIET, name: value}), 1, name)
        result = subprocess.run(["bash", str(SCRIPT)], env={"PATH": "/usr/bin:/bin"},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertNotEqual(result.returncode, 0)


class RehearsalWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.changes = self.text.split("\n  changes:\n", 1)[1].split("\n  safety:\n", 1)[0]

    def test_both_real_jobs_follow_the_decision_and_fail_open(self):
        fail_open = "(needs.changes.result != 'success' && github.event_name != 'push' && github.event_name != 'merge_group')"
        for job, output in (("automatic-upgrade", "upgrade"), ("automatic-a-b-c", "abc")):
            body = self.text.split("\n  " + job + ":\n", 1)[1]
            condition = next(line.strip() for line in body.splitlines() if line.strip().startswith("if: "))
            self.assertEqual(
                condition,
                "if: ${{ !cancelled() && (needs.changes.outputs." + output + " == 'true' || " + fail_open + ") }}",
                job,
            )
            self.assertIn("    needs: changes\n", body.split("    steps:", 1)[0])
        self.assertIn("      upgrade: ${{ steps.decide.outputs.upgrade }}\n", self.changes)
        self.assertIn("      abc: ${{ steps.decide.outputs.abc }}\n", self.changes)
        self.assertIn('run: bash scripts/update-rehearsal-decision.sh >> "$GITHUB_OUTPUT"', self.changes)

    def test_the_nightly_and_a_manual_run_still_exist(self):
        triggers = self.text.split("\npermissions:", 1)[0]
        self.assertIn("  schedule:\n", triggers)
        self.assertIn("  workflow_dispatch:\n", triggers)
        # The cheap jobs still report on a merge group.
        self.assertIn("  merge_group:\n", triggers)

    def test_the_label_is_read_live_and_matched_exactly(self):
        self.assertIn("""--jq 'any(.labels[]; .name == "update-rehearsal")'""", self.changes)
        self.assertIn("LABELLED: ${{ steps.labels.outputs.rehearsal == 'true' }}", self.changes)

    def test_documents_and_json_registers_under_deploy_update_do_not_trigger(self):
        code = self.changes.split("      - id: update-code\n", 1)[1].split("      - id: labels\n", 1)[0]
        self.assertIn("predicate-quantifier: every", code)
        for pattern in ("'deploy/update/**'", "'!**/*.md'", "'!**/*.json'"):
            self.assertIn("              - " + pattern + "\n", code)
        tooling = self.changes.split("            tooling:\n", 1)[1].split("            migrations:\n", 1)[0]
        listed = [line.strip()[3:-1] for line in tooling.splitlines() if line.strip().startswith("- '")]
        # No whole-source glob: an ordinary backend or docs change must not run three-hour jobs.
        for pattern in listed:
            self.assertFalse(pattern.startswith("**"), pattern)
            self.assertNotIn(pattern, ("deploy/**", "futureagi/**", "scripts/**", "bin/**"))
        for required in ("bin/update", "deploy/tests/update_e2e/**", "docker-compose*.yml",
                         "scripts/update-rehearsal-decision.sh", ".github/workflows/update-ci.yml"):
            self.assertIn(required, listed)
        self.assertIn("            migrations:\n              - 'futureagi/**/migrations/**'\n", self.changes)


if __name__ == "__main__":
    unittest.main()
