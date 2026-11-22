"""When Update CI runs its two Real rehearsals (stdlib only)."""
import runpy
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/update-rehearsal-decision.sh"
WORKFLOW = ROOT / ".github/workflows/update-ci.yml"
QUIET = {"LABELLED": "false", "TOOLING_CHANGED": "false", "BUILT_CHANGED": "false"}


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

    def test_a_main_push_and_a_merge_group_never_rehearse(self):
        # Three hours per job would stall a merge queue.
        loud = {"LABELLED": "true", "TOOLING_CHANGED": "true", "BUILT_CHANGED": "true", "BASE_REF": "main"}
        self.assertEqual(decide("push", **loud), NEITHER)
        self.assertEqual(decide("merge_group", **loud), NEITHER)
        self.assertEqual(decide("push", BASE_REF="dev"), BOTH)

    def test_an_ordinary_pull_request_does_not_rehearse(self):
        self.assertEqual(decide("pull_request", BASE_REF="dev", **QUIET), NEITHER)
        self.assertEqual(decide("pull_request", BASE_REF="feat/stacked", **QUIET), NEITHER)

    def test_a_release_a_label_or_a_tooling_change_runs_both(self):
        self.assertEqual(decide("pull_request", BASE_REF="main", **QUIET), BOTH)
        self.assertEqual(decide("pull_request", BASE_REF="dev", **{**QUIET, "LABELLED": "true"}), BOTH)
        self.assertEqual(decide("pull_request", BASE_REF="dev", **{**QUIET, "TOOLING_CHANGED": "true"}), BOTH)

    def test_a_migration_runs_the_upgrade_from_the_latest_release_only(self):
        self.assertEqual(decide("pull_request", BASE_REF="dev", **{**QUIET, "BUILT_CHANGED": "true"}),
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
        fail_open = "(needs.changes.result != 'success' && github.event_name != 'merge_group' && (github.event_name != 'push' || github.ref == 'refs/heads/dev'))"
        for job, output in (("automatic-upgrade", "upgrade"), ("automatic-a-b-c", "abc")):
            body = self.text.split("\n  " + job + ":\n", 1)[1]
            condition = next(line.strip() for line in body.splitlines() if line.strip().startswith("if: "))
            self.assertEqual(
                condition,
                "if: ${{ !cancelled() && !(github.event.action == 'edited' && !github.event.changes.base && (github.event.changes.body || github.event.changes.title)) && (needs.changes.outputs." + output + " == 'true' || " + fail_open + ") }}",
                job,
            )
            self.assertIn("    needs: changes\n", body.split("    steps:", 1)[0])
        self.assertIn("      upgrade: ${{ steps.decide.outputs.upgrade }}\n", self.changes)
        self.assertIn("      abc: ${{ steps.decide.outputs.abc }}\n", self.changes)
        self.assertIn('git show "$POLICY:scripts/update-rehearsal-decision.sh"', self.changes)

    def test_the_nightly_and_a_manual_run_still_exist(self):
        triggers = self.text.split("\npermissions:", 1)[0]
        self.assertIn("  schedule:\n", triggers)
        self.assertIn("  workflow_dispatch:\n", triggers)
        # The cheap jobs still report on a merge group.
        self.assertIn("  merge_group:\n", triggers)

    def test_the_label_is_read_live_and_matched_exactly(self):
        self.assertIn("""--jq 'any(.labels[]; .name == "update-rehearsal")'""", self.changes)
        self.assertIn("LABELLED: ${{ steps.labels.outputs.rehearsal == 'true' }}", self.changes)

    def test_documents_and_static_gate_registers_under_deploy_update_do_not_trigger(self):
        classify = runpy.run_path(str(ROOT / '.github/scripts/ci_scope.py'))['rehearsal_inputs']
        # The registers that only scripts/check_update_safety.py reads: an
        # ordinary pull request edits them to record a review.
        for path in (b'deploy/update/clickhouse-change-reviews.json', b'deploy/update/clickhouse-sources.json',
                     b'deploy/update/env-generators.json', b'deploy/update/README.md', b'futureagi/example.py'):
            self.assertEqual(classify([path]), (False, False))
        # Every other JSON file there is input of the update tool or the
        # qualifier, so a change to one must rehearse.
        for path in (b'deploy/update/release-policy.json', b'deploy/update/high-volume-tables.json',
                     b'deploy/update/manifest.schema.json', b'deploy/update/review.json'):
            self.assertEqual(classify([path]), (True, False), path)
        for path in (b'bin/update', b'deploy/tests/update_e2e/run.py', b'docker-compose.yml',
                     b'scripts/update-rehearsal-decision.sh', b'.github/workflows/update-ci.yml'):
            self.assertEqual(classify([path]), (True, False))

    def test_what_the_images_build_and_boot_triggers_latest_stable_only(self):
        classify = runpy.run_path(str(ROOT / '.github/scripts/ci_scope.py'))['rehearsal_inputs']
        self.assertIn('BUILT_CHANGED: ${{ steps.filter.outputs.built }}', self.changes)
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
        for path in [b'futureagi/app/migrations/002.py', b'futureagi/tfc/settings/test.py', b'futureagi/uv.lock',
                     *(p for p in tracked if p.rsplit(b'/', 1)[-1].lower().startswith(b'dockerfile') or p.endswith(b'entrypoint.sh'))]:
            self.assertTrue(classify([path])[1], path)

if __name__ == "__main__":
    unittest.main()
