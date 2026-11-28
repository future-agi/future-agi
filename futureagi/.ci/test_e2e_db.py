"""The E2E stack's saved database dump: its key, its record and the scratch control."""

import contextlib
import gzip
import hashlib
import io
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

import e2e_db
import test_db_dump as samples

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTROL = ROOT / "futureagi/.ci/e2e-db-control.sh"
BOUNDARY = ROOT / "futureagi/.ci/e2e_db_migrate.py"
KEY = "e2e-migrated-db-v1-distributed-" + "1" * 40

# Stands in for `bin/e2e`: each database is one file that holds its dump.
# `migrate` fills an empty database with the next prepared dump.
FAKE_E2E = r"""#!/usr/bin/env python3
import os, pathlib, sys
state = pathlib.Path(os.environ["FAKE_PG"])
args = sys.argv[1:]
with (state / "calls").open("a") as log:
    # The wrapper's text is one argument of many lines: noted by name.
    log.write(" ".join("<wrapper>" if "\n" in a else a for a in args) + "\n")
def db(name):
    return state / f"db-{name}"
if args[:2] == ["compose", "run"]:
    name = next(a for a in args if a.startswith("PG_DB=")).split("=", 1)[1]
    # The stack's migrate, stopped before post_migrate: the wrapper's text.
    code = pathlib.Path(os.environ["FAKE_MIGRATE_CODE"]).read_text().rstrip("\n")
    assert args[-2:] == ["-c", code], args[:-1]
    assert db(name).read_text() == "", "migrate into a database that is not empty"
    count = len(list(state.glob("migrated-*"))) + 1
    (state / f"migrated-{count}").write_text(name)
    if os.environ.get("FAKE_MIGRATE_FAILS"):
        sys.exit(1)
    source = state / f"migrate-{count}.sql"
    if not source.exists():
        source = state / "migrate-1.sql"
    db(name).write_text(source.read_text())
    sys.exit(0)
assert args[:4] == ["compose", "exec", "-T", "postgres"], args
program, rest = args[4], args[5:]
if program == "createdb":
    assert not db(rest[-1]).exists()
    db(rest[-1]).write_text("")
elif program == "dropdb":
    db(rest[-1]).unlink(missing_ok=True)
elif program == "pg_dump":
    sys.stdout.write(db(rest[-1]).read_text())
elif program == "psql":
    name = rest[rest.index("-d") + 1]
    query = rest[rest.index("-c") + 1] if "-c" in rest else ""
    if "server_version_num" in query:
        print(os.environ.get("FAKE_PG_VERSION", "160004"))
    elif "count(*)" in query:
        print(0 if db(name).read_text() == "" else 7)
    elif "django_migrations" in query:
        lines = db(name).read_text().split("\n")
        start = lines.index("COPY public.django_migrations (id, app, name, applied) FROM stdin;")
        for row in lines[start + 1 : lines.index("\\.", start)]:
            print("\t".join(row.split("\t")[1:3]))
    else:
        db(name).write_text(sys.stdin.read())
        if os.environ.get("FAKE_LOAD_FAILS"):
            sys.exit(3)
else:
    sys.exit(f"unexpected program {program}")
"""


def run_tool(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = e2e_db.main(list(argv))
    return code, out.getvalue()


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)

    def gz(self, name, text):
        path = self.dir / name
        with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path


class KeyTest(Workspace):
    def repo(self):
        root = self.dir / "repo"
        for path, text in {
            "futureagi/accounts/migrations/0001_initial.py": "one",
            "futureagi/tfc/settings/settings.py": "settings",
            "futureagi/uv.lock": "lock",
            "futureagi/Dockerfile.oss": "image",
            "futureagi/ee/falcon_ai/builtin_skills/a.yaml": "skill",
            "futureagi/.ci/e2e_db.py": "tool",
            "futureagi/.ci/e2e-db-control.sh": "control",
            "futureagi/.ci/e2e_db_migrate.py": "migrate",
            "futureagi/.ci/db_dump.py": "dump tool",
            "futureagi/conftest.py": "pytest only",
            "futureagi/accounts/views.py": "view",
            "docker-compose.distributed.yml": "postgres:16",
            "e2e/stack/docker-compose.e2e.yml": "overlay",
            "e2e/stack/e2e.env": "env",
            "docker-compose.yml": "standalone",
            "e2e/stack/docker-compose.standalone-e2e.yml": "standalone overlay",
            "e2e/stack/standalone-e2e.env": "standalone env",
            "deploy/standalone/bin/bootstrap.py": "bootstrap",
            "frontend/package.json": "{}",
        }.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        self.git(root, "init", "-q")
        self.commit(root)
        return root

    def git(self, cwd, *args):
        subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    def commit(self, root):
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "change", "--no-gpg-sign")

    def keys_after(self, root, path, text):
        (root / path).write_text(text)
        self.commit(root)
        return {stack: e2e_db.inputs_key(root, stack) for stack in e2e_db.STACKS}

    SHARED = (
        "futureagi/accounts/migrations/0001_initial.py",
        "futureagi/tfc/settings/settings.py",
        "futureagi/uv.lock",
        "futureagi/Dockerfile.oss",
        # What a migration opens: the same list the backend's key has.
        "futureagi/ee/falcon_ai/builtin_skills/a.yaml",
        "futureagi/.ci/e2e_db.py",
        # What runs the migrate: a change there can change the dump.
        "futureagi/.ci/e2e-db-control.sh",
        "futureagi/.ci/e2e_db_migrate.py",
        "futureagi/.ci/db_dump.py",
    )
    # The files that name a stack's Postgres image, build its image and hold
    # its settings.
    OWN = {
        "distributed": (
            "docker-compose.distributed.yml",
            "e2e/stack/docker-compose.e2e.yml",
            "e2e/stack/e2e.env",
        ),
        "standalone": (
            "docker-compose.yml",
            "e2e/stack/docker-compose.standalone-e2e.yml",
            "e2e/stack/standalone-e2e.env",
            "deploy/standalone/bin/bootstrap.py",
        ),
    }

    def test_each_stack_has_its_own_name(self):
        root = self.repo()
        keys = {stack: e2e_db.inputs_key(root, stack) for stack in e2e_db.STACKS}
        self.assertEqual(sorted(keys), ["distributed", "standalone"])
        for stack, key in keys.items():
            self.assertRegex(key, rf"^e2e-migrated-db-v1-{stack}-[0-9a-f]{{40}}$")
        # Not one hash under two names: the stack is part of what is hashed.
        self.assertNotEqual(keys["distributed"][-40:], keys["standalone"][-40:])
        with self.assertRaisesRegex(e2e_db.db_dump.DumpError, "no E2E stack"):
            e2e_db.inputs_key(root, "error-feed")
        code, out = run_tool("key", "error-feed", "--root", str(root))
        self.assertEqual(code, 1)
        self.assertIn("::error title=E2E database dump::", out)

    def test_what_every_stacks_migrate_reads_changes_every_key(self):
        root = self.repo()
        keys = {stack: e2e_db.inputs_key(root, stack) for stack in e2e_db.STACKS}
        for path in self.SHARED:
            with self.subTest(path):
                changed = self.keys_after(root, path, f"changed {path}")
                for stack in keys:
                    self.assertNotEqual(changed[stack], keys[stack], stack)
                keys = changed

    def test_a_stacks_own_files_change_only_its_key(self):
        root = self.repo()
        keys = {stack: e2e_db.inputs_key(root, stack) for stack in e2e_db.STACKS}
        for stack, paths in self.OWN.items():
            (other,) = set(keys) - {stack}
            for path in paths:
                with self.subTest(path):
                    changed = self.keys_after(root, path, f"changed {path}")
                    self.assertNotEqual(changed[stack], keys[stack])
                    self.assertEqual(changed[other], keys[other])
                    keys = changed

    def test_what_it_does_not_read_keeps_the_key(self):
        root = self.repo()
        keys = {stack: e2e_db.inputs_key(root, stack) for stack in e2e_db.STACKS}
        for path in (
            "futureagi/accounts/views.py",
            # pytest's files are the backend key's business, not this one's.
            "futureagi/conftest.py",
            "frontend/package.json",
        ):
            with self.subTest(path):
                self.assertEqual(self.keys_after(root, path, "changed"), keys)
        (root / "futureagi/uv.lock").write_text("not committed")
        self.assertEqual(e2e_db.inputs_key(root, "distributed"), keys["distributed"])

    def test_the_part_shared_with_the_backend_key_comes_from_its_list(self):
        shared = e2e_db.db_dump.KEY_FILES - e2e_db.PYTEST_ONLY
        self.assertIn("ee/falcon_ai/builtin_skills_loader.py", shared)
        self.assertIn("uv.lock", shared)
        self.assertNotIn("conftest.py", shared)
        for stack in e2e_db.STACKS:
            for name in shared:
                self.assertTrue(e2e_db.key_input("futureagi/" + name, stack), name)
            for folder in e2e_db.db_dump.KEY_DIRS:
                self.assertTrue(e2e_db.key_input(f"futureagi/{folder}x", stack), folder)

    def test_the_recipe_adds_the_server_that_made_the_dump(self):
        self.assertEqual(e2e_db.recipe(KEY, "160004"), KEY + "-pg160004")
        for odd in ("", "16.4", "16 (Debian)", "160004\n160004"):
            with self.subTest(odd), self.assertRaises(e2e_db.db_dump.DumpError):
                e2e_db.recipe(KEY, odd)


class RecordTest(Workspace):
    def saved(self, recipe=KEY + "-pg160004"):
        out = self.dir / "record"
        out.mkdir()
        dump = self.gz("fresh.sql.gz", samples.sql())
        e2e_db.write_record(out, dump, "a" * 64, recipe)
        return out

    def test_a_written_record_verifies_and_gives_its_recipe(self):
        out = self.saved()
        self.assertEqual(
            sorted(p.name for p in out.iterdir()),
            ["checksums", "dump.sql.gz", "preseed", "recipe"],
        )
        self.assertEqual(e2e_db.verify(out, KEY), KEY + "-pg160004")
        # The same four files and the same checksum lines as the backend's record.
        sums = "".join(
            f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}\n"
            for name in ("dump.sql.gz", "preseed", "recipe")
        )
        self.assertEqual((out / "checksums").read_text(), sums)

    def test_a_record_that_is_changed_odd_or_for_another_key_is_refused(self):
        def tamper(name, text):
            return lambda out: (out / name).write_text(text)

        cases = {
            "changed dump": tamper("dump.sql.gz", "other"),
            "changed digest": tamper("preseed", "b" * 64 + "\n"),
            "extra file": tamper("extra", "x"),
            "missing file": lambda out: (out / "preseed").unlink(),
            "a link": lambda out: (
                (out / "preseed").unlink(),
                (out / "preseed").symlink_to(out / "recipe"),
            ),
        }
        for name, change in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.dir / "record", ignore_errors=True)
                out = self.saved()
                change(out)
                with self.assertRaises(e2e_db.db_dump.DumpError):
                    e2e_db.verify(out, KEY)
        shutil.rmtree(self.dir / "record")
        out = self.saved()
        with self.assertRaises(e2e_db.db_dump.DumpError):
            e2e_db.verify(out, "e2e-migrated-db-v1-" + "2" * 40)
        shutil.rmtree(out)
        with self.assertRaises(e2e_db.db_dump.DumpError):
            e2e_db.verify(self.saved(recipe=KEY + "-other"), KEY)

    def test_the_command_line_reports_a_bad_record_without_a_traceback(self):
        out = self.saved()
        (out / "dump.sql.gz").write_text("other")
        code, text = run_tool("verify", str(out), KEY)
        self.assertEqual(code, 1)
        self.assertIn("::error title=E2E database dump::", text)


class DifferingTest(Workspace):
    def test_it_names_the_tables_and_never_a_value(self):
        first = self.gz("a.sql.gz", samples.sql())
        again = self.gz(
            "b.sql.gz", samples.sql(token="tokenB", seeds=samples.SEEDS_AGAIN)
        )
        self.assertEqual(e2e_db.differing(first, again), [])
        secret = "s3cret-value"
        other = self.gz(
            "c.sql.gz",
            samples.sql(seeds=(samples.SEEDS[0], samples.SEEDS[1] + secret)),
        )
        self.assertEqual(e2e_db.differing(first, other), ["public.some_seed"])
        code, text = run_tool("differing", str(first), str(other))
        self.assertEqual((code, text), (0, "public.some_seed\n"))
        self.assertNotIn(secret, text)

    def test_a_schema_difference_and_the_ledger_are_named_too(self):
        first = self.gz("a.sql.gz", samples.sql())
        schema = self.gz("b.sql.gz", samples.sql(body="  PERFORM 1;"))
        ledger = self.gz(
            "c.sql.gz",
            samples.sql(rows=samples.ROWS + ("3\ttracer\t0003\t2026-11-21",)),
        )
        self.assertEqual(e2e_db.differing(first, schema), ["(schema)"])
        self.assertIn("public.django_migrations", e2e_db.differing(first, ledger))


class WhatADumpDoesNotCarryTest(Workspace):
    def test_roles_grants_and_database_settings_are_found(self):
        folder = self.dir / "futureagi/app/migrations"
        folder.mkdir(parents=True)
        (folder / "__init__.py").write_text("")
        # Samples for the search, put together from words. The update-safety
        # gate reads source files for statements like these and asks for a
        # reviewed declaration; none of these ever runs.
        table, who = "some_table", "reader"
        statements = tuple(
            " ".join(words)
            for words in (
                ("GRANT", "SELECT", "ON", table, "TO", who),
                ("revoke", "all", "on", table, "from", who),
                ("CREATE", "ROLE", who),
                ("CREATE", "USER", who),
                ("ALTER", "ROLE", who, "SET search_path = x"),
                ("ALTER", "DATABASE", "futureagi", "SET timezone TO 'UTC'"),
                ("ALTER", "SYSTEM", "SET work_mem = '1MB'"),
                ("ALTER", "TABLE", table, "OWNER", "TO", who),
            )
        )
        for number, statement in enumerate(statements):
            (folder / f"000{number}_x.py").write_text(
                f'operations = [migrations.RunSQL("{statement}")]\n'
            )
        (folder / "0100_fine.py").write_text(
            '# granted_by, owner, a user role field, "grant access on request"\n'
            "# and the like are not SQL\n"
            'operations = [migrations.AddField("grant", "granted_to_user", None)]\n'
        )
        found = e2e_db.not_carried(self.dir / "futureagi")
        self.assertEqual(
            sorted(path.name for path, _ in found),
            [f"000{number}_x.py" for number in range(len(statements))],
        )

    def test_no_migration_of_this_checkout_writes_what_a_dump_leaves_out(self):
        # The dump is taken without owners and privileges, of one database. A
        # grant, a role or a setting stored on the database would be missing
        # on every leg that restores it, with no error. If this fails, the
        # restoring legs need that statement applied another way before the
        # migration can land.
        found = e2e_db.not_carried(ROOT / "futureagi")
        self.assertEqual(
            [(str(path.relative_to(ROOT)), what) for path, what in found],
            [],
            "A migration writes something a restored dump does not carry "
            "(see NOT_CARRIED in futureagi/.ci/e2e_db.py). A leg that restores "
            "the saved dump would lack it, with no error.",
        )


# Stands in for Django and the stack's manage.py: it notes the command line
# Django sees, and whether post_migrate was sent.
FAKE_MIGRATE_MODULE = """
import pathlib
def emit_post_migrate_signal(*args, **kwargs):
    pathlib.Path("sent").write_text("post_migrate")
"""
FAKE_MANAGE = """
import pathlib, sys
from django.core.management.commands import migrate
pathlib.Path("argv").write_text(repr(sys.argv))
if pathlib.Path("applied").exists():
    migrate.emit_post_migrate_signal(1, False, "default")
"""


class BoundaryTest(Workspace):
    """The scratch migrate stops where post_migrate would be sent."""

    def setUp(self):
        super().setUp()
        commands = self.dir / "django/core/management/commands"
        commands.mkdir(parents=True)
        for folder in (commands, *commands.parents):
            if folder == self.dir:
                break
            (folder / "__init__.py").write_text("")
        (commands / "migrate.py").write_text(FAKE_MIGRATE_MODULE)
        (self.dir / "manage.py").write_text(FAKE_MANAGE)

    def run_boundary(self):
        # As the control runs it: the file's text, from manage.py's folder.
        return subprocess.run(
            ["python3", "-c", BOUNDARY.read_text().rstrip("\n")],
            cwd=self.dir,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            capture_output=True,
            text=True,
        )

    def test_migrate_runs_with_its_own_command_line_and_sends_no_post_migrate(self):
        (self.dir / "applied").write_text("")
        result = self.run_boundary()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.dir / "argv").read_text(), repr(["manage.py", "migrate", "--noinput"])
        )
        # The receivers seed from code no migration names: they run on the
        # leg that restores, never into the dump.
        self.assertFalse((self.dir / "sent").exists())

    def test_a_migrate_that_never_reaches_post_migrate_fails(self):
        result = self.run_boundary()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without reaching post_migrate", result.stderr)

    def test_the_control_hands_the_stack_this_wrapper_and_not_plain_migrate(self):
        control = CONTROL.read_text()
        self.assertIn('boundary=$(< "$here/e2e_db_migrate.py")\n', control)
        self.assertIn('"${run_migrate[@]}" \\\n    -c "$boundary"', control)
        # Outside comments, nothing runs a plain migrate.
        self.assertNotRegex(control, r"(?m)^[^#]*manage\.py migrate")
        for stack in e2e_db.STACKS:
            self.assertTrue(e2e_db.key_input("futureagi/.ci/e2e_db_migrate.py", stack))


class ControlTest(Workspace):
    def setUp(self):
        super().setUp()
        self.work = self.dir / "checkout"
        (self.work / "bin").mkdir(parents=True)
        fake = self.work / "bin/e2e"
        fake.write_text(FAKE_E2E)
        fake.chmod(0o755)
        self.state = self.dir / "pg"
        self.state.mkdir()
        (self.state / "migrate-1.sql").write_text(samples.sql())
        self.record = self.dir / "record"
        self.out = self.dir / "out"
        self.env = dict(
            os.environ,
            FAKE_PG=str(self.state),
            RUNNER_TEMP=str(self.dir / "tmp"),
            KEY=KEY,
            FOUND="false",
            PRODUCE="true",
            RECORD=str(self.record),
            OUT=str(self.out),
            FAKE_MIGRATE_CODE=str(BOUNDARY),
        )
        (self.dir / "tmp").mkdir()

    def control(self, **env):
        return subprocess.run(
            ["bash", str(CONTROL)],
            cwd=self.work,
            env={**self.env, **env},
            capture_output=True,
            text=True,
        )

    def save(self, text, recipe=KEY + "-pg160004"):
        self.record.mkdir()
        dump = self.gz("saved.sql.gz", text)
        digest = e2e_db.db_dump.fingerprint_rows(text.encode())
        e2e_db.write_record(self.record, dump, digest, recipe)

    def migrates(self):
        return len(list(self.state.glob("migrated-*")))

    def saved_files(self):
        return sorted(p.name for p in self.out.iterdir()) if self.out.exists() else []

    def test_a_dev_push_with_no_saved_dump_migrates_twice_and_saves_one(self):
        # The second migrate writes other ids and times: still the same dump.
        (self.state / "migrate-2.sql").write_text(
            samples.sql(token="tokenB", seeds=samples.SEEDS_AGAIN)
        )
        result = self.control()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.migrates(), 2)
        self.assertEqual(e2e_db.verify(self.out, KEY), KEY + "-pg160004")
        with gzip.open(self.out / "dump.sql.gz", "rt") as handle:
            self.assertEqual(handle.read(), samples.sql())
        # Scratch databases only: the stack's own database is never touched.
        calls = (self.state / "calls").read_text()
        named = re.findall(
            r"(?:-d |PG_DB=)(\S+)|(?:createdb|dropdb|pg_dump) .* (\S+)$", calls, re.M
        )
        databases = {first or last for first, last in named}
        self.assertEqual(
            databases,
            {
                "postgres",
                "e2e_scratch_fresh",
                "e2e_scratch_replay",
                "e2e_scratch_second",
            },
        )
        self.assertIn(
            "compose run --rm --no-deps -T -e PG_DB=e2e_scratch_fresh "
            "--entrypoint python postgres-schema-bootstrap -c <wrapper>",
            calls,
        )

    def test_a_migrate_that_differs_from_run_to_run_saves_nothing(self):
        (self.state / "migrate-2.sql").write_text(
            samples.sql(seeds=(samples.SEEDS[0], samples.SEEDS[1] + "-salted"))
        )
        result = self.control()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.saved_files(), [])
        self.assertRegex(
            result.stdout,
            r"::warning title=E2E database dump::Two migrates of this commit differ"
            r" in: public\.some_seed\. No dump is saved",
        )
        self.assertNotIn("salted", result.stdout + result.stderr)

    def test_a_saved_dump_equal_to_a_fresh_migrate_passes_and_saves_nothing(self):
        self.save(samples.sql(token="tokenB", seeds=samples.SEEDS_AGAIN))
        result = self.control(FOUND="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.migrates(), 1)
        self.assertEqual(self.saved_files(), [])
        self.assertIn("equals a fresh migrate", result.stdout)

    def test_a_saved_dump_that_differs_exits_3_and_names_the_tables(self):
        self.save(samples.sql(seeds=(samples.SEEDS[0], samples.SEEDS[1] + "-old")))
        result = self.control(FOUND="true")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertRegex(
            result.stdout,
            r"::error title=Saved E2E database dump differs from a fresh migrate::"
            r"Differs in: public\.some_seed\.",
        )
        self.assertIn("futureagi/.ci/e2e_db.py", result.stdout)
        self.assertNotIn("-old", result.stdout + result.stderr)
        self.assertEqual(self.saved_files(), [])

    def test_a_control_that_cannot_run_fails_but_never_with_3(self):
        broken = {
            "the migrate fails": {"FAKE_MIGRATE_FAILS": "1"},
            "a restore fails": {"FAKE_LOAD_FAILS": "1"},
            "the server version is odd": {"FAKE_PG_VERSION": "16.4 (Debian)"},
        }
        for name, env in broken.items():
            with self.subTest(name):
                shutil.rmtree(self.state)
                self.state.mkdir()
                (self.state / "migrate-1.sql").write_text(samples.sql())
                result = self.control(**env)
                self.assertNotIn(result.returncode, (0, 3), result.stdout)
                self.assertEqual(self.saved_files(), [])
                self.assertIn("::error title=", result.stdout)

    def test_a_tampered_record_fails_before_anything_is_compared(self):
        self.save(samples.sql())
        (self.record / "dump.sql.gz").write_bytes(b"other")
        result = self.control(FOUND="true")
        self.assertNotIn(result.returncode, (0, 3), result.stdout)
        self.assertNotIn("equals a fresh migrate", result.stdout)
        self.assertIn(
            "::error title=E2E database dump::the saved dump does not match its checksums",
            result.stdout,
        )

    def test_a_saved_dump_from_another_postgres_is_never_compared(self):
        # A new minor release of the image may print a dump differently. The
        # saved dump here differs from what migrate gives, and must not count.
        self.save(samples.sql(token="tokenOLD"), recipe=KEY + "-pg160003")
        # A run that does not save: nothing to do, not even a migrate.
        result = self.control(FOUND="true", PRODUCE="false")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("another Postgres version", result.stdout)
        self.assertNotIn("equals a fresh migrate", result.stdout)
        self.assertEqual(self.migrates(), 0)
        self.assertEqual(self.saved_files(), [])
        # A dev push saves a fresh one for this server, as if nothing was saved.
        result = self.control(FOUND="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.migrates(), 2)
        self.assertEqual(e2e_db.verify(self.out, KEY), KEY + "-pg160004")

    def test_each_stack_runs_its_own_migrate_with_what_its_boot_sets(self):
        runs = {
            "distributed": "compose run --rm --no-deps -T -e PG_DB=e2e_scratch_fresh"
            " --entrypoint python postgres-schema-bootstrap -c <wrapper>",
            # deploy/standalone/bin/bootstrap.py sets both before it migrates.
            "standalone": "compose run --rm --no-deps -T -e PG_DB=e2e_scratch_fresh"
            " -e NO_STARTUP_DB_MUTATIONS=false -e SERVICE_TYPE=bootstrap"
            " -w /app/backend app python -c <wrapper>",
        }
        for stack, run in runs.items():
            with self.subTest(stack):
                shutil.rmtree(self.state)
                self.state.mkdir()
                (self.state / "migrate-1.sql").write_text(samples.sql())
                shutil.rmtree(self.out, ignore_errors=True)
                result = self.control(E2E_STACK=stack)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = (self.state / "calls").read_text().splitlines()
                self.assertIn(run, calls)
        bootstrap = (ROOT / "deploy/standalone/bin/bootstrap.py").read_text()
        self.assertIn('os.environ["NO_STARTUP_DB_MUTATIONS"] = "false"', bootstrap)
        self.assertIn('os.environ["SERVICE_TYPE"] = "bootstrap"', bootstrap)
        result = self.control(E2E_STACK="error-feed")
        self.assertNotIn(result.returncode, (0, 3), result.stdout)
        self.assertIn("No scratch migrate is defined", result.stdout)

    def test_a_run_that_neither_compares_nor_saves_does_nothing(self):
        result = self.control(PRODUCE="false")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.migrates(), 0)


class WorkflowTest(unittest.TestCase):
    DEV_PUSH = "github.event_name == 'push' && github.ref == 'refs/heads/dev'"

    def setUp(self):
        self.text = (ROOT / ".github/workflows/e2e-ci.yml").read_text()

    def job(self, name):
        start = self.text.index(f"\n  {name}:\n")
        end = re.compile(r"\n  [a-z0-9-]+:\n").search(self.text, start + 1)
        return self.text[start : end.start() if end else len(self.text)]

    def step(self, job, name):
        start = job.index(f"      - name: {name}\n")
        end = job.find("\n      - ", start + 1)
        return job[start : end + 1 if end != -1 else len(job)]

    def test_the_fetch_runs_on_dev_pushes_only_and_cannot_fail_the_plan(self):
        changes = self.job("changes")
        step = self.step(
            changes, "Find each stack's saved database dump for this commit"
        )
        self.assertIn(f"if: {self.DEV_PUSH}", step)
        self.assertIn("continue-on-error: true\n", step)
        self.assertIn("timeout-minutes: 2\n", step)
        hand = self.step(
            changes, "Hand the saved database dumps to the legs that compare them"
        )
        self.assertIn("continue-on-error: true\n", hand)
        self.assertIn("if: steps.db.outputs.artifact != ''\n", hand)
        # After the plan: its outputs exist whatever happens here.
        self.assertLess(changes.index("      - id: shards\n"), changes.index(step))
        # The job's own rule: scripts come out of Git at the policy commit,
        # never from a checked-out candidate file.
        self.assertIn('git show "$GITHUB_SHA:futureagi/.ci/$tool"', step)
        self.assertEqual(
            re.findall(r"python3 .*", step),
            [
                'python3 -I "$tools/e2e_db.py" key "$stack" --root "$GITHUB_WORKSPACE")',
                'python3 -I "$tools/fetch_migrated_db.py" "$records/$stack"'
                ' --workflow .github/workflows/e2e-ci.yml --name "$key" &&',
                'python3 -I "$tools/e2e_db.py" verify "$records/$stack" "$key"; then',
            ],
        )
        # Every stack the run plans, each into its own folder.
        self.assertIn("STACKS: ${{ steps.stacks.outputs.stacks }}\n", step)
        self.assertIn("for stack in $(jq -r '.[]' <<< \"$STACKS\"); do\n", step)

    def test_the_control_runs_on_one_leg_per_stack_of_a_dev_push(self):
        e2e = self.job("e2e")
        suite = e2e.index("      - name: Run E2E suite\n")
        names = (
            "Download the saved database dump",
            "Compare the saved database dump with a fresh migrate, and save one",
            "Save the database dump for later dev runs",
        )
        role = f"{self.DEV_PUSH} && matrix.shard == 3"
        # Read so that a run without the plan's answer gives nothing, not an
        # expression error.
        mine = "fromJSON(needs.changes.outputs.db || '{}')[matrix.stack]"
        for name in names:
            step = self.step(e2e, name)
            self.assertLess(suite, e2e.index(step), name)
            self.assertIn(role, step, name)
            # The dump is public: nothing of a secret may be near the migrate.
            self.assertNotRegex(step, r"secrets\.|github\.token|TOKEN", name)
        control = self.step(e2e, names[1])
        self.assertIn("steps.boot.outcome == 'success'", control)
        self.assertIn("run: bash futureagi/.ci/e2e-db-control.sh\n", control)
        self.assertIn(f"KEY: ${{{{ {mine}.key }}}}\n", control)
        self.assertIn(f"FOUND: ${{{{ {mine}.found == true }}}}\n", control)
        self.assertIn(
            "RECORD: ${{ runner.temp }}/e2e-db-record/${{ matrix.stack }}\n", control
        )
        self.assertIn('PRODUCE: "true"\n', control)
        self.assertIn(f"&& {mine}.found }}}}\n", self.step(e2e, names[0]))
        save = self.step(e2e, names[2])
        self.assertIn(f"name: ${{{{ {mine}.key }}}}\n", save)
        self.assertIn("if-no-files-found: ignore\n", save)
        self.assertIn("retention-days: 7\n", save)
        self.assertIn("overwrite: true\n", save)
        # Nothing else in the job reads or restores the dump yet.
        self.assertEqual(e2e.count("db_artifact"), 1)
        self.assertEqual(e2e.count("needs.changes.outputs.db "), 4)

    def test_no_job_gained_a_permission_or_a_dependency(self):
        self.assertIn("    needs: [changes, lock-wheels]\n", self.job("e2e"))
        self.assertNotIn("actions: read", self.job("e2e"))


if __name__ == "__main__":
    unittest.main()
