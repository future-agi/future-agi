"""Credential-free checks for the early migration-leaf gate. It must say "no"
for an app with two latest migrations, and must never say "no" where Django
would say "yes": wherever it is unsure it gives no verdict and warns."""

import contextlib
import io
import pathlib
import re
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import migration_leaves  # noqa: E402

REPO = HERE.parents[1]


def migration(*dependencies, extra=""):
    body = ", ".join(dependencies)
    return textwrap.dedent(
        f"""
        from django.conf import settings
        from django.db import migrations

        class Migration(migrations.Migration):
            dependencies = [{body}]
            {extra}
            operations = []
        """
    )


class Tree:
    def __init__(self, case):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name) / "futureagi"

    def app(self, folder, files, label=None):
        directory = self.root / folder / "migrations"
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("")
        if label:
            (directory.parent / "apps.py").write_text(
                f"from django.apps import AppConfig\n\nclass C(AppConfig):\n    name = 'x'\n    label = {label!r}\n"
            )
        for name, text in files.items():
            (directory / f"{name}.py").write_text(text)
        return directory

    def run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = migration_leaves.main(["migration_leaves.py", str(self.root)])
        return code, out.getvalue()


def chain(label, *names):
    """A straight line of migrations: each follows the one before it."""
    files, previous = {}, None
    for name in names:
        files[name] = migration(*([f"('{label}', '{previous}')"] if previous else []))
        previous = name
    return files


class MigrationLeavesTest(unittest.TestCase):
    def setUp(self):
        self.tree = Tree(self)

    def test_this_checkout_has_no_app_with_two_latest_migrations(self):
        # Only the "no" is asserted here. The plan job runs this file on every
        # candidate: a candidate the script gives no verdict on (a squashed
        # migration, a tree without the enterprise folder) must stay green.
        clashes, _warnings, _judged = migration_leaves.check(REPO / "futureagi")
        self.assertEqual(clashes, [])

    def test_the_label_of_a_real_app_whose_folder_has_another_name(self):
        self.assertEqual(
            migration_leaves.app_label(REPO / "futureagi/agentcc"), "prism"
        )
        self.assertEqual(
            migration_leaves.app_label(REPO / "futureagi/simulate"), "simulate"
        )

    def test_two_latest_migrations_is_a_no_that_names_the_app_and_both(self):
        files = chain("simulate", "0001_initial", "0002_base")
        files["0003_mine"] = migration(
            "('simulate', '0002_base')", "('accounts', '0001_initial')"
        )
        files["0003_theirs"] = migration("('simulate', '0002_base')")
        self.tree.app("simulate", files)
        self.tree.app("accounts", chain("accounts", "0001_initial", "0002_more"))
        code, out = self.tree.run()
        self.assertEqual(code, 1)
        error = [line for line in out.splitlines() if line.startswith("::error")]
        self.assertEqual(len(error), 1)
        self.assertIn("'simulate'", error[0])
        self.assertIn("0003_mine and 0003_theirs", error[0])
        self.assertRegex(error[0], r"simulate[/\\]migrations")
        # Says what to do, for someone who has never seen it.
        self.assertIn("merge the latest dev into your branch", error[0])
        self.assertIn("dependencies", error[0])
        self.assertNotIn("accounts", error[0])
        self.assertIn(
            "2 apps read, 1 with more than one latest migration, 0 without a verdict",
            out,
        )

    def test_a_merge_migration_with_two_parents_passes(self):
        files = chain("tracer", "0001_initial", "0002_base")
        files["0003_a"] = migration("('tracer', '0002_base')")
        files["0003_b"] = migration("('tracer', '0002_base')")
        files["0004_merge_a_and_b"] = migration(
            "('tracer', '0003_a')", "('tracer', '0003_b')"
        )
        self.tree.app("tracer", files)
        self.assertEqual(self.tree.run()[0], 0)

    def test_the_highest_number_is_not_the_leaf(self):
        # 0005 was renumbered late but still follows 0002; 0003 follows it.
        files = chain("tracer", "0001_initial", "0002_base")
        files["0005_late"] = migration("('tracer', '0002_base')")
        files["0003_after_late"] = migration("('tracer', '0005_late')")
        directory = self.tree.app("tracer", files)
        self.assertEqual(
            migration_leaves.leaves(directory), ("tracer", ["0003_after_late"])
        )
        self.assertEqual(self.tree.run()[0], 0)

    def test_the_label_comes_from_apps_py_when_it_sets_one(self):
        files = chain("prism", "0001_initial", "0002_base")
        files["0003_a"] = migration("('prism', '0002_base')")
        files["0003_b"] = migration(
            "('prism', '0002_base')", "('agentcc', '0001_initial')"
        )
        self.tree.app("agentcc", files, label="prism")
        code, out = self.tree.run()
        self.assertEqual(code, 1)
        self.assertIn("'prism'", out)

    def assert_no_verdict(self, reason):
        code, out = self.tree.run()
        self.assertEqual(code, 0, out)
        self.assertNotIn("::error", out)
        warning = [line for line in out.splitlines() if line.startswith("::warning")]
        self.assertEqual(len(warning), 1, out)
        self.assertIn("Django's check decides", warning[0])
        self.assertRegex(warning[0], reason)
        self.assertIn("1 without a verdict", out)

    def two_leaves(self, label="simulate"):
        files = chain(label, "0001_initial", "0002_base")
        files["0003_a"] = migration(f"('{label}', '0002_base')")
        files["0003_b"] = migration(f"('{label}', '0002_base')")
        return files

    def test_a_file_that_cannot_be_read_means_no_verdict_for_its_app_only(self):
        # Skipping the broken file would make its parent look like a leaf.
        files = chain("simulate", "0001_initial", "0002_base", "0003_next")
        files["0003_next"] = "class Migration(:\n"
        self.tree.app("simulate", files)
        self.tree.app("tracer", self.two_leaves("tracer"))
        code, out = self.tree.run()
        self.assertEqual(code, 1)  # the other app is still judged
        self.assertEqual(
            len([line for line in out.splitlines() if line.startswith("::error")]), 1
        )
        self.assertIn("'tracer'", out)
        self.assertRegex(out, r"::warning::.*0003_next\.py cannot be read")

    def test_a_file_too_deep_to_parse_means_no_verdict_not_a_crash(self):
        # A crash would exit 1, which the workflow reads as a "no".
        self.tree.app("deep", chain("deep", "0001_initial", "0002_next"))
        with mock.patch.object(
            migration_leaves.ast, "parse", side_effect=RecursionError("too deep")
        ):
            code, out = self.tree.run()
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"::warning::.*cannot be read \(RecursionError\)")

    def test_what_the_files_cannot_settle_means_no_verdict(self):
        cases = {
            "a dependency that is not a plain pair": (
                {"0003_b": migration("('simulate', '0002_base')", "PARENT")},
                "neither a plain pair",
            ),
            "dependencies built by code": (
                {
                    "0003_b": migration().replace(
                        "dependencies = []", "dependencies = BASE + []"
                    )
                },
                "not a plain list",
            ),
            "dependencies changed after they are set": (
                {
                    "0003_b": migration("('accounts', '0001_initial')")
                    + "\nMigration.dependencies.append(('simulate', '0003_a'))\n"
                },
                "more than one place",
            ),
            "a parent that is not a file here": (
                {"0003_b": migration("('simulate', '0002_gone')")},
                "not a file here",
            ),
            "a squashed migration": (
                {
                    "0003_b": migration(
                        "('simulate', '0002_base')",
                        extra="replaces = [('simulate', '0003_a')]",
                    )
                },
                "sets replaces",
            ),
            "run_before": (
                {
                    "0003_b": migration(
                        "('simulate', '0002_base')",
                        extra="run_before = [('tracer', '0001_initial')]",
                    )
                },
                "sets run_before",
            ),
            "no Migration class": (
                {"0003_b": "x = 1\n"},
                "exactly one Migration class",
            ),
            "a label that is not a plain string": (
                {},
                "label that is not a plain string",
            ),
        }
        for name, (changed, reason) in cases.items():
            with self.subTest(name=name):
                self.tree = Tree(self)
                directory = self.tree.app("simulate", {**self.two_leaves(), **changed})
                if not changed:
                    (directory.parent / "apps.py").write_text(
                        "class C:\n    label = NAME\n"
                    )
                self.assert_no_verdict(reason)

    def test_a_label_the_files_do_not_use_means_no_verdict_not_a_no(self):
        # The folder is agentcc and nothing says the label is prism: every
        # file would look like a leaf. That is the script's doubt, not a clash.
        self.tree.app(
            "agentcc", chain("prism", "0001_initial", "0002_base", "0003_next")
        )
        self.assert_no_verdict("3 migrations depend on no migration of 'agentcc'")

    def test_dependencies_on_other_apps_are_ignored(self):
        files = chain("simulate", "0001_initial", "0002_base")
        files["0003_next"] = migration(
            "('simulate', '0002_base')",
            "migrations.swappable_dependency(settings.AUTH_USER_MODEL)",
            "('accounts', '0099_not_in_this_tree')",
            "('contenttypes', '__first__')",
        )
        self.tree.app("simulate", files)
        code, out = self.tree.run()
        self.assertEqual((code, "::warning" in out), (0, False), out)

    def test_only_migration_modules_are_read(self):
        files = chain("simulate", "0001_initial", "0002_base")
        files["_helpers"] = "this is not python ("
        directory = self.tree.app("simulate", files)
        (directory / "~0003_backup.py").write_text("neither is this (")
        (directory / "notes.txt").write_text("0003")
        (directory / "data").mkdir()
        (directory / "data" / "0003_inside_a_package.py").write_text(
            migration("('simulate', '0001_initial')")
        )
        self.tree.app("sdk", {})  # a migrations package with no migration
        code, out = self.tree.run()
        self.assertEqual(code, 0, out)
        self.assertIn(
            "1 apps read, 0 with more than one latest migration, 0 without a verdict",
            out,
        )


class WorkflowTest(unittest.TestCase):
    """Both workflows call the one script before any heavy job can start."""

    def job(self, workflow, name):
        text = (REPO / ".github/workflows" / workflow).read_text()
        start = text.index(f"\n  {name}:\n")
        following = re.search(r"\n  [a-z][a-z0-9-]*:\n", text[start + 1 :])
        return (
            text[start : start + 1 + following.start()] if following else text[start:]
        )

    def test_the_backend_plan_job_runs_it_first(self):
        plan = self.job("backend-ci.yml", "plan")
        steps = plan[plan.index("\n    steps:\n") :]
        first = re.findall(r"\n      - (?:name|uses): (.+)", steps)[:2]
        self.assertEqual(first, ["actions/checkout@v4", "One latest migration per app"])
        self.assertIn(
            "run: python3 -I futureagi/.ci/migration_leaves.py futureagi\n", steps
        )
        # Every shard and the other heavy jobs wait for plan.
        for job in ("test", "saml-multiworker", "migrated-database", "agentic-eval"):
            self.assertIn("needs: [changes, plan]", self.job("backend-ci.yml", job))
        # Django's own check is still there, and the final check still needs it.
        self.assertIn(
            "run: uv run --no-sync python manage.py makemigrations --check --dry-run\n",
            self.job("backend-ci.yml", "update-safety"),
        )
        self.assertIn("update-safety,", self.job("backend-ci.yml", "backend-tests"))
        self.assertIn("plan,", self.job("backend-ci.yml", "backend-tests"))

    def test_the_e2e_changes_job_runs_the_base_copy_before_it_plans(self):
        changes = self.job("e2e-ci.yml", "changes")
        gate = changes.index("- name: One latest migration per app")
        # Before the plan step: a failed job must leave no `any` output, or
        # the legs (which run on `!cancelled()`) would start anyway.
        self.assertLess(changes.index("- uses: actions/checkout@v4"), gate)
        self.assertLess(
            gate,
            changes.index(
                "- name: Plan from the event base and complete candidate diff"
            ),
        )
        step = changes[gate : changes.index("\n      - ", gate + 1)]
        # The job never runs code from the candidate: the script comes from
        # the base commit, and reads the candidate's files as text.
        self.assertIn("script=futureagi/.ci/migration_leaves.py\n", step)
        # Which commit is "the base": the queue's base, else the commit
        # itself; for a pull request the test merge's first parent, because
        # the event's base can predate the base tip the merge was built on.
        self.assertIn("BASE_SHA: ${{ github.event.merge_group.base_sha }}\n", step)
        self.assertIn("policy=${BASE_SHA:-$GITHUB_SHA}\n", step)
        self.assertIn(
            "if [[ $GITHUB_EVENT_NAME == pull_request ]]; then\n"
            "            policy=$(git rev-parse --verify --quiet 'HEAD^1^{commit}') || policy=\n",
            step,
        )
        self.assertNotIn("pull_request.base.sha", step)
        self.assertNotIn("policy=HEAD", step)
        # Reading the candidate's files can fail (blobs come on demand):
        # that is a doubt, and a doubt passes.
        self.assertIn("if ! git show ", step)
        self.assertIn("! git archive HEAD ", step)
        self.assertRegex(step, r"::warning[^\n]*\n\s+exit 0\n")
        self.assertIn(
            'git show "$policy:$script" > "$RUNNER_TEMP/migration-leaves.py"', step
        )
        self.assertIn(
            "git archive HEAD ':(glob)futureagi/**/migrations/*.py' ':(glob)futureagi/**/apps.py'",
            step,
        )
        self.assertIn('python3 -I "$RUNNER_TEMP/migration-leaves.py" futureagi', step)
        self.assertEqual(
            re.findall(r"python3 .*", step),
            ['python3 -I "$RUNNER_TEMP/migration-leaves.py" futureagi'],
        )
        # The final check fails when this job fails, and no leg starts.
        self.assertIn(
            'if [ "$CHANGES" != success ]; then', self.job("e2e-ci.yml", "e2e-tests")
        )
        self.assertIn(
            "needs.changes.outputs.any == 'true'", self.job("e2e-ci.yml", "e2e")
        )


if __name__ == "__main__":
    unittest.main()
