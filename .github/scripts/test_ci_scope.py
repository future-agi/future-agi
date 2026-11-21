"""Exercise complete Git object diffs and the immutable policy loader offline."""

import json
import os
import runpy
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
scope = runpy.run_path(str(SCRIPTS / "ci_scope.py"))
plan = scope["plan"]


def bootstrap(workflow):
    text = (SCRIPTS.parent / "workflows" / workflow).read_text()
    step = text.split("        # Bootstrap is workflow policy;", 1)[1].split(
        "\n      - ", 1
    )[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1]).strip()


class CandidateScope(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        original = os.getcwd()
        self.addCleanup(os.chdir, original)
        os.chdir(directory.name)
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("BRANCH_NAMING_CONVENTION.md")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], text=True).strip()

    def write(self, path, data="text"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def group(self, mode="qualified"):
        head = self.git("rev-parse", "HEAD")
        return plan("merge_group", head, self.base, head, mode)

    def test_group_is_union_not_last_parent_and_does_not_truncate_at_3000(self):
        for i in range(3001):
            self.write("new-root/file-" + str(i))
        self.write("futureagi/code-executor/Dockerfile.base")
        self.commit()
        self.write(".git-blame-ignore-revs")
        self.commit()
        result = self.group()
        self.assertEqual(result["path_count"], 3003)
        for key in (*scope["SUITES"], "executor_base", "update_safety"):
            self.assertTrue(result["flags"][key], key)

    def test_rename_deletion_newline_and_shared_inputs(self):
        self.write("delete\nthis")
        self.write("unsafe\nname")
        self.base = self.commit()
        os.rename("unsafe\nname", ".git-blame-ignore-revs")
        os.remove("delete\nthis")
        head = self.commit()
        self.assertEqual(
            scope["changes"](self.base, head),
            {b"delete\nthis", b"unsafe\nname", b".git-blame-ignore-revs"},
        )
        for path in [
            "INSTALLATION.md",
            ".github/scripts/ci_scope.py",
            ".github/scripts/resolve-sdk-test-ref.py",
            ".github/scripts/test_resolve_sdk_test_ref.py",
            "api_contracts/new.json",
            "futureagi/tfc/temporal/test.py",
            "futureagi/app/models/agent_definition.py",
            "futureagi/simulation/project.py",
            "docs/rebuild-catalog",
            "new/lock",
        ]:
            self.base = self.git("rev-parse", "HEAD")
            self.write(path)
            self.commit()
            flags = self.group()["flags"]
            self.assertTrue(
                all(flags[k] for k in (*scope["SUITES"], "update_safety")), path
            )

    def test_report_only_and_group_labels(self):
        self.write(".git-blame-ignore-revs")
        self.commit()
        self.assertTrue(self.group("full")["flags"]["metrics"])
        flags = self.group()["flags"]
        self.assertFalse(flags["metrics"])
        self.assertFalse(flags["executor_base"])
        self.assertTrue(flags["update_safety"])
        self.assertTrue(
            flags["any"]
        )  # groups cannot prove the absence of a Standalone label
        for event in ("push", "schedule", "workflow_dispatch"):
            head = self.git("rev-parse", "HEAD")
            flags = plan(event, head, head, head, "qualified")["flags"]
            self.assertTrue(all(flags[k] for k in scope["SUITES"]))
            self.assertFalse(flags["executor_base"])

    def test_event_identities_required_but_unknown_impact_widens(self):
        self.write("x")
        head = self.commit()
        for arguments in [
            (self.base, self.base, head),
            (head, "", head),
            (head, self.base, ""),
            (head, "a" * 40, head),
            (head, self.base, self.base),
        ]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                plan("merge_group", *arguments, "qualified")
        with patch.dict(
            plan.__globals__,
            changes=lambda *unused: (_ for _ in ()).throw(ValueError("diff")),
        ):
            self.assertTrue(all(self.group()["flags"].values()))

    def test_diverged_pr_selects_head_diff_but_records_actual_merge_tree(self):
        self.git("checkout", "-qb", "work")
        self.write(".git-blame-ignore-revs")
        head = self.commit()
        self.git("checkout", "-q", "dev")
        self.write("new-root/input")
        base = self.commit()
        self.git("merge", "-q", "--no-ff", "work", "-m", "event merge")
        tested = self.git("rev-parse", "HEAD")
        result = plan("pull_request", tested, base, head, "qualified")
        self.assertEqual(
            (self.base, head, base, tested),
            tuple(
                result[k]
                for k in ("diff_base", "diff_head", "policy_base", "tested_commit")
            ),
        )
        self.assertFalse(result["flags"]["backend"])
        with self.assertRaises(ValueError):
            plan("pull_request", tested, self.base, head, "qualified")

    def test_retarget_with_unchanged_head_includes_path_renamed_by_new_base(self):
        old = "BRANCH_NAMING_CONVENTION.md"
        self.git("checkout", "-qb", "work")
        self.write(old, "edited on head")
        head = self.commit()
        self.git("checkout", "-q", "dev")
        self.git("merge", "-q", "--no-ff", "work", "-m", "first base")
        tested = self.git("rev-parse", "HEAD")
        first = plan("pull_request", tested, self.base, head, "qualified")
        self.assertFalse(first["proposed"])

        self.git("checkout", "-qb", "retarget", self.base)
        Path(old).rename("Dockerfile")
        base = self.commit()
        self.git("merge", "-q", "--no-ff", "work", "-m", "new base")
        tested = self.git("rev-parse", "HEAD")
        self.assertEqual(scope["changes"](base, tested), {b"Dockerfile"})
        result = plan("pull_request", tested, base, head, "qualified")
        self.assertEqual(result["path_count"], 2)
        self.assertTrue(all(result["flags"][k] for k in (*scope["SUITES"], "built")))
        for missing in ((self.base, head), (base, tested)):

            def diff(a, b, missing=missing):
                if (a, b) == missing:
                    raise ValueError("unavailable")
                return {old.encode()}

            with patch.dict(plan.__globals__, changes=diff):
                flags = plan("pull_request", tested, base, head, "qualified")["flags"]
                self.assertTrue(all(flags.values()), missing)

    def test_retargets_replan_and_metadata_edits_never_issue_protected_names(self):
        metadata = "github.event.action == 'edited' && !github.event.changes.base && (github.event.changes.body || github.event.changes.title)"
        for workflow, check in (
            ("backend-ci", "Backend"), ("e2e-ci", "E2E"), ("update-ci", None)
        ):
            text = (SCRIPTS.parent / "workflows" / (workflow + ".yml")).read_text()
            self.assertIn("  pull_request:\n    types: [opened, synchronize, reopened, edited]", text)
            self.assertIn(metadata + " && github.run_id || 'candidate'", text)
            self.assertIn("  changes:\n    if: ${{ !(" + metadata + ") }}", text)
            if check:
                self.assertIn(metadata + f" && '{check} metadata edit' || '{check} Tests Pass'", text)
                self.assertIn("always() && !(" + metadata + ")", text)

    def test_private_overlay_is_not_selected_from_merge_group_heads(self):
        workflow = (SCRIPTS.parent / "workflows/backend-ci.yml").read_text()
        selector = workflow.split("      - id: overlay\n")[1].split("      - id: shards")[0]
        script = textwrap.dedent(selector.split("        run: |\n")[1])
        for event, head_repo, members, wanted in (
            ("pull_request", "base/repo", [], "true"),
            ("pull_request", "fork/repo", [], "false"),
            ("pull_request", "", [], "false"),
            ("merge_group", "", ["base/repo", "base/repo"], "false"),
            ("merge_group", "", ["base/repo", "fork/repo"], "false"),
            ("push", "", [], "true"),
        ):
            with self.subTest(event=event, members=members, head=head_repo):
                # Both group compositions produce the same GitHub event shape:
                # the head is a synthetic ref, not the provenance of its members.
                payload = {"merge_group": {"head_ref": "refs/heads/gh-readonly-queue/dev/group"}} if members else {}
                self.write("event", json.dumps(payload))
                output = Path("output").resolve()
                output.write_text("")
                subprocess.run(
                    ["bash", "-eu", "-c", script],
                    check=True,
                    env=dict(
                        os.environ,
                        GITHUB_EVENT_NAME=event,
                        GITHUB_REPOSITORY="base/repo",
                        PR_HEAD_REPO=head_repo,
                        GITHUB_EVENT_PATH=str(Path("event").resolve()),
                        GITHUB_OUTPUT=str(output),
                    ),
                )
                self.assertEqual(output.read_text().strip(), "internal=" + wanted)
        changes = workflow.split("  changes:\n")[1].split("\n  update-safety:")[0]
        self.assertNotIn("test_ci_scope.py", changes)
        self.assertIn('.["policy-self-test"].result == "success"', workflow)
        steps = workflow.split("\n      - ")
        token_steps = [s for s in steps if "secrets.EE_REPO_READ_TOKEN" in s or "with: *ee_checkout" in s]
        self.assertEqual(len(token_steps), 3)  # cold producer, shards, independent control
        guards = [next(line.strip() for line in s.splitlines() if line.startswith("        if:"))
                  for s in token_steps]
        self.assertCountEqual(guards, [
            "if: inputs.backend_cold_fanout && env.IS_INTERNAL_RUN == 'true'",
            "if: env.IS_INTERNAL_RUN == 'true'",
            "if: needs.plan.outputs.cold_control != 'success' && env.IS_INTERNAL_RUN == 'true'",
        ])
        for step in token_steps:
            self.assertRegex(step, r"uses: future-agi/future-agi/\.github/actions/checkout-ee@[a-f0-9]{40}\n")
        self.assertIn("IS_INTERNAL_RUN: ${{ needs.changes.outputs.internal }}", workflow)
        self.assertEqual(workflow.count("env: *backend_database_env"), 2)

    def test_loader_uses_base_policy_and_bootstraps_conservatively(self):
        driver = bootstrap("backend-ci.yml")
        for name in ("update-ci.yml", "e2e-ci.yml"):
            self.assertEqual(driver, bootstrap(name))

        def run_loader(base, head):
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "outputs"
                env = dict(
                    os.environ,
                    BASE_SHA=base,
                    HEAD_SHA=head,
                    CI_SCOPE_MODE="qualified",
                    GITHUB_SHA=head,
                    GITHUB_EVENT_NAME="merge_group",
                    GITHUB_OUTPUT=str(output),
                    RUNNER_TEMP=temp,
                )
                subprocess.run(
                    ["bash", "-ec", driver],
                    env=env,
                    check=True,
                    capture_output=True,
                )
                return dict(
                    line.split("=", 1) for line in output.read_text().splitlines()
                )

        self.write(".github/scripts/ci_scope.py", (SCRIPTS / "ci_scope.py").read_text())
        published = self.commit()
        self.assertEqual(
            json.loads(run_loader(self.base, published)["scope"])["version"], 0
        )
        self.write(
            ".github/scripts/ci_scope.py",
            "raise RuntimeError('candidate cannot decide scope')",
        )
        candidate = self.commit()
        result = run_loader(published, candidate)
        self.assertEqual(result["backend"], "true")
        self.assertEqual(json.loads(result["scope"])["policy_base"], published)


if __name__ == "__main__":
    unittest.main()
