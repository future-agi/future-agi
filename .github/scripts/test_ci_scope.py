"""Exercise complete Git object diffs and the immutable policy loader offline."""

import ast
import json
import os
import re
import runpy
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent.parent
scope = runpy.run_path(str(SCRIPTS / "ci_scope.py"))
plan = scope["plan"]
ACCEPTANCE = "deploy/tests/update_e2e/acceptance.py"


def rehearsal(path):
    """(tooling, built) for one changed path."""
    return scope["rehearsal_inputs"]({path.encode()})


def rehearsal_modules():
    """Every repository file the acceptance driver imports, directly or not."""
    seen = set()
    queue = [ACCEPTANCE]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        for node in ast.walk(ast.parse((ROOT / path).read_text())):
            if isinstance(node, ast.Import):
                found = [(alias.name, []) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level:
                # `from .readiness import x` inside deploy/update/apply.py.
                package = Path(path).parent
                for _ in range(node.level - 1):
                    package = package.parent
                prefix = ".".join(package.parts)
                module = prefix + ("." + node.module if node.module else "")
                found = [(module, [alias.name for alias in node.names])]
            elif isinstance(node, ast.ImportFrom) and node.module:
                found = [(node.module, [alias.name for alias in node.names])]
            else:
                continue
            for module, names in found:
                if module.split(".")[0] not in ("deploy", "scripts"):
                    continue
                base = module.replace(".", "/")
                for candidate in [
                    base + ".py",
                    base + "/__init__.py",
                    *(base + "/" + name + ".py" for name in names),
                ]:
                    if (ROOT / candidate).is_file():
                        queue.append(candidate)
    return sorted(seen)


def workflow_job(workflow, job):
    return re.split(
        r"\n  [a-z0-9-]+:\n", workflow.split(f"\n  {job}:\n", 1)[1], maxsplit=1
    )[0]


def decide(**env):
    """scripts/update-rehearsal-decision.sh, as the changes job runs it."""
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/update-rehearsal-decision.sh")],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


class UpdateCiAdmission(unittest.TestCase):
    """Every pull request starts Update CI; the changes job decides what runs."""

    WORKFLOW = (SCRIPTS.parent / "workflows/update-ci.yml").read_text()

    def triggers(self):
        return self.WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]

    def test_pull_requests_start_the_workflow_on_devs_path_list(self):
        # No wider than dev: a pull request touching none of these files does
        # not start Update CI. Only the host-update API contracts are push-only.
        pull_request = (
            self.triggers().split("  pull_request:\n", 1)[1].split("  push:\n", 1)[0]
        )
        push = self.triggers().split("  push:\n", 1)[1].split("  merge_group:", 1)[0]
        listed = re.findall(r'^      - "([^"]+)"$', pull_request, re.MULTILINE)
        pushed = re.findall(r'^      - "([^"]+)"$', push, re.MULTILINE)
        self.assertEqual(len(listed), 47)
        self.assertEqual(
            [path for path in pushed if path not in listed],
            ["api_contracts/host-update/**"],
        )
        self.assertEqual([path for path in listed if path not in pushed], [])
        self.assertIn("    branches: [dev, main]\n", pull_request)

    def test_the_policy_list_is_the_push_list(self):
        push = self.triggers().split("  push:\n", 1)[1].split("  merge_group:", 1)[0]
        listed = re.findall(r'^      - "([^"]+)"$', push, re.MULTILINE)
        self.assertGreater(len(listed), 40)
        self.assertEqual(list(scope["UPDATE_CI_PATHS"]), listed)

    def test_which_paths_select_the_ordinary_jobs(self):
        relevant = scope["update_ci_relevant"]
        for path in (
            "futureagi/tracer/models.py",
            "bin/update",
            "deploy/update/apply.py",
            "fi-collector/main.go",
            "docker-compose.yml",
            ".github/workflows/update-ci.yml",
        ):
            self.assertTrue(relevant({path.encode()}), path)
        for path in (
            "frontend/src/app.jsx",
            "README.md",
            "docs/page.mdx",
            "e2e/flows/a.spec.ts",
        ):
            self.assertFalse(relevant({path.encode()}), path)

    def test_an_ordinary_pull_request_into_dev_with_no_matching_file_runs_nothing(self):
        # The decision for the Real jobs ...
        quiet = {
            "EVENT_NAME": "pull_request",
            "BASE_REF": "dev",
            "LABELLED": "false",
            "TOOLING_CHANGED": "false",
            "BUILT_CHANGED": "false",
        }
        self.assertEqual(decide(**quiet), {"upgrade": "false", "abc": "false"})
        # ... and the plan for the ordinary jobs, on a real pull request merge.
        with tempfile.TemporaryDirectory() as directory:
            original = os.getcwd()
            os.chdir(directory)
            try:
                git = lambda *a: subprocess.check_output(["git", *a], text=True).strip()
                git("init", "-q", "-b", "dev")
                git("config", "user.name", "fixture")
                git("config", "user.email", "fixture@example.invalid")
                Path("README.md").write_text("a")
                git("add", "-A")
                git("commit", "-qm", "base")
                base = git("rev-parse", "HEAD")
                git("checkout", "-qb", "work")
                Path("frontend").mkdir()
                Path("frontend/app.jsx").write_text("b")
                git("add", "-A")
                git("commit", "-qm", "frontend only")
                head = git("rev-parse", "HEAD")
                git("checkout", "-q", "dev")
                git("merge", "-q", "--no-ff", "work", "-m", "event merge")
                tested = git("rev-parse", "HEAD")
                flags = plan("pull_request", tested, base, head, "")["flags"]
                self.assertFalse(flags["ordinary_update"])
                # A backend file in the same pull request selects them.
                git("checkout", "-q", "work")
                Path("futureagi").mkdir()
                Path("futureagi/x.py").write_text("c")
                git("add", "-A")
                git("commit", "-qm", "backend")
                head = git("rev-parse", "HEAD")
                git("checkout", "-q", "dev")
                git("reset", "-q", "--hard", base)
                git("merge", "-q", "--no-ff", "work", "-m", "event merge")
                tested = git("rev-parse", "HEAD")
                self.assertTrue(
                    plan("pull_request", tested, base, head, "")["flags"][
                        "ordinary_update"
                    ]
                )
            finally:
                os.chdir(original)

    def test_a_labelled_pull_request_or_one_into_main_rehearses_whatever_it_touches(
        self,
    ):
        # Unit-proven only: this repository's own pull requests are neither.
        quiet = {
            "EVENT_NAME": "pull_request",
            "LABELLED": "false",
            "TOOLING_CHANGED": "false",
            "BUILT_CHANGED": "false",
        }
        both = {"upgrade": "true", "abc": "true"}
        self.assertEqual(
            decide(**{**quiet, "BASE_REF": "dev", "LABELLED": "true"}), both
        )
        self.assertEqual(decide(**{**quiet, "BASE_REF": "main"}), both)
        changes = workflow_job(self.WORKFLOW, "changes")
        self.assertIn("""--jq 'any(.labels[]; .name == "update-rehearsal")'""", changes)
        self.assertIn("BASE_REF: ${{ github.base_ref || github.ref_name }}", changes)

    def test_when_the_decision_fails_the_ordinary_jobs_run_and_real_jobs_only_off_pull_requests(
        self,
    ):
        for job, output in (
            ("automatic-upgrade", "upgrade"),
            ("automatic-a-b-c", "abc"),
        ):
            condition = (
                workflow_job(self.WORKFLOW, job)
                .split("    if: ", 1)[1]
                .split("\n", 1)[0]
            )
            self.assertIn(
                f"needs.changes.outputs.{output} == 'true' || (needs.changes.result != 'success'",
                condition,
            )
            # Fails closed on a pull request: a failed decision starts neither
            # ~3 h rehearsal there; push to dev and schedules still run them.
            self.assertIn("github.event_name != 'pull_request'", condition)
        names = re.findall(
            r"^  ([a-z0-9-]+):$", self.WORKFLOW.split("\njobs:\n", 1)[1], re.MULTILINE
        )
        ordinary = [
            n
            for n in names
            if n not in ("changes", "automatic-upgrade", "automatic-a-b-c")
        ]
        self.assertGreaterEqual(len(ordinary), 6)
        for job in ordinary:
            head = workflow_job(self.WORKFLOW, job).split("    steps:", 1)[0]
            self.assertIn("    needs: changes\n", head, job)
            self.assertIn(
                "(needs.changes.result != 'success' || needs.changes.outputs.ordinary != 'false')",
                head,
                job,
            )


class RehearsalInputs(unittest.TestCase):
    """Which changed paths start the two Real update rehearsals."""

    def test_everything_the_rehearsals_import_starts_both(self):
        # Derived, not listed by hand: the driver and every module it imports
        # from scripts/ and deploy/, at any depth. The qualifier and its
        # update_rehearsal_* modules were missing from the list.
        modules = rehearsal_modules()
        for required in (
            ACCEPTANCE,
            # Reached only through relative imports inside deploy/update.
            "deploy/update/readiness.py",
            "scripts/rehearse_update_compatibility.py",
            "scripts/build_update_compatibility_proof.py",
            "scripts/update_rehearsal_qualification.py",
            "scripts/generate_update_manifest.py",
            "deploy/update/manifest.py",
        ):
            self.assertIn(required, modules)
        for path in modules:
            self.assertTrue(rehearsal(path)[0], path)
        for path in ("bin/update", "docker-compose.yml", ".github/scripts/ci_scope.py"):
            self.assertTrue(rehearsal(path)[0], path)

    def test_the_local_actions_of_the_two_jobs_start_both(self):
        workflow = (SCRIPTS.parent / "workflows/update-ci.yml").read_text()
        for job in ("automatic-upgrade", "automatic-a-b-c"):
            body = re.split(
                r"\n  [a-z0-9-]+:\n", workflow.split(f"\n  {job}:\n", 1)[1], maxsplit=1
            )[0]
            actions = re.findall(r"uses: \./(\S+)", body)
            self.assertTrue(actions, job)
            for action in actions:
                self.assertTrue(rehearsal(action + "/action.yml")[0], action)

    def test_the_updaters_frozen_runtime_schemas_start_both(self):
        for path in (
            "api_contracts/host-update/api-response.v1.schema.json",
            "api_contracts/host-update/public-status.v1.schema.json",
        ):
            self.assertTrue((ROOT / path).is_file(), path)
            self.assertEqual(rehearsal(path), (True, False), path)
            # ... and a pull request that changes only one starts Update CI.
            self.assertTrue(scope["update_ci_relevant"]({path.encode()}), path)

    def test_a_setup_action_only_change_is_admitted_and_rehearses(self):
        # The workflow admits every pull request; the policy then selects.
        path = ".github/actions/setup-helm-tools/action.yml"
        self.assertTrue((ROOT / path).is_file())
        self.assertEqual(rehearsal(path), (True, False))
        self.assertTrue(scope["update_ci_relevant"]({path.encode()}))

    def test_only_the_packaged_chart_starts_latest_stable(self):
        # acceptance.py packages deploy/helm/futureagi without hack/, ci/ and
        # dot-files; a change confined to those packages nothing new.
        chart = ROOT / "deploy/helm/futureagi"
        excluded = [
            str(p.relative_to(ROOT))
            for p in chart.rglob("*")
            if p.is_file()
            and (
                p.relative_to(chart).parts[0] in ("hack", "ci")
                or any(part.startswith(".") for part in p.relative_to(chart).parts)
            )
        ]
        self.assertGreater(len(excluded), 5)
        for path in excluded:
            self.assertEqual(rehearsal(path), (False, False), path)
        for path in (
            "deploy/helm/futureagi/Chart.yaml",
            "deploy/helm/futureagi/values.yaml",
        ):
            self.assertEqual(rehearsal(path), (False, True), path)
        # Prose and example values in the chart: packaged, but nothing the
        # job runs changes. Ordinary chart checks still cover them.
        for path in (
            "deploy/helm/futureagi/README.md",
            "deploy/helm/futureagi/UPGRADING.md",
            "deploy/helm/futureagi/LICENSE",
            "deploy/helm/futureagi/examples/README.md",
            "deploy/helm/futureagi/examples/cloud/gke.yaml",
        ):
            self.assertEqual(rehearsal(path), (False, False), path)
        acceptance = (ROOT / ACCEPTANCE).read_text()
        self.assertIn("relative.parts[0] in ('hack','ci')", acceptance)
        self.assertIn("any(p.startswith('.') for p in relative.parts)", acceptance)

    def test_the_runtime_json_list_is_what_the_rehearsal_modules_read(self):
        # Derived: a JSON file under deploy/update that a rehearsal module or
        # bin/update names is runtime input and must be listed; nothing else is.
        sources = "".join(
            (ROOT / path).read_text() for path in [*rehearsal_modules(), "bin/update"]
        )
        registers = sorted(
            str(path.relative_to(ROOT))
            for path in (ROOT / "deploy/update").rglob("*.json")
            if "__pycache__" not in path.parts
        )
        read = {path for path in registers if path.rsplit("/", 1)[-1] in sources}
        listed = {path.decode() for path in scope["RUNTIME_JSON"]}
        self.assertIn("deploy/update/release-policy.json", read)
        self.assertIn("deploy/update/high-volume-tables.json", read)
        self.assertEqual(listed, read)
        static = {path.decode() for path in scope["STATIC_GATE_REGISTERS"]}
        gate = (ROOT / "scripts/check_update_safety.py").read_text()
        # Known readers only: any other JSON file there (a read-only review
        # record, say) is inert and starts no rehearsal, without being listed.
        self.assertLessEqual(static | listed, set(registers))
        self.assertFalse(static & listed)
        for path in registers:
            self.assertEqual(rehearsal(path)[0], path in listed, path)
        for path in static:
            self.assertIn(path.rsplit("/", 1)[-1], gate, path)
        # A new JSON file there, such as a review record, starts nothing.
        for path in ("deploy/update/review.json", "deploy/update/reviews/2026-11.json"):
            self.assertEqual(rehearsal(path), (False, False), path)
        self.assertFalse(rehearsal("deploy/update/README.md")[0])
        self.assertTrue(rehearsal("deploy/update/apply.py")[0])

    def test_what_the_boot_installs_starts_latest_stable_only(self):
        for path in (
            "futureagi/tracer/migrations/0001_initial.py",
            "futureagi/tfc/settings/settings.py",
            "futureagi/uv.lock",
            "deploy/standalone/bin/bootstrap.py",
            "deploy/standalone/nginx.conf",
            "deploy/helm/futureagi/Chart.yaml",
            "futureagi/tfc/management/commands/bootstrap_install.py",
            "futureagi/tracer/services/clickhouse/oss_cdc_install.py",
            "futureagi/tracer/services/clickhouse/oss_native_bootstrap.py",
            "futureagi/tracer/services/clickhouse/oss_cdc_bootstrap.py",
            # bootstrap_install and the Standalone bootstrap install it at boot.
            "futureagi/tracer/services/clickhouse/oss_outbox_cdc.py",
            "futureagi/tracer/services/clickhouse/v2/apply_schema.py",
            "futureagi/tracer/services/clickhouse/v2/apply_schema_rewriter.py",
            "futureagi/tracer/services/clickhouse/v2/schema_topology.py",
            "futureagi/tracer/services/clickhouse/v2/observed_catalog/schema.sql",
            "futureagi/scripts/property_catalog_oss/validate_clickhouse.sql",
        ):
            self.assertTrue((ROOT / path).is_file(), path)
            self.assertEqual(rehearsal(path), (False, True), path)
        schema = ROOT / "futureagi/tracer/services/clickhouse/v2/schema"
        files = [path for path in schema.rglob("*") if path.is_file()]
        self.assertGreater(len(files), 10)
        for path in files:
            self.assertTrue(rehearsal(str(path.relative_to(ROOT)))[1], path)
        # Each image the job builds: its Dockerfile is an input.
        acceptance = (ROOT / ACCEPTANCE).read_text()
        dockerfiles = re.findall(r"'([\w./-]*Dockerfile[\w.]*)'", acceptance)
        self.assertGreaterEqual(len(dockerfiles), 5)
        for path in dockerfiles:
            self.assertTrue(rehearsal(path)[1], path)

    def test_ordinary_source_starts_neither(self):
        # The product source inside the images is left to the nightly run.
        for path in (
            "futureagi/tracer/views/observation_span.py",
            "futureagi/tracer/services/clickhouse/v2/span_reader.py",
            "scripts/check-staged-safety.mjs",
            "frontend/src/app.jsx",
            "README.md",
        ):
            self.assertEqual(rehearsal(path), (False, False), path)


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

    def test_required_workflows_start_only_on_code_events(self):
        # A run for any other activity type (a body or title edit, a label)
        # becomes its workflow's newest suite for the head commit, and GitHub
        # then ignores the older suite's required check, so the PR cannot be
        # enqueued (#4332, #4001). These workflows therefore never start on
        # one, and nothing in them depends on such an event.
        for workflow, job, check in (
            ("backend-ci", "backend-tests", "Backend Tests Pass"),
            ("e2e-ci", "e2e-tests", "E2E Tests Pass"),
            ("python-lint", "python-lint-pass", "Python Lint Pass"),
        ):
            text = (SCRIPTS.parent / "workflows" / (workflow + ".yml")).read_text()
            with self.subTest(workflow=workflow):
                self.assertRegex(
                    text,
                    r"(?m)^  pull_request:\n(?:    #.*\n)*"
                    r"    types: \[opened, synchronize, reopened\]\n",
                )
                for expression in (
                    "github.event.action",
                    "github.event.changes",
                    "github.event.label",
                    "metadata edit",
                ):
                    self.assertNotIn(expression, text)
                block = re.search(
                    rf"(?ms)^  {job}:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)", text
                )[1]
                self.assertRegex(block, rf"(?m)^    name: {check}( #.*)?$")
                self.assertIn("    if: ${{ always() }}\n", block)
                suffix = (
                    "-candidate"
                    if workflow == "python-lint"
                    else "-${{ github.workflow == 'Retarget proof' && github.run_id || 'candidate' }}"
                )
                self.assertRegex(text, rf"(?m)^  group: .*{re.escape(suffix)}$")

    def test_only_retarget_proof_runs_leave_the_candidate_group(self):
        # Pull request and merge-group runs, direct or through the merge
        # queue's call, keep today's "candidate" group, so one run per PR is
        # active and a newer push cancels the older one. Only Retarget proof's
        # calls get a group of their own: they never cancel, and are never
        # cancelled by, a pull request's real runs.
        expression = (
            "github.workflow == 'Retarget proof' && github.run_id || 'candidate'"
        )

        def suffix(workflow_name, run_id):
            # GitHub's && and || return operand values, like Python's.
            return (workflow_name == "Retarget proof" and run_id) or "candidate"

        for name in ("Backend CI", "E2E CI", "Backend merge queue"):
            self.assertEqual(suffix(name, "45079002539"), "candidate", name)
        self.assertEqual(suffix("Retarget proof", "45079002539"), "45079002539")
        for workflow in ("backend-ci", "e2e-ci"):
            text = (SCRIPTS.parent / "workflows" / (workflow + ".yml")).read_text()
            with self.subTest(workflow=workflow):
                self.assertEqual(text.count(expression), 1)
                self.assertIn(
                    "    inputs:\n      job_prefix:\n",
                    text.split("  workflow_call:\n", 1)[1],
                )
                # Empty by default: today's job names hold.
                block = text.split("      job_prefix:\n", 1)[1].split(
                    "\n    outputs:", 1
                )[0]
                self.assertIn('        default: ""', block)
        proof = (SCRIPTS.parent / "workflows/retarget-proof.yml").read_text()
        self.assertIn("name: Retarget proof\n", proof)

    def test_update_ci_metadata_events_start_no_jobs(self):
        # Update CI is not required and still starts on edits and labels:
        # update-rehearsal starts the Real rehearsals. Any other label,
        # e2e-standalone included (it only sets the plan's E2E fields, which
        # no job here reads), and a title or body edit start a run with no jobs.
        edit = "github.event.action == 'edited' && !github.event.changes.base && (github.event.changes.body || github.event.changes.title)"
        metadata = (
            "(("
            + edit
            + ") || (github.event.action == 'labeled' && github.event.label.name != 'update-rehearsal'))"
        )
        text = (SCRIPTS.parent / "workflows/update-ci.yml").read_text()
        self.assertIn(
            "  pull_request:\n    types: [opened, synchronize, reopened, edited, labeled]",
            text,
        )
        self.assertIn(metadata + " && github.run_id || 'candidate'", text)
        self.assertIn("  changes:\n    if: ${{ !" + metadata + " }}", text)
        # No job keeps a narrower guard, so a stray label starts none.
        self.assertNotIn("!(" + edit + ")", text)

    def test_private_overlay_is_not_selected_from_merge_group_heads(self):
        workflow = (SCRIPTS.parent / "workflows/backend-ci.yml").read_text()
        selector = workflow.split("      - id: overlay\n")[1].split(
            "      - id: shards"
        )[0]
        script = textwrap.dedent(selector.split("        run: |\n")[1])
        for event, head_repo, members, wanted, caller in (
            ("pull_request", "base/repo", [], "true", "Backend CI"),
            ("pull_request", "fork/repo", [], "false", "Backend CI"),
            ("pull_request", "", [], "false", "Backend CI"),
            # Retarget proof forwards no secrets: an OSS run, like the queue.
            ("pull_request", "base/repo", [], "false", "Retarget proof"),
            ("pull_request", "fork/repo", [], "false", "Retarget proof"),
            # Unchanged: dev/main pushes, dispatches and schedules are internal.
            ("workflow_dispatch", "", [], "true", "Backend CI"),
            ("schedule", "", [], "true", "Backend CI"),
            (
                "merge_group",
                "",
                ["base/repo", "base/repo"],
                "false",
                "Backend merge queue",
            ),
            (
                "merge_group",
                "",
                ["base/repo", "fork/repo"],
                "false",
                "Backend merge queue",
            ),
            ("push", "", [], "true", "Backend CI"),
        ):
            with self.subTest(
                event=event, members=members, head=head_repo, caller=caller
            ):
                # Both group compositions produce the same GitHub event shape:
                # the head is a synthetic ref, not the provenance of its members.
                payload = (
                    {
                        "merge_group": {
                            "head_ref": "refs/heads/gh-readonly-queue/dev/group"
                        }
                    }
                    if members
                    else {}
                )
                self.write("event", json.dumps(payload))
                output = Path("output").resolve()
                output.write_text("")
                subprocess.run(
                    ["bash", "-eu", "-c", script],
                    check=True,
                    env=dict(
                        os.environ,
                        GITHUB_EVENT_NAME=event,
                        GITHUB_WORKFLOW=caller,
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
        token_steps = [
            s
            for s in steps
            if "secrets.EE_REPO_READ_TOKEN" in s or "with: *ee_checkout" in s
        ]
        self.assertEqual(
            len(token_steps), 3
        )  # cold producer, shards, independent control
        guards = [
            next(
                line.strip()
                for line in s.splitlines()
                if line.startswith("        if:")
            )
            for s in token_steps
        ]
        self.assertCountEqual(
            guards,
            [
                "if: inputs.backend_cold_fanout && env.IS_INTERNAL_RUN == 'true'",
                "if: env.IS_INTERNAL_RUN == 'true'",
                "if: needs.plan.outputs.cold_control != 'success' && env.IS_INTERNAL_RUN == 'true'",
            ],
        )
        for step in token_steps:
            self.assertRegex(
                step,
                r"uses: future-agi/future-agi/\.github/actions/checkout-ee@[a-f0-9]{40}\n",
            )
        self.assertIn(
            "IS_INTERNAL_RUN: ${{ needs.changes.outputs.internal }}", workflow
        )
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
