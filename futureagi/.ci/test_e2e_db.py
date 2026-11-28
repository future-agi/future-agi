"""The E2E stack's saved database dump: its key, its record and the scratch control."""

import contextlib
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import runpy
import shutil
import subprocess
import tempfile
import textwrap
import unittest

import e2e_db
import test_db_dump as samples

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTROL = ROOT / "futureagi/.ci/e2e-db-control.sh"
RESTORE = ROOT / "futureagi/.ci/e2e-db-restore.sh"
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
if args == ["compose", "up", "-d", "--wait", "temporal"]:
    sys.exit(1 if os.environ.get("FAKE_TEMPORAL_UP_FAILS") else 0)
if args[:6] == ["compose", "exec", "-T", "temporal", "temporal", "operator"]:
    # Distributed's replay of tracer 0093: Temporal's own CLI.
    verb, rest = args[7], args[8:]
    assert args[6] == "search-attribute", args
    assert rest[:4] == ["--address", "temporal:7233", "--namespace", "default"], args
    known = state / "search-attributes"
    names = known.read_text().split() if known.exists() else []
    if verb == "list":
        tries = state / "list-tries"
        count = int(tries.read_text()) + 1 if tries.exists() else 1
        tries.write_text(str(count))
        if count <= int(os.environ.get("FAKE_REGISTER_FAILS", "0")):
            print(f"try {count}: Error: unable to list search attributes:", file=sys.stderr)
            print("Namespace default is not found (100% sure).", file=sys.stderr)
            sys.exit(1)
        print("  Name  Type")
        for name in names:
            print(f"  {name}  Keyword")
        sys.exit(0)
    assert verb == "create", args
    pairs = rest[4:]
    created = pairs[1::4]
    assert pairs[0::4] == ["--name"] * len(created) and pairs[2::4] == ["--type"] * len(created), pairs
    assert pairs[3::4] == ["Keyword"] * len(created), pairs
    assert not set(created) & set(names), "created a name that exists"
    known.write_text(" ".join(names + created))
    sys.exit(0)
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
if args == ["compose", "up", "-d", "--wait", "postgres"]:
    if os.environ.get("FAKE_UP_FAILS"):
        sys.exit(1)
    # A new volume: Postgres creates the stack's database, empty.
    if not db("futureagi").exists():
        db("futureagi").write_text("")
    sys.exit(0)
if args == ["compose", "down", "-v"]:
    for path in state.glob("db-*"):
        path.unlink()
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


class ReceiverTest(Workspace):
    """The dump stops before post_migrate: every receiver runs on the boot."""

    # The project's post_migrate receivers. A leg that restores the dump gets
    # their rows only from its stack's own migrate, which applies nothing.
    KNOWN = [
        ("agent_playground/apps.py", "_seed_after_migrate"),
        ("ee/usage/apps.py", "self.create_usage_entries_after_migrate"),
        ("model_hub/apps.py", "_seed_prompt_labels_after_migrate"),
        (
            "tfc/management/commands/bootstrap_install.py",
            "_seed_prompt_labels_after_migrate",
        ),
    ]

    def test_the_projects_post_migrate_receivers_are_these(self):
        self.assertEqual(
            e2e_db.post_migrate_receivers(ROOT / "futureagi"),
            self.KNOWN,
            "A post_migrate receiver was added, moved or removed. The saved E2E "
            "dump stops before post_migrate, so on a leg that restores it the "
            "receiver runs only from the boot's own migrate, which applies no "
            "migration. The receiver must do its work then too: never only when "
            "its own migrations were just applied, never by reading the plan. "
            "Check that, then update KNOWN in futureagi/.ci/test_e2e_db.py.",
        )

    def test_the_search_finds_each_way_of_connecting_one(self):
        folder = self.dir / "app"
        (folder / "tests").mkdir(parents=True)
        (folder / "apps.py").write_text(
            "post_migrate.connect(\n    seed_one,\n    sender=self,\n)\n"
            "signals.post_migrate.connect(self.seed_two)\n"
        )
        (folder / "signals.py").write_text(
            "@receiver(post_migrate, sender=X)\ndef seed_three(sender, **kwargs):\n    pass\n"
        )
        (folder / "tests/test_apps.py").write_text("post_migrate.connect(fake)\n")
        (folder / "test_more.py").write_text("post_migrate.connect(fake)\n")
        self.assertEqual(
            e2e_db.post_migrate_receivers(self.dir),
            [
                ("app/apps.py", "seed_one"),
                ("app/apps.py", "self.seed_two"),
                ("app/signals.py", "seed_three"),
            ],
        )


class OutsideTest(unittest.TestCase):
    """Migrations that write outside Postgres: the dump holds them as applied."""

    # A leg that restores the dump never runs these: its boot's migrate finds
    # them applied. Each one's effect has to reach the stack another way.
    KNOWN = {
        # Both stacks set FI_SKIP_CH25_MIGRATION=1; their bootstraps apply the
        # ClickHouse schema on every start.
        "tracer/migrations/0078_ch25_apply_schema.py": "skipped",
        # Bridged by CI until the start-up registers them: on Distributed,
        # e2e-db-restore.sh adds them with Temporal's CLI (the stack refuses
        # the migration's own command outside migrate). Standalone's Temporal
        # starts with them. The product change that registers them at start-up
        # deletes the bridge (e2e_db.BRIDGES).
        "tracer/migrations/0093_register_eval_task_search_attributes.py": (
            "bridged by CI until the start-up registers them"
        ),
        # Reads ClickHouse only for queue items, and a new database has none.
        "model_hub/migrations/0117_backfill_queueitem_project.py": "nothing",
        # Clears cached Redis keys, and a new stack's Redis is empty.
        "ee/usage/migrations/0023_unlock_features_free_tier.py": "nothing",
    }
    OUTSIDE = re.compile(
        r"^\s*(?:from|import)\s+[\w.]*\b(?:temporalio|temporal|clickhouse|redis|"
        r"celery|requests|httpx|boto3|botocore|minio|kafka|peerdb)\b"
        r"|\bget_redis\(|\.apply_async\(|\.delay\(",
        re.M,
    )

    def outside(self, root):
        found = []
        for path in sorted(root.glob("**/migrations/*.py")):
            text = path.read_text()
            if "node_modules" not in path.parts and "RunPython" in text:
                if self.OUTSIDE.search(text):
                    found.append(path.relative_to(root).as_posix())
        return found

    def test_the_migrations_that_write_outside_postgres_are_these(self):
        self.assertEqual(
            self.outside(ROOT / "futureagi"),
            sorted(self.KNOWN),
            "A migration that reaches Temporal, ClickHouse, Redis or another "
            "store outside Postgres was added or removed. The saved E2E dump "
            "holds it only as applied, so a leg that restores the dump never "
            "runs it. First choice: make each stack's start-up do the same "
            "work on every start. Only if that cannot be done, replay it in "
            "futureagi/.ci/e2e-db-restore.sh. Or show it does nothing on a new "
            "stack. Then update KNOWN in futureagi/.ci/test_e2e_db.py.",
        )

    def test_the_search_finds_each_way_of_reaching_out(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = pathlib.Path(tmp.name) / "app/migrations"
        folder.mkdir(parents=True)
        body = "from django.db import migrations\nops = [migrations.RunPython(f)]\n"
        samples_ = {
            "0001_a.py": "    from tfc.temporal.x import y\n",
            "0002_b.py": "from tracer.services.clickhouse.v2 import get_reader\n",
            "0003_c.py": "    r = get_redis()\n",
            "0004_d.py": "    task.delay(1)\n",
            "0005_e.py": "    import requests\n",
            # A model named after a store is not reaching it.
            "0006_f.py": "    model_name='clickhouseannotation'\n",
        }
        for name, line in samples_.items():
            (folder / name).write_text(body + line)
        (folder / "0007_g.py").write_text(
            "from tfc.temporal import x\n"
        )  # no RunPython
        self.assertEqual(
            self.outside(pathlib.Path(tmp.name)),
            [f"app/migrations/{n}" for n in sorted(samples_)[:5]],
        )

    def test_each_stack_makes_the_effects_the_dump_cannot_hold(self):
        compose = (ROOT / "docker-compose.distributed.yml").read_text()
        self.assertIn('  FI_SKIP_CH25_MIGRATION: "1"\n', compose)
        bootstrap = (ROOT / "deploy/standalone/bin/bootstrap.py").read_text()
        self.assertIn('os.environ.setdefault("FI_SKIP_CH25_MIGRATION", "1")', bootstrap)
        self.assertIn("steps.register_search_attributes(log)", bootstrap)
        script = RESTORE.read_text()
        self.assertIn(e2e_db.BRIDGES["distributed"], script)
        self.assertIn('"$here/../tfc/temporal/eval_tasks/search_attributes.py"', script)

    def test_the_bridge_can_create_every_attribute_the_product_defines(self):
        # Fails when the product's file holds anything the bridge cannot make.
        found = e2e_db.search_attributes(ROOT / "futureagi" / e2e_db.SEARCH_ATTRIBUTES)
        self.assertEqual(
            found,
            [
                ("EvalTaskOrgId", "Keyword"),
                ("EvalTaskProjectId", "Keyword"),
                ("EvalTaskRunType", "Keyword"),
                ("EvalTaskStatus", "Keyword"),
            ],
        )

    def test_the_reader_refuses_what_the_bridge_cannot_create(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "search_attributes.py"
        head = "from temporalio.common import SearchAttributeKey\n"
        for body in (
            'X = SearchAttributeKey.for_int("EvalTaskCount")\n',
            "NAME = 'EvalTaskOrgId'\nX = SearchAttributeKey.for_keyword(NAME)\n",
            'X = SearchAttributeKey.for_keyword("bad name")\n',
            "",
        ):
            with self.subTest(body=body):
                path.write_text(head + body)
                with self.assertRaises(ValueError):
                    e2e_db.search_attributes(path)
        path.write_text(head + 'A = SearchAttributeKey.for_keyword("EvalTaskOrgId")\n')
        self.assertEqual(e2e_db.search_attributes(path), [("EvalTaskOrgId", "Keyword")])
        self.assertEqual(
            run_tool("search-attributes", str(path)), (0, "EvalTaskOrgId\n")
        )

    def test_the_bridges_address_and_namespace_are_the_stacks_own(self):
        compose = (ROOT / "docker-compose.distributed.yml").read_text()
        self.assertIn("  TEMPORAL_HOST: temporal:7233\n", compose)
        self.assertIn("  TEMPORAL_NAMESPACE: ${TEMPORAL_NAMESPACE:-default}\n", compose)
        # Nothing in the E2E stack moves the namespace off its default.
        for path in ("e2e/stack/e2e.env", "e2e/stack/docker-compose.e2e.yml"):
            self.assertNotIn("TEMPORAL_NAMESPACE", (ROOT / path).read_text(), path)
        at = "--address temporal:7233 --namespace default"
        self.assertIn("at=(" + at + ")", RESTORE.read_text())
        workflow = (ROOT / ".github/workflows/e2e-ci.yml").read_text()
        self.assertIn("search-attribute list " + at + ")", workflow)

    def test_the_bridge_only_lists_and_creates(self):
        verbs = re.findall(r'"\$\{cli\[@\]\}" (\S+)', RESTORE.read_text())
        self.assertEqual(sorted(set(verbs)), ["create", "list"])
        self.assertNotRegex(
            RESTORE.read_text(), r"search-attribute (remove|update|delete)"
        )


class PinPlaceTest(unittest.TestCase):
    """The pins above run on a pull request that only adds a migration or a
    receiver: Backend CI's plan job runs every futureagi/.ci/test_*.py."""

    def test_backend_ci_runs_these_tests_on_every_code_change(self):
        text = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        on = text.split("\non:\n", 1)[1].split("\njobs:\n", 1)[0]
        pr = on.split("  pull_request:\n", 1)[1].split("\n  push:\n", 1)[0]
        # No path filter: every pull request starts the workflow.
        self.assertNotIn("paths", pr)
        jobs = text.split("\njobs:\n", 1)[1]
        plan = jobs[jobs.index("\n  plan:\n") : jobs.index("\n  test:\n")]
        self.assertIn("    if: needs.changes.outputs.backend == 'true'\n", plan)
        step = plan.split(
            "      - name: Test CI asset verification and failure paths\n", 1
        )[1].split("\n      - ", 1)[0]
        self.assertNotIn("if:", step)
        self.assertIn(
            "run: python3 -I -m unittest discover -s futureagi/.ci -p 'test_*.py' -v",
            step,
        )

    def test_a_migration_or_a_receiver_alone_runs_the_backend_suite(self):
        scope = runpy.run_path(str(ROOT / ".github/scripts/ci_scope.py"))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = pathlib.Path(tmp.name)

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(work), *args], text=True
            ).strip()

        git("init", "-q", "-b", "dev")
        git("config", "user.name", "fixture")
        git("config", "user.email", "fixture@example.invalid")
        (work / "README.md").write_text("x\n")
        git("add", "-A")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(work)
        for path, text in (
            (
                "futureagi/tracer/migrations/0200_reach_out.py",
                "from tfc.temporal import x\n",
            ),
            ("futureagi/model_hub/apps.py", "post_migrate.connect(seed)\n"),
        ):
            with self.subTest(path=path):
                git("checkout", "-q", "-B", "candidate", base)
                (work / path).parent.mkdir(parents=True, exist_ok=True)
                (work / path).write_text(text)
                git("add", "-A")
                git("commit", "-qm", "candidate")
                head = git("rev-parse", "HEAD")
                # A queue entry under the narrowing mode: the narrowest case.
                result = scope["plan"]("merge_group", head, base, head, "qualified")
                self.assertTrue(result["flags"]["backend"], result)


class SwitchTest(Workspace):
    """E2E_DB_REUSE: which stacks restore, and what the plan job prints."""

    TOTALS = {"distributed": 8, "standalone": 6}
    BOTH_FOUND = {
        "distributed": {"key": "kd", "found": True},
        "standalone": {"key": "ks", "found": True},
    }

    def decide(self, switch, totals=None, db=None, bridged=tuple(e2e_db.BRIDGES)):
        lines, result = e2e_db.plan(
            switch,
            totals or self.TOTALS,
            self.BOTH_FOUND if db is None else db,
            bridged,
        )
        return lines, {stack: entry["restore"] for stack, entry in result.items()}

    def test_unset_empty_or_blank_is_off_for_every_stack(self):
        for value in (None, "", " ", ",", " , "):
            with self.subTest(value=value):
                lines, restores = self.decide(value)
                self.assertEqual(restores, {"distributed": False, "standalone": False})
                self.assertIn("E2E_DB_REUSE is not set", lines[0])

    def test_only_the_named_stacks_restore(self):
        _lines, restores = self.decide("distributed")
        self.assertEqual(restores, {"distributed": True, "standalone": False})
        _lines, restores = self.decide(" standalone , distributed ")
        self.assertEqual(restores, {"distributed": True, "standalone": True})
        # Names are exact: a stack is named only by its own name.
        _lines, restores = self.decide("Distributed,distributed-x")
        self.assertEqual(restores, {"distributed": False, "standalone": False})

    def test_an_unknown_name_warns_quoting_it_and_the_known_names_still_apply(self):
        lines, restores = self.decide("distributed,standalon")
        self.assertEqual(restores, {"distributed": True, "standalone": False})
        warnings = [line for line in lines if line.startswith("::warning")]
        self.assertEqual(len(warnings), 1, lines)
        self.assertIn("'standalon'", warnings[0])
        self.assertIn("It is ignored", warnings[0])

    def test_the_value_read_and_each_stacks_decision_are_printed(self):
        lines, _ = self.decide("distributed")
        self.assertEqual(lines[0], "E2E_DB_REUSE is 'distributed'.")
        self.assertTrue(lines[1].startswith("distributed: restores the saved dump"))
        self.assertIn(
            "standalone: migrates from zero: E2E_DB_REUSE does not name it", lines[2]
        )

    def test_a_value_that_could_hold_a_workflow_command_is_never_printed(self):
        lines, restores = self.decide("distributed,::error::x\n::warning::y")
        self.assertEqual(restores["distributed"], True)
        joined = "\n".join(lines)
        self.assertNotIn("::error", joined)
        self.assertNotIn("::warning::y", joined)
        self.assertIn("a name with other characters", joined)

    def test_no_saved_dump_means_no_restore(self):
        db = {**self.BOTH_FOUND, "distributed": {"key": "kd", "found": False}}
        lines, restores = self.decide("distributed,standalone", db=db)
        self.assertEqual(restores, {"distributed": False, "standalone": True})
        self.assertIn(
            "distributed: migrates from zero: no saved dump", "\n".join(lines)
        )
        # A stack the fetch never answered for has nothing to restore either.
        _lines, restores = self.decide("distributed", db={})
        self.assertEqual(restores, {"distributed": False, "standalone": False})

    def test_a_stack_without_a_zero_leg_and_a_control_leg_never_restores(self):
        for total, expected in ((1, False), (2, False), (3, True)):
            with self.subTest(total=total):
                _lines, restores = self.decide(
                    "distributed", totals={"distributed": total}
                )
                self.assertEqual(restores, {"distributed": expected})

    def test_distributed_never_restores_without_its_bridge_whatever_the_switch(self):
        lines, restores = self.decide("distributed,standalone", bridged=())
        self.assertEqual(restores, {"distributed": False, "standalone": True})
        self.assertIn(
            "distributed: migrates from zero: this commit's restore step cannot add "
            "Temporal's eval-task search attributes (migration tracer 0093).",
            lines,
        )
        # The command line reads the bridges from the restore step it is given.
        out = self.dir / "db.json"
        script = self.dir / "restore.sh"
        script.write_text("#!/usr/bin/env bash\nsay restored\n")
        both = json.dumps(self.BOTH_FOUND)
        for given, expected in ((script, False), (RESTORE, True), (None, False)):
            with self.subTest(script=given):
                extra = ["--restore-script", str(given)] if given else []
                code, printed = run_tool(
                    "plan",
                    "distributed",
                    json.dumps(self.TOTALS),
                    both,
                    str(out),
                    *extra,
                )
                self.assertEqual(code, 0, printed)
                self.assertIs(
                    json.loads(out.read_text())["distributed"]["restore"], expected
                )

    def test_the_command_line_writes_the_result_and_names_only_known_stacks(self):
        out = self.dir / "db.json"
        code, printed = run_tool(
            "plan",
            "distributed,nope",
            '{"distributed": 8}',
            '{"distributed": {"key": "kd", "found": true}}',
            str(out),
            "--restore-script",
            str(RESTORE),
        )
        self.assertEqual(code, 0, printed)
        self.assertEqual(
            json.loads(out.read_text()),
            {"distributed": {"key": "kd", "found": True, "restore": True}},
        )
        # Compact: the workflow finds '"restore":true' in it as text.
        self.assertIn('"restore":true', out.read_text())
        self.assertIn("::warning", printed)
        self.assertEqual(
            run_tool("named", "standalone,nope, distributed"),
            (0, "standalone distributed\n"),
        )
        code, printed = run_tool("plan", "distributed", "not json", "{}", str(out))
        self.assertEqual(code, 1)
        self.assertIn("::error", printed)


class SummaryTest(Workspace):
    """The run summary counts what each leg of a restoring run did."""

    def leg(self, shard, result, attempt=1, stack="distributed", suite=None):
        folder = self.dir / f"db-reuse-{stack}-{shard}-attempt-{attempt}"
        folder.mkdir()
        tail = f"suite: {suite}\n" if suite else ""
        (folder / "e2e-db-result").write_text(result + "\n" + tail)

    def test_each_failed_leg_is_named_with_its_role(self):
        self.leg(1, "restored", suite="success")
        self.leg(2, "zero", suite="failure")
        self.leg(3, "restored", suite="failure")
        self.leg(4, "fell back: the restore failed", suite="failure")
        self.leg(5, "restored", suite="failure")
        table = e2e_db.summary(self.dir, "distributed", 6)
        failed = table.split("Failed legs:\n", 1)[1].split("\n\n")[0]
        self.assertEqual(
            failed.splitlines(),
            [
                "- shard 2: migrated from zero, by design",
                "- shard 3: restored the saved dump, and compared it with a fresh migrate",
                "- shard 4: migrated from zero after its restore fell back",
                "- shard 5: restored the saved dump",
            ],
        )
        self.assertTrue(table.endswith("\n\n" + e2e_db.RESTORED_FAILED))
        # Said plainly: a cause that is possible, and how to tell.
        self.assertEqual(
            e2e_db.RESTORED_FAILED,
            "A leg that restored the saved dump failed. A restored database that "
            "lacks something the stack needs is possible: the dump holds only "
            "Postgres, and the boot's migrate applies nothing on it. To tell: take "
            "this stack out of the E2E_DB_REUSE repository variable (or clear it), "
            "then re-run all jobs of this run. Every leg then migrates from zero. "
            "If the same tests pass, the saved database is the cause; if they fail "
            "the same way, it is not.",
        )
        self.assertNotIn("likely", table)

    def test_a_failure_only_on_legs_from_zero_says_nothing_of_the_dump(self):
        self.leg(1, "restored", suite="success")
        self.leg(2, "zero", suite="failure")
        table = e2e_db.summary(self.dir, "distributed", 3)
        self.assertIn("- shard 2: migrated from zero, by design", table)
        self.assertNotIn(e2e_db.RESTORED_FAILED, table)

    def test_a_run_with_no_failed_leg_lists_none(self):
        self.leg(1, "restored", suite="success")
        self.leg(2, "zero")
        self.assertNotIn("Failed legs", e2e_db.summary(self.dir, "distributed", 3))

    def test_the_last_attempt_decides_whether_a_leg_failed(self):
        self.leg(3, "restored", suite="failure")
        self.leg(3, "restored", attempt=2, suite="success")
        self.assertNotIn("Failed legs", e2e_db.summary(self.dir, "distributed", 3))

    def test_each_kind_is_counted_with_its_shards_and_reasons(self):
        self.leg(1, "restored")
        self.leg(2, "zero")
        self.leg(3, "restored")
        self.leg(4, "fell back: the restore failed")
        self.leg(5, "miss: Postgres version")
        self.leg(1, "restored", stack="standalone")
        table = e2e_db.summary(self.dir, "distributed", 6)
        self.assertIn("| restored | 2 | 1, 3 |", table)
        self.assertIn("| zero | 1 | 2 |", table)
        self.assertIn("| fell back | 1 | 4 |", table)
        self.assertIn("| miss | 1 | 5 |", table)
        # A leg that left no result (it failed before the step, or was
        # cancelled) is shown, never counted as restored.
        self.assertIn("| no result | 1 | 6 |", table)
        self.assertIn("- shard 4: fell back: the restore failed", table)
        self.assertIn("- shard 5: miss: Postgres version", table)

    def test_the_last_attempt_of_a_shard_counts(self):
        self.leg(1, "fell back: Postgres did not start", attempt=1)
        self.leg(1, "restored", attempt=2)
        table = e2e_db.summary(self.dir, "distributed", 1)
        self.assertIn("| restored | 1 | 1 |", table)
        self.assertIn("| fell back | 0 | - |", table)
        self.assertNotIn("no result", table)

    def test_no_results_at_all_is_every_leg_without_one(self):
        self.assertIn(
            "| no result | 3 | 1, 2, 3 |",
            e2e_db.summary(self.dir / "none", "distributed", 3),
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


class RestoreTest(Workspace):
    """futureagi/.ci/e2e-db-restore.sh against the stand-in for `bin/e2e`."""

    def setUp(self):
        super().setUp()
        self.work = self.dir / "checkout"
        (self.work / "bin").mkdir(parents=True)
        fake = self.work / "bin/e2e"
        fake.write_text(FAKE_E2E)
        fake.chmod(0o755)
        self.state = self.dir / "pg"
        self.state.mkdir()
        self.record = self.dir / "record"
        self.result = self.dir / "result"
        self.dump = samples.sql()
        self.env = dict(
            os.environ,
            FAKE_PG=str(self.state),
            KEY=KEY,
            STACK="distributed",
            RESTORE="true",
            ROLE="restore",
            RECORD=str(self.record),
            RESULT=str(self.result),
        )

    def save(self, recipe=KEY + "-pg160004", rows=None):
        self.record.mkdir()
        dump = self.gz("saved.sql.gz", self.dump)
        digest = rows or e2e_db.db_dump.fingerprint_rows(self.dump.encode())
        e2e_db.write_record(self.record, dump, digest, recipe)

    def restore(self, **env):
        # `sleep` between tries: noted, not waited for.
        stubs = self.dir / "stubs"
        stubs.mkdir(exist_ok=True)
        (stubs / "sleep").write_text(
            '#!/usr/bin/env bash\necho "sleep $*" >> "$FAKE_PG/calls"\n'
        )
        (stubs / "sleep").chmod(0o755)
        path = f"{stubs}:{os.environ['PATH']}"
        run = subprocess.run(
            ["bash", str(RESTORE)],
            cwd=self.work,
            env={**self.env, "PATH": path, **env},
            capture_output=True,
            text=True,
        )
        # Whatever happens, the leg goes on: the script never fails it.
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return self.result.read_text().strip(), run.stdout

    def calls(self):
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def database(self):
        path = self.state / "db-futureagi"
        return path.read_text() if path.exists() else None

    def test_a_good_dump_is_restored_into_the_stacks_own_database(self):
        self.save()
        result, out = self.restore()
        self.assertEqual(result, "restored")
        self.assertIn("Database for this leg: restored", out)
        self.assertEqual(self.database(), self.dump)
        self.assertIn("compose up -d --wait postgres", self.calls())
        self.assertNotIn("compose down -v", self.calls())
        # Only the stack's database; no scratch database on a restoring leg.
        self.assertEqual(
            sorted(p.name for p in self.state.glob("db-*")), ["db-futureagi"]
        )

    def test_a_run_that_restores_nothing_and_the_zero_leg_touch_nothing(self):
        self.save()
        self.assertEqual(self.restore(RESTORE="false")[0], "off")
        self.assertEqual(self.restore(ROLE="zero")[0], "zero")
        self.assertEqual(self.calls(), [])

    def test_a_dump_from_another_postgres_is_a_miss_and_writes_nothing(self):
        self.save()
        result, _out = self.restore(FAKE_PG_VERSION="160020")
        self.assertEqual(result, "miss: Postgres version")
        self.assertEqual(self.database(), "")
        self.assertNotIn("compose down -v", self.calls())

    def test_every_failure_falls_back_to_a_clean_stack_and_says_why(self):
        cases = {
            "the downloaded dump does not verify": (
                {},
                lambda: (self.record / "recipe").write_text("odd\n"),
            ),
            "Postgres did not start": ({"FAKE_UP_FAILS": "1"}, None),
            "the restore failed": ({"FAKE_LOAD_FAILS": "1"}, None),
        }
        for reason, (env, spoil) in cases.items():
            with self.subTest(reason):
                shutil.rmtree(self.record, ignore_errors=True)
                for path in self.state.iterdir():
                    path.unlink()
                self.save()
                if spoil:
                    spoil()
                result, out = self.restore(**env)
                self.assertEqual(result, f"fell back: {reason}")
                self.assertIn("::warning title=E2E database reuse::", out)
                # Never a half-restored database: no volume is left.
                self.assertIn("compose down -v", self.calls())
                self.assertIsNone(self.database())

    UP = "compose up -d --wait temporal"
    LIST = "compose exec -T temporal temporal operator search-attribute list --address temporal:7233 --namespace default"
    # The product's own list: what every eval-task workflow upserts.
    NAMES = re.findall(
        r'for_keyword\("(\w+)"\)',
        (ROOT / "futureagi/tfc/temporal/eval_tasks/search_attributes.py").read_text(),
    )

    def registered(self):
        path = self.state / "search-attributes"
        return path.read_text().split() if path.exists() else []

    def test_distributed_adds_the_products_search_attributes_after_the_restore(self):
        self.save()
        result, out = self.restore()
        self.assertEqual(result, "restored")
        self.assertEqual(len(self.NAMES), 4)
        self.assertEqual(self.registered(), self.NAMES)
        calls = self.calls()
        # Temporal starts only after the rows are checked.
        up = calls.index(self.UP)
        self.assertGreater(up, max(i for i, c in enumerate(calls) if "psql" in c))
        create = [c for c in calls if " create " in c]
        self.assertEqual(len(create), 1)
        self.assertTrue(
            create[0].endswith(
                "".join(f" --name {n} --type Keyword" for n in self.NAMES)
            )
        )
        self.assertEqual(calls.count(self.LIST), 2)  # before, and to confirm
        self.assertRegex(
            out,
            r"Registered the Temporal search attributes \(EvalTaskOrgId [^)]*\) "
            r"in \d+ s \(try 2 of 12\)\.",
        )

    def test_a_try_after_a_partial_one_adds_only_what_is_missing(self):
        self.save()
        self.state.mkdir(exist_ok=True)
        (self.state / "search-attributes").write_text(" ".join(self.NAMES[:2]))
        self.assertEqual(self.restore()[0], "restored")
        self.assertEqual(sorted(self.registered()), sorted(self.NAMES))
        create = [c for c in self.calls() if " create " in c]
        self.assertEqual(len(create), 1)
        self.assertNotIn(self.NAMES[0], create[0])

    def test_standalone_adds_nothing_its_temporal_starts_with_them(self):
        self.save()
        self.assertEqual(self.restore(STACK="standalone")[0], "restored")
        self.assertFalse([c for c in self.calls() if "temporal" in c])

    def test_temporal_gets_time_to_add_its_namespace(self):
        self.save()
        result, out = self.restore(FAKE_REGISTER_FAILS="3")
        self.assertEqual(result, "restored")
        self.assertRegex(out, r"in \d+ s \(try 5 of 12\)\.")
        calls = self.calls()
        self.assertEqual(calls.count(self.LIST), 5)
        self.assertEqual(calls.count("sleep 10"), 3)

    def test_temporal_that_does_not_start_falls_back(self):
        self.save()
        result, _out = self.restore(FAKE_TEMPORAL_UP_FAILS="1")
        self.assertEqual(result, "fell back: Temporal did not start")
        self.assertIsNone(self.database())

    def test_search_attributes_never_registered_fall_back_to_a_clean_stack(self):
        self.save()
        result, out = self.restore(FAKE_REGISTER_FAILS="99")
        self.assertEqual(
            result, "fell back: the Temporal search attributes were not registered"
        )
        calls = self.calls()
        self.assertEqual(calls.count(self.LIST), 12)
        # No wait after the last try.
        self.assertEqual(calls.count("sleep 10"), 11)
        self.assertEqual(calls[-1], "compose down -v")
        self.assertIsNone(self.database())
        # The last try's output, in the log and in the one-line warning.
        self.assertIn("try 12: Error: unable to list search attributes:\n", out)
        warning = next(x for x in out.splitlines() if x.startswith("::warning"))
        self.assertIn(
            " Its last try printed:%0Atry 12: Error: unable to list search "
            "attributes:%0ANamespace default is not found (100%25 sure).%0A",
            warning,
        )
        self.assertNotIn("try 11:", out)

    def test_a_leg_with_no_stack_falls_back(self):
        self.save()
        self.assertEqual(self.restore(STACK="")[0], "fell back: no stack was named")
        self.assertIsNone(self.database())

    def test_rows_other_than_the_dumps_own_fall_back(self):
        self.save(rows="0" * 64)
        result, _out = self.restore()
        self.assertEqual(
            result,
            "fell back: the restored rows are not the ones the dump was made from",
        )
        self.assertIsNone(self.database())

    def test_a_leg_with_no_key_falls_back(self):
        self.save()
        self.assertEqual(
            self.restore(KEY="")[0], "fell back: no key was handed to this leg"
        )


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
        # What to do now: the switch off, then the whole run again.
        self.assertIn(
            "take this stack out of the E2E_DB_REUSE repository variable", result.stdout
        )
        self.assertIn("then re-run all jobs of this run", result.stdout)
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

    MINE = "fromJSON(needs.changes.outputs.db || '{}')[matrix.stack]"

    def run_block(self, step):
        """The shell of a step's `run: |`, as the runner gets it."""
        body = step.split("        run: |\n", 1)[1]
        return textwrap.dedent(
            "".join(
                line
                for line in body.splitlines(True)
                if line.startswith("          ") or not line.strip()
            )
        )

    def run_step(self, step, scripts, env):
        """Runs a step's shell in a checkout whose scripts are stubs."""
        work = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        # `timeout` as on the runner: the command, run to its end here.
        stubs = {
            "bin/e2e": 'echo "$*" >> "$CALLS"\n',
            "stubs/timeout": 'shift\nexec "$@"\n',
        }
        for path, text in {**scripts, **stubs}.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("#!/usr/bin/env bash\n" + text)
            target.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{work / 'stubs'}:{os.environ['PATH']}",
            "CALLS": str(work / "calls"),
            "RUNNER_TEMP": str(work),
            **env,
        }
        run = subprocess.run(
            ["bash", "-e", "-c", self.run_block(step)],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
        )
        calls = (work / "calls").read_text() if (work / "calls").exists() else ""
        return run, calls

    def test_the_plan_finds_dumps_on_every_event_and_cannot_fail_the_plan(self):
        changes = self.job("changes")
        step = self.step(
            changes, "Find each stack's saved database dump for this commit"
        )
        self.assertNotIn("\n        if:", step)
        self.assertIn("continue-on-error: true\n", step)
        self.assertIn("timeout-minutes: 2\n", step)
        hand = self.step(
            changes, "Hand the saved database dumps to the legs that use them"
        )
        self.assertIn("continue-on-error: true\n", hand)
        self.assertIn("if: steps.db.outputs.artifact != ''\n", hand)
        # After the plan: its outputs exist whatever happens here.
        self.assertLess(changes.index("      - id: shards\n"), changes.index(step))
        # The scripts come out of Git at the policy commit: the pushed commit,
        # a queue entry's base, or the base tip a pull request's test merge
        # was built on. Never a candidate file, with this job's token.
        self.assertIn("BASE_SHA: ${{ github.event.merge_group.base_sha }}\n", step)
        self.assertIn("policy=${BASE_SHA:-$GITHUB_SHA}\n", step)
        self.assertIn("policy=$(git rev-parse --verify 'HEAD^1^{commit}')\n", step)
        self.assertIn('git show "$policy:futureagi/.ci/$tool"', step)
        # A base from before the tools ends the step quietly, before any fetch.
        guard = step.index("The policy commit cannot plan a restore yet")
        self.assertLess(guard, step.index('fetch_migrated_db.py" "$records'))
        self.assertIn(
            'python3 -I "$tools/e2e_db.py" plan --help > /dev/null 2>&1; then', step
        )
        self.assertNotIn("$GITHUB_SHA:", step)
        self.assertEqual(
            re.findall(r"python3 .*", step),
            [
                'python3 -I "$tools/e2e_db.py" plan --help > /dev/null 2>&1; then',
                'python3 -I "$tools/e2e_db.py" named "$E2E_DB_REUSE") "',
                'python3 -I "$tools/e2e_db.py" key "$stack" --root "$GITHUB_WORKSPACE")',
                'python3 -I "$tools/fetch_migrated_db.py" "$records/$stack"'
                ' --workflow .github/workflows/e2e-ci.yml --name "$key" &&',
                'python3 -I "$tools/e2e_db.py" verify "$records/$stack" "$key"; then',
                'python3 -I "$tools/e2e_db.py" plan "$E2E_DB_REUSE" "$TOTALS" "$db"'
                ' "$RUNNER_TEMP/e2e-db-plan.json" \\',
            ],
        )
        # The bridges are read from the restore step the legs run.
        self.assertIn(
            '  --restore-script "$GITHUB_WORKSPACE/futureagi/.ci/e2e-db-restore.sh"\n',
            step,
        )
        # A dev push fetches for every stack; any other run only for a named one.
        self.assertIn('if [[ $DEV_PUSH == true || $named == *" $stack "* ]] &&\n', step)
        self.assertIn(f"DEV_PUSH: ${{{{ {self.DEV_PUSH} }}}}\n", step)
        self.assertIn("E2E_DB_REUSE: ${{ vars.E2E_DB_REUSE }}\n", step)
        self.assertIn("TOTALS: ${{ steps.shards.outputs.totals }}\n", step)
        self.assertIn("STACKS: ${{ steps.stacks.outputs.stacks }}\n", step)
        self.assertIn("for stack in $(jq -r '.[]' <<< \"$STACKS\"); do\n", step)
        # The plan's answer is the last `db` written: the one the legs read.
        self.assertTrue(
            step.rstrip().endswith(
                'echo "db=$(cat "$RUNNER_TEMP/e2e-db-plan.json")" >> "$GITHUB_OUTPUT"'
            )
        )

    def test_a_leg_restores_before_its_boot_and_shard_2_never_does(self):
        e2e = self.job("e2e")
        order = [
            "Await the vendor image prefetch",
            "Download the saved database dump",
            "Restore the saved database dump",
            "Boot the e2e stack",
        ]
        at = [e2e.index(f"      - name: {name}\n") for name in order]
        self.assertEqual(at, sorted(at))
        download = self.step(e2e, order[1])
        self.assertIn(f"if: ${{{{ {self.MINE}.found == true }}}}\n", download)
        self.assertIn("continue-on-error: true\n", download)
        restore = self.step(e2e, order[2])
        self.assertIn(f"if: ${{{{ {self.MINE}.restore == true }}}}\n", restore)
        self.assertIn(
            "ROLE: ${{ matrix.shard == 2 && 'zero' || 'restore' }}\n", restore
        )
        self.assertIn(f"KEY: ${{{{ {self.MINE}.key }}}}\n", restore)
        self.assertIn("STACK: ${{ matrix.stack }}\n", restore)
        self.assertIn("timeout 600 bash futureagi/.ci/e2e-db-restore.sh ||", restore)
        self.assertNotIn("continue-on-error", restore)
        self.assertNotRegex(restore + download, r"secrets\.|github\.token|TOKEN")
        # Shards 2 and 3 get fewer tests only when the plan says the stack restores.
        self.assertIn(
            f"      E2E_DB_RESTORES: ${{{{ {self.MINE}.restore == true }}}}\n", e2e
        )

    def test_every_distributed_leg_of_a_restoring_run_checks_temporals_attributes(self):
        e2e = self.job("e2e")
        check = self.step(e2e, "Check Temporal's eval-task search attributes")
        # After the suite, whatever its outcome; the leg from zero checks too.
        self.assertLess(
            e2e.index("      - name: Upload Redis diagnostics\n"), e2e.index(check)
        )
        self.assertIn(
            "if: ${{ !cancelled() && steps.boot.outcome == 'success' && "
            f"matrix.stack == 'distributed' && {self.MINE}.restore == true }}}}\n",
            check,
        )
        self.assertNotIn("matrix.shard", check)
        self.assertNotIn("continue-on-error", check)
        names = [
            n
            for n, _ in e2e_db.search_attributes(
                ROOT / "futureagi" / e2e_db.SEARCH_ATTRIBUTES
            )
        ]
        for listed, fails in (
            ("  Name  Type\n" + "".join(f"  {n}  Keyword\n" for n in names), False),
            ("  Name  Type\n" + "".join(f"  {n}  Keyword\n" for n in names[1:]), True),
            ("  Name  Type\n" + "".join(f"  {n}  Int\n" for n in names), True),
        ):
            with self.subTest(listed=listed):
                work = pathlib.Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, work, ignore_errors=True)
                (work / "futureagi").mkdir()
                (work / "futureagi/.ci").symlink_to(ROOT / "futureagi/.ci")
                (work / "futureagi/tfc").symlink_to(ROOT / "futureagi/tfc")
                (work / "bin").mkdir()
                (work / "listed").write_text(listed)
                (work / "bin/e2e").write_text(
                    '#!/usr/bin/env bash\necho "$*" >> calls\ncat listed\n'
                )
                (work / "bin/e2e").chmod(0o755)
                run = subprocess.run(
                    ["bash", "-e", "-c", self.run_block(check)],
                    cwd=work,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(run.returncode != 0, fails, run.stdout + run.stderr)
                self.assertEqual(
                    (work / "calls").read_text(),
                    "compose exec -T temporal temporal operator search-attribute list"
                    " --address temporal:7233 --namespace default\n",
                )
                if fails:
                    self.assertIn("::error title=E2E database reuse::", run.stdout)
                else:
                    self.assertEqual(
                        run.stdout.splitlines(),
                        [
                            f"Temporal search attribute on this leg: {n} Keyword"
                            for n in names
                        ],
                    )

    def test_a_restore_step_that_stops_falls_back_and_never_fails_the_leg(self):
        restore = self.step(self.job("e2e"), "Restore the saved database dump")
        for script in ("exit 1\n", "kill -TERM $$\n", 'echo restored > "$RESULT"\n'):
            with self.subTest(script=script):
                work = pathlib.Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, work, ignore_errors=True)
                run, calls = self.run_step(
                    restore,
                    {"futureagi/.ci/e2e-db-restore.sh": script},
                    {"RESULT": str(work / "result")},
                )
                self.assertEqual(run.returncode, 0, run.stderr)
                result = (work / "result").read_text().strip()
                if script.startswith("echo"):
                    self.assertEqual(result, "restored")
                    self.assertEqual(calls, "")
                else:
                    self.assertEqual(result, "fell back: the restore step stopped")
                    self.assertEqual(calls, "compose down -v\n")

    def test_the_control_fails_every_run_on_a_difference_and_a_dev_push_on_any_failure(
        self,
    ):
        e2e = self.job("e2e")
        suite = e2e.index("      - name: Run E2E suite\n")
        control = self.step(
            e2e, "Compare the saved database dump with a fresh migrate, and save one"
        )
        self.assertLess(suite, e2e.index(control))
        self.assertIn(
            f"if: ${{{{ !cancelled() && steps.boot.outcome == 'success' && matrix.shard == 3"
            f" && ({self.DEV_PUSH} || {self.MINE}.restore == true) }}}}\n",
            control,
        )
        # On a hit it compares even when this leg's own restore fell back:
        # nothing in its condition reads the leg's result.
        self.assertNotIn("db-restore", control)
        self.assertIn(f"FOUND: ${{{{ {self.MINE}.found == true }}}}\n", control)
        self.assertIn(f"PRODUCE: ${{{{ {self.DEV_PUSH} }}}}\n", control)
        self.assertIn(
            "RECORD: ${{ runner.temp }}/e2e-db-record/${{ matrix.stack }}\n", control
        )
        self.assertNotRegex(control, r"secrets\.|github\.token|TOKEN")
        for produce, status, fails in (
            ("true", 0, False),
            ("true", 1, True),
            ("true", 3, True),
            ("false", 0, False),
            ("false", 1, False),
            ("false", 3, True),
        ):
            with self.subTest(produce=produce, status=status):
                run, _calls = self.run_step(
                    control,
                    {"futureagi/.ci/e2e-db-control.sh": f"exit {status}\n"},
                    {"PRODUCE": produce},
                )
                self.assertEqual(run.returncode != 0, fails, run.stdout)
                if status == 1 and produce == "false":
                    self.assertIn("::warning", run.stdout)

    def test_a_dev_push_fails_a_fallen_back_leg_after_its_suite_and_uploads(self):
        e2e = self.job("e2e")
        late = self.step(
            e2e, "Fail a dev push whose leg could not restore the saved dump"
        )
        upload = self.step(e2e, "Upload this leg's database result")
        for earlier in (
            "Run E2E suite",
            "Upload report",
            "Save the database dump for later dev runs",
        ):
            self.assertLess(
                e2e.index(f"      - name: {earlier}\n"), e2e.index(late), earlier
            )
        self.assertLess(e2e.index(upload), e2e.index(late))
        self.assertIn(
            f"if: ${{{{ always() && steps.db-restore.outcome == 'success' && {self.DEV_PUSH} }}}}\n",
            late,
        )
        self.assertIn(
            "if: ${{ always() && steps.db-restore.outcome == 'success' }}\n", upload
        )
        self.assertIn(
            "name: db-reuse-${{ matrix.stack }}-${{ matrix.shard }}-attempt-${{ github.run_attempt }}\n",
            upload,
        )
        for result, fails in (
            ("restored", False),
            ("zero", False),
            ("miss: Postgres version", False),
            ("fell back: x", True),
        ):
            with self.subTest(result=result):
                work = pathlib.Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, work, ignore_errors=True)
                (work / "e2e-db-result").write_text(result + "\nsuite: failure\n")
                run = subprocess.run(
                    ["bash", "-e", "-c", self.run_block(late)],
                    env={**os.environ, "RUNNER_TEMP": str(work)},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(run.returncode != 0, fails, run.stdout)
                # The error names the restore's result, not the suite line.
                self.assertNotIn("suite:", run.stdout)
        note = self.step(e2e, "Note this leg's outcome for the database count")
        self.assertLess(e2e.index("      - name: Run E2E suite\n"), e2e.index(note))
        self.assertLess(e2e.index(note), e2e.index(upload))
        self.assertIn(
            "if: ${{ always() && steps.db-restore.outcome == 'success' }}\n", note
        )
        self.assertIn("OUTCOME: ${{ job.status }}\n", note)
        work = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        (work / "e2e-db-result").write_text("restored\n")
        subprocess.run(
            ["bash", "-e", "-c", self.run_block(note)],
            env={**os.environ, "RUNNER_TEMP": str(work), "OUTCOME": "failure"},
            check=True,
        )
        self.assertEqual(
            (work / "e2e-db-result").read_text(), "restored\nsuite: failure\n"
        )

    def test_the_dev_push_producer_still_saves_by_key(self):
        e2e = self.job("e2e")
        save = self.step(e2e, "Save the database dump for later dev runs")
        self.assertIn(f"{self.DEV_PUSH} && matrix.shard == 3", save)
        self.assertIn(f"name: ${{{{ {self.MINE}.key }}}}\n", save)
        self.assertIn("if-no-files-found: ignore\n", save)
        self.assertIn("retention-days: 7\n", save)
        self.assertIn("overwrite: true\n", save)
        self.assertNotRegex(save, r"secrets\.|github\.token|TOKEN")
        self.assertEqual(e2e.count("db_artifact"), 1)
        # Every read of the plan's answer survives a run without one.
        for job in ("e2e", "db-reuse"):
            reads = re.findall(
                r"needs\.changes\.outputs\.db\b(?!_).{0,8}", self.job(job)
            )
            self.assertTrue(reads, job)
            for read in reads:
                self.assertIn(
                    read,
                    (
                        "needs.changes.outputs.db || '{}'",
                        "needs.changes.outputs.db || '', ",
                    ),
                    job,
                )

    def test_a_job_nothing_waits_for_counts_each_restoring_runs_legs(self):
        job = self.job("db-reuse")
        self.assertIn("    needs: [changes, e2e]\n", job)
        self.assertIn(
            "    if: ${{ !cancelled() && contains(needs.changes.outputs.db || '',"
            """ '"restore":true') }}\n""",
            job,
        )
        # A job's own condition cannot read its matrix: GitHub would refuse
        # the whole file. Each step checks its stack instead.
        condition = re.search(r"\n    if: (.*)\n", job).group(1)
        self.assertNotIn("matrix.", condition)
        for name in (
            "Download each leg's database result",
            "Count what each leg did with the saved database",
        ):
            self.assertIn(
                f"if: ${{{{ {self.MINE}.restore == true }}}}\n", self.step(job, name)
            )
        # The workflow's own read-only permissions; no token is used.
        self.assertNotIn("permissions:", job)
        self.assertNotRegex(job, r"secrets\.|github\.token|TOKEN")
        self.assertIn("permissions:\n  contents: read\n\n", self.text)
        self.assertIn("pattern: db-reuse-${{ matrix.stack }}-*\n", job)
        self.assertIn(
            'e2e_db.py summary "$RUNNER_TEMP/db-reuse" "$STACK" "$SHARD_TOTAL"'
            ' | tee -a "$GITHUB_STEP_SUMMARY"',
            job,
        )
        # No gate reads it.
        self.assertNotRegex(self.text, r"needs: \[[^]]*db-reuse")

    def test_no_job_gained_a_permission_or_a_dependency(self):
        self.assertIn("    needs: [changes, lock-wheels]\n", self.job("e2e"))
        self.assertNotIn("actions: read", self.job("e2e"))


if __name__ == "__main__":
    unittest.main()
