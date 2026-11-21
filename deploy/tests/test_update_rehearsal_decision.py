"""What Update CI runs for an event: the two Real rehearsals and the other jobs (stdlib only)."""

import ast
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/update-rehearsal-decision.sh"
WORKFLOW = ROOT / ".github/workflows/update-ci.yml"
ACCEPTANCE = "deploy/tests/update_e2e/acceptance.py"
QUIET = {
    "LABELLED": "false",
    "TOOLING_CHANGED": "false",
    "BUILT_CHANGED": "false",
    "RELEVANT_CHANGED": "false",
}
# The registers that only scripts/check_update_safety.py reads.
STATIC_GATE_REGISTERS = (
    "deploy/update/clickhouse-sources.json",
    "deploy/update/clickhouse-change-reviews.json",
    "deploy/update/env-generators.json",
)
REAL_JOBS = {"automatic-upgrade": "upgrade", "automatic-a-b-c": "abc"}


def decide(event, **env):
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin", "EVENT_NAME": event, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def outcome(upgrade, abc, relevant):
    return {
        "upgrade": str(upgrade).lower(),
        "abc": str(abc).lower(),
        "relevant": str(relevant).lower(),
    }


def tracked():
    return subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()


def matches(pattern, path):
    """A path filter pattern against a repository path: ** crosses directories, * does not."""
    out = ""
    at = 0
    while at < len(pattern):
        if pattern.startswith("**/", at):
            out += "(?:.*/)?"
            at += 3
        elif pattern.startswith("**", at):
            out += ".*"
            at += 2
        elif pattern[at] == "*":
            out += "[^/]*"
            at += 1
        else:
            out += re.escape(pattern[at])
            at += 1
    return re.fullmatch(out, path) is not None


def imported(source):
    """(module, names) of every import of a Python source, also those inside functions."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # The oldest supported Python cannot parse every newer file.
        return [
            (found, [])
            for found in re.findall(
                r"^\s*(?:from|import)\s+([\w.]+)", source, re.MULTILINE
            )
        ]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, []) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.module, [alias.name for alias in node.names]))
    return found


def rehearsal_modules():
    """Every repository file the acceptance driver imports, directly or not."""
    seen = set()
    queue = [ACCEPTANCE]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        for module, names in imported((ROOT / path).read_text()):
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


class RehearsalDecisionTest(unittest.TestCase):
    def test_scheduled_and_dispatched_runs_always_rehearse(self):
        # The nightly is the gate that keeps these rehearsals mandatory.
        self.assertEqual(decide("schedule"), outcome(True, True, True))
        self.assertEqual(decide("workflow_dispatch"), outcome(True, True, True))

    def test_a_push_and_a_merge_group_never_rehearse(self):
        # Three hours per job would stall a merge queue.
        loud = {name: "true" for name in QUIET}
        loud["BASE_REF"] = "main"
        self.assertEqual(decide("push", **loud), outcome(False, False, True))
        self.assertEqual(decide("merge_group", **loud), outcome(False, False, True))

    def test_a_pull_request_that_touches_nothing_here_runs_no_other_job(self):
        # The workflow starts for every pull request. This is the whole cost
        # for one that changes, say, only the frontend: the decision job.
        nothing = outcome(False, False, False)
        self.assertEqual(decide("pull_request", BASE_REF="dev", **QUIET), nothing)
        self.assertEqual(
            decide("pull_request", BASE_REF="feat/stacked", **QUIET), nothing
        )

    def test_an_ordinary_pull_request_runs_the_other_jobs_and_no_rehearsal(self):
        backend = {**QUIET, "RELEVANT_CHANGED": "true"}
        self.assertEqual(
            decide("pull_request", BASE_REF="dev", **backend),
            outcome(False, False, True),
        )

    def test_a_release_or_a_label_rehearses_whatever_files_changed(self):
        # With a workflow-level path filter these two never started when the
        # pull request's files matched nothing.
        both = outcome(True, True, False)
        self.assertEqual(decide("pull_request", BASE_REF="main", **QUIET), both)
        labelled = {**QUIET, "LABELLED": "true"}
        self.assertEqual(decide("pull_request", BASE_REF="dev", **labelled), both)

    def test_a_tooling_change_runs_both(self):
        tooling = {**QUIET, "TOOLING_CHANGED": "true", "RELEVANT_CHANGED": "true"}
        self.assertEqual(
            decide("pull_request", BASE_REF="dev", **tooling),
            outcome(True, True, True),
        )

    def test_a_migration_runs_the_upgrade_from_the_latest_release_only(self):
        built = {**QUIET, "BUILT_CHANGED": "true", "RELEVANT_CHANGED": "true"}
        self.assertEqual(
            decide("pull_request", BASE_REF="dev", **built),
            outcome(True, False, True),
        )

    def test_a_pull_request_with_an_unknown_input_fails_instead_of_skipping(self):
        # The job then fails, and the workflow runs every job.
        for name in QUIET:
            for value in ("", "yes"):
                env = {**QUIET, name: value}
                self.assertEqual(decide("pull_request", BASE_REF="dev", **env), 1, name)
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)


class RehearsalWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.triggers = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.jobs = self.text.split("\njobs:\n", 1)[1]
        self.changes = self.job("changes")

    def job(self, name):
        body = self.jobs.split("\n  " + name + ":\n", 1)[1]
        return re.split(r"\n  [a-z0-9-]+:\n", body, maxsplit=1)[0]

    def filter(self, name):
        """The patterns of one named path filter of the changes job."""
        block = self.changes.split("            " + name + ":\n", 1)[1]
        patterns = []
        for line in block.splitlines():
            entry = line.strip()
            if entry.startswith(("- '", '- "')):
                patterns.append(entry[3:-1])
            elif entry and not entry.startswith("#"):
                break
        return patterns

    def test_both_real_jobs_follow_the_decision_and_fail_open(self):
        fail_open = (
            "(needs.changes.result != 'success' && github.event_name != 'push'"
            " && github.event_name != 'merge_group')"
        )
        for name, output in REAL_JOBS.items():
            head = self.job(name).split("    steps:", 1)[0]
            self.assertIn("    needs: changes\n", head, name)
            self.assertIn(
                "    if: ${{ !cancelled() && (needs.changes.outputs."
                + output
                + " == 'true' || "
                + fail_open
                + ") }}\n",
                head,
                name,
            )
        for output in ("upgrade", "abc", "relevant"):
            self.assertIn(
                "      " + output + ": ${{ steps.decide.outputs." + output + " }}\n",
                self.changes,
            )
        self.assertIn(
            'run: bash scripts/update-rehearsal-decision.sh >> "$GITHUB_OUTPUT"',
            self.changes,
        )

    def test_a_pull_request_is_admitted_whatever_it_touches(self):
        # A workflow-level path filter stops the workflow before the decision:
        # a pull request into main, or a labelled one, then never rehearsed.
        # No `types` either: a `labeled` event would cancel a running rehearsal.
        pull_request = self.triggers.split("  pull_request:\n", 1)[1].split(
            "  push:\n", 1
        )[0]
        self.assertEqual(pull_request, "    branches: [dev, main]\n")

    def test_the_other_jobs_follow_the_same_paths_as_a_push(self):
        push = self.triggers.split("  push:\n", 1)[1].split("  merge_group:\n", 1)[0]
        paths = [
            line.strip()[3:-1]
            for line in push.split("    paths:\n", 1)[1].splitlines()
            if line.strip().startswith('- "')
        ]
        self.assertGreater(len(paths), 40)
        self.assertEqual(self.filter("relevant"), paths)
        self.assertIn(
            "RELEVANT_CHANGED: ${{ steps.filter.outputs.relevant == 'true' }}",
            self.changes,
        )
        # Every job but the decision and the two rehearsals: it runs unless the
        # decision says the pull request is not relevant, so also when the
        # decision job failed.
        names = re.findall(r"^  ([a-z0-9-]+):$", self.jobs, re.MULTILINE)
        others = [name for name in names if name != "changes" and name not in REAL_JOBS]
        self.assertGreaterEqual(len(others), 6)
        for name in others:
            head = self.job(name).split("    steps:", 1)[0]
            self.assertIn("    needs: changes\n", head, name)
            self.assertIn(
                "    if: ${{ !cancelled() && needs.changes.outputs.relevant != 'false' }}\n",
                head,
                name,
            )

    def test_the_nightly_and_a_manual_run_still_exist(self):
        self.assertIn("  schedule:\n", self.triggers)
        self.assertIn("  workflow_dispatch:\n", self.triggers)
        # The cheap jobs still report on a merge group.
        self.assertIn("  merge_group:\n", self.triggers)

    def test_the_label_is_read_live_and_matched_exactly(self):
        self.assertIn(
            """--jq 'any(.labels[]; .name == "update-rehearsal")'""", self.changes
        )
        self.assertIn(
            "LABELLED: ${{ steps.labels.outputs.rehearsal == 'true' }}", self.changes
        )

    def update_code(self, path):
        """The update-code filter: every pattern must match."""
        block = self.changes.split("      - id: update-code\n", 1)[1].split(
            "      - id: labels\n", 1
        )[0]
        self.assertIn("predicate-quantifier: every", block)
        patterns = [
            line.strip()[3:-1]
            for line in block.splitlines()
            if line.strip().startswith("- '")
        ]
        self.assertEqual(
            patterns,
            ["deploy/update/**", "!**/*.md"]
            + ["!" + register for register in STATIC_GATE_REGISTERS],
        )
        return all(
            not matches(pattern[1:], path)
            if pattern.startswith("!")
            else matches(pattern, path)
            for pattern in patterns
        )

    def runs_both(self, path):
        return self.update_code(path) or any(
            matches(pattern, path) for pattern in self.filter("tooling")
        )

    def test_everything_the_rehearsals_import_runs_both(self):
        # The list is derived: the acceptance driver and every module it
        # imports from scripts/ and deploy/, at any depth.
        modules = rehearsal_modules()
        for required in (
            ACCEPTANCE,
            "scripts/rehearse_update_compatibility.py",
            "scripts/build_update_compatibility_proof.py",
            "scripts/update_rehearsal_qualification.py",
            "scripts/generate_update_manifest.py",
            "deploy/update/manifest.py",
        ):
            self.assertIn(required, modules)
        for path in modules:
            self.assertTrue(self.runs_both(path), path)
        self.assertTrue(self.runs_both("bin/update"))
        self.assertTrue(self.runs_both("docker-compose.yml"))
        self.assertTrue(self.runs_both(".github/workflows/update-ci.yml"))
        # The local actions the two jobs use.
        for name in REAL_JOBS:
            local = re.findall(r"uses: \./(\S+)", self.job(name))
            self.assertTrue(local, name)
            for action in local:
                self.assertTrue(self.runs_both(action + "/action.yml"), action)

    def test_every_register_the_update_tool_reads_runs_both(self):
        # Excluded: documents, and the registers only the static gate reads.
        # A register a rehearsal module names must not be excluded.
        sources = "".join(
            (ROOT / path).read_text() for path in [*rehearsal_modules(), "bin/update"]
        )
        registers = [
            path
            for path in tracked()
            if path.startswith("deploy/update/") and path.endswith(".json")
        ]
        self.assertIn("deploy/update/release-policy.json", registers)
        self.assertIn("deploy/update/high-volume-tables.json", registers)
        for path in registers:
            if path in STATIC_GATE_REGISTERS:
                self.assertFalse(self.update_code(path), path)
                self.assertNotIn(path.rsplit("/", 1)[-1], sources, path)
            else:
                self.assertTrue(self.update_code(path), path)
        self.assertFalse(self.update_code("deploy/update/README.md"))
        self.assertTrue(self.update_code("deploy/update/apply.py"))
        gate = (ROOT / "scripts/check_update_safety.py").read_text()
        for register in STATIC_GATE_REGISTERS:
            self.assertIn(register.rsplit("/", 1)[-1], gate, register)

    def test_no_whole_source_glob_runs_both(self):
        # An ordinary backend or docs change must not run two three-hour jobs.
        files = tracked()
        tooling = self.filter("tooling")
        for pattern in tooling:
            self.assertFalse(pattern.startswith("**"), pattern)
            self.assertNotIn(
                pattern, ("deploy/**", "futureagi/**", "scripts/**", "bin/**")
            )
            self.assertTrue(any(matches(pattern, path) for path in files), pattern)
        for path in (
            "futureagi/tracer/views/observation_span.py",
            "scripts/check-staged-safety.mjs",
            "frontend/src/app.jsx",
            "README.md",
        ):
            self.assertFalse(self.runs_both(path), path)

    def test_what_the_images_build_and_boot_triggers_latest_stable_only(self):
        built = self.filter("built")
        self.assertIn(
            "BUILT_CHANGED: ${{ steps.filter.outputs.built == 'true' }}", self.changes
        )
        files = tracked()
        for pattern in built:
            self.assertTrue(any(matches(pattern, path) for path in files), pattern)

        def builds(path):
            return any(matches(pattern, path) for pattern in built)

        # Migrations, settings and dependencies; the Standalone image's own
        # directory; the ClickHouse schema the boot installs; the chart the
        # job packages.
        for path in (
            "futureagi/tracer/migrations/0001_initial.py",
            "futureagi/tfc/settings/settings.py",
            "futureagi/uv.lock",
            "deploy/standalone/bin/bootstrap.py",
            "deploy/standalone/nginx.conf",
            "deploy/helm/futureagi/Chart.yaml",
            "futureagi/tfc/management/commands/bootstrap_install.py",
            "futureagi/tracer/services/clickhouse/oss_cdc_install.py",
            "futureagi/tracer/services/clickhouse/v2/apply_schema.py",
            "futureagi/tracer/services/clickhouse/v2/apply_schema_rewriter.py",
            "futureagi/tracer/services/clickhouse/v2/schema_topology.py",
            "futureagi/tracer/services/clickhouse/v2/observed_catalog/schema.sql",
            "futureagi/scripts/property_catalog_oss/validate_clickhouse.sql",
        ):
            self.assertIn(path, files)
            self.assertTrue(builds(path), path)
        schema = [
            path
            for path in files
            if path.startswith("futureagi/tracer/services/clickhouse/v2/schema/")
        ]
        self.assertGreater(len(schema), 10)
        for path in files:
            name = path.rsplit("/", 1)[-1]
            # Every image build and entrypoint in the repository, and every
            # file of the boot-time ClickHouse schema.
            if (
                name.lower().startswith("dockerfile")
                or name.endswith("entrypoint.sh")
                or path in schema
            ):
                self.assertTrue(builds(path), path)
        # Each image the job builds: its Dockerfile is listed.
        acceptance = (ROOT / ACCEPTANCE).read_text()
        dockerfiles = re.findall(r"'([\w./-]*Dockerfile[\w.]*)'", acceptance)
        self.assertGreaterEqual(len(dockerfiles), 5)
        for path in dockerfiles:
            self.assertTrue(builds(path), path)
        # The product source inside those images is left to the nightly run.
        self.assertFalse(builds("futureagi/tracer/views/observation_span.py"))


if __name__ == "__main__":
    unittest.main()
