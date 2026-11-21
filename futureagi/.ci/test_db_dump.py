"""The migrated-database dump: its cache key, its fail-closed restore and the control compare."""

import gzip
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "db_dump", ROOT / "futureagi/.ci/db_dump.py"
)
db_dump = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(db_dump)

DUMP = textwrap.dedent(
    """\
    --
    -- PostgreSQL database dump
    --

    \\restrict {token}

    -- Dumped from database version 16.4
    SET statement_timeout = 0;
    CREATE TABLE public.django_migrations (
        id bigint NOT NULL,
        app character varying(255) NOT NULL{extra_column}
    );
    COPY public.django_migrations (id, app) FROM stdin;
    {rows}
    \\.
    ALTER TABLE ONLY public.django_migrations
        ADD CONSTRAINT django_migrations_pkey PRIMARY KEY (id);

    \\unrestrict {token}

    --
    -- PostgreSQL database dump complete
    --
    """
)


def sql(token="tokenA", rows=("1\taccounts", "2\ttracer"), extra_column=""):
    return DUMP.format(token=token, rows="\n".join(rows), extra_column=extra_column)


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="db-dump-test-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)

    def gz(self, name, text):
        path = self.dir / name
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(text)
        return path


class CompareTest(Workspace):
    def test_two_correct_migrates_are_equal_despite_tokens_comments_and_row_values(
        self,
    ):
        cached = self.gz("cached.sql.gz", sql())
        fresh = self.gz(
            "fresh.sql.gz",
            sql(token="tokenB", rows=("7\taccounts", "9\ttracer")).replace(
                "16.4", "16.5"
            ),
        )
        self.assertEqual(db_dump.compare(cached, fresh), [])

    def test_a_schema_difference_is_reported(self):
        cached = self.gz("cached.sql.gz", sql())
        fresh = self.gz("fresh.sql.gz", sql(extra_column=",\n    name text"))
        difference = "\n".join(db_dump.compare(cached, fresh))
        self.assertIn("+    name text", difference)

    def test_a_different_row_count_is_reported(self):
        cached = self.gz("cached.sql.gz", sql())
        fresh = self.gz(
            "fresh.sql.gz", sql(rows=("1\taccounts", "2\ttracer", "3\tmodel_hub"))
        )
        difference = "\n".join(db_dump.compare(cached, fresh))
        self.assertIn("-<2 rows>", difference)
        self.assertIn("+<3 rows>", difference)

    def test_an_unfinished_or_unreadable_dump_is_refused(self):
        complete = self.gz("complete.sql.gz", sql())
        truncated = self.gz("truncated.sql.gz", sql().split("\\unrestrict", 1)[0])
        with self.assertRaisesRegex(db_dump.DumpError, "incomplete"):
            db_dump.compare(truncated, complete)
        with self.assertRaisesRegex(db_dump.DumpError, "incomplete"):
            db_dump.compare(complete, truncated)
        plain = self.dir / "plain.sql.gz"
        plain.write_text(sql())
        with self.assertRaisesRegex(db_dump.DumpError, "not a readable gzip"):
            db_dump.read_dump(plain)
        cut = self.dir / "cut.sql.gz"
        cut.write_bytes(complete.read_bytes()[:-20])
        with self.assertRaisesRegex(db_dump.DumpError, "not a readable gzip"):
            db_dump.read_dump(cut)
        with self.assertRaisesRegex(db_dump.DumpError, "COPY block has no end"):
            db_dump.normalise("COPY public.t (id) FROM stdin;\n1\n")

    def test_the_command_line_fails_on_a_difference_and_says_what_to_do(self):
        cached = self.gz("cached.sql.gz", sql())
        fresh = self.gz("fresh.sql.gz", sql(extra_column=",\n    name text"))
        script = str(ROOT / "futureagi/.ci/db_dump.py")
        same = subprocess.run(
            [sys.executable, "-I", script, "compare", str(cached), str(cached)],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(same.returncode, 0, same.stdout)
        differs = subprocess.run(
            [sys.executable, "-I", script, "compare", str(cached), str(fresh)],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(differs.returncode, 1)
        self.assertIn("Raise VERSION in futureagi/.ci/db_dump.py", differs.stdout)


# Stands in for `docker compose exec -T <db>`: answers pg_dump and psql from a
# script of replies, and logs each call, so the fail-closed paths run offline.
STUB = textwrap.dedent(
    """\
    import json, pathlib, sys
    state = pathlib.Path(sys.argv[1])
    plan = json.loads(state.read_text())
    args = sys.argv[2:]
    plan["calls"].append(args)
    kind = args[0]
    if kind == "pg_dump":
        sys.stdout.write(plan["dump_output"])
        code = plan["dump_exit"]
    elif "--single-transaction" in args:
        plan["loaded"] = sys.stdin.read()
        code = plan["load_exit"]
    else:
        reply = plan["replies"].pop(0)
        sys.stdout.write(reply[0])
        code = reply[1]
    state.write_text(json.dumps(plan))
    sys.exit(code)
    """
)


class RestoreAndDumpTest(Workspace):
    def stub(self, **plan):
        import json

        script = self.dir / "stub.py"
        script.write_text(STUB)
        state = self.dir / "state.json"
        defaults = {
            "calls": [],
            "dump_output": sql(),
            "dump_exit": 0,
            "load_exit": 0,
            "replies": [["0\n", 0], ["412\n", 0]],
            "loaded": None,
        }
        state.write_text(json.dumps({**defaults, **plan}))
        return [sys.executable, str(script), str(state)], lambda: json.loads(
            state.read_text()
        )

    def test_a_complete_dump_restores_into_an_empty_database(self):
        prefix, state = self.stub()
        applied = db_dump.restore(self.gz("dump.sql.gz", sql()), "user", "db", prefix)
        self.assertEqual(applied, 412)
        load = [call for call in state()["calls"] if "--single-transaction" in call]
        self.assertEqual(len(load), 1)
        self.assertIn("ON_ERROR_STOP=1", load[0])
        self.assertEqual(state()["loaded"], sql())

    def test_an_incomplete_dump_never_reaches_the_database(self):
        prefix, state = self.stub()
        with self.assertRaisesRegex(db_dump.DumpError, "incomplete"):
            db_dump.restore(
                self.gz("dump.sql.gz", sql().split("\\unrestrict", 1)[0]),
                "user",
                "db",
                prefix,
            )
        self.assertEqual(state()["calls"], [])

    def test_a_database_that_is_not_empty_is_refused(self):
        for replies in ([["3\n", 0]], [["", 2]]):
            prefix, state = self.stub(replies=replies)
            with self.assertRaisesRegex(
                db_dump.DumpError, "not an empty, reachable database"
            ):
                db_dump.restore(self.gz("dump.sql.gz", sql()), "user", "db", prefix)
            self.assertIsNone(state()["loaded"])

    def test_a_failed_load_or_an_empty_result_fails_the_restore(self):
        prefix, _ = self.stub(load_exit=3)
        with self.assertRaisesRegex(db_dump.DumpError, "restore failed"):
            db_dump.restore(self.gz("dump.sql.gz", sql()), "user", "db", prefix)
        prefix, _ = self.stub(replies=[["0\n", 0], ["0\n", 0]])
        with self.assertRaisesRegex(db_dump.DumpError, "no applied migration"):
            db_dump.restore(self.gz("dump.sql.gz", sql()), "user", "db", prefix)

    def test_dump_writes_only_a_complete_file(self):
        prefix, state = self.stub()
        target = self.dir / "out" / "dump.sql.gz"
        db_dump.dump(target, "user", "db", prefix)
        self.assertEqual(db_dump.read_dump(target), sql())
        self.assertEqual(
            state()["calls"][0],
            ["pg_dump", "-U", "user", "--no-owner", "--no-privileges", "db"],
        )
        for plan in (
            {"dump_exit": 1},
            {"dump_output": sql().split("\\unrestrict", 1)[0]},
        ):
            prefix, _ = self.stub(**plan)
            failed = self.dir / "failed" / "dump.sql.gz"
            with self.assertRaises(db_dump.DumpError):
                db_dump.dump(failed, "user", "db", prefix)
            self.assertFalse(failed.exists())


class KeyTest(Workspace):
    def repo(self):
        root = self.dir / "repo"
        files = {
            "futureagi/accounts/migrations/0001_initial.py": "# one\n",
            "futureagi/ee/usage/migrations/0001_initial.py": "# ee\n",
            "futureagi/uv.lock": "lock\n",
            "futureagi/tfc/settings/test.py": "settings\n",
            "futureagi/conftest.py": "conftest\n",
            "futureagi/pytest.ini": "[pytest]\n",
            "futureagi/docker-compose.test.yml": "image: postgres:16-alpine\n",
            "futureagi/.ci/db_dump.py": "tool\n",
            "futureagi/Dockerfile.oss": "FROM python\n",
            "futureagi/entrypoint.sh": "migrate\n",
            "docker-compose.distributed.yml": "image: postgres\n",
            "e2e/stack/e2e.env": "A=1\n",
            "futureagi/accounts/views.py": "view\n",
        }
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git(root, "init", "-q")
        self.commit(root)
        return root

    def git(self, cwd, *args):
        subprocess.run(
            [
                "git",
                "-C",
                str(cwd),
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def commit(self, root):
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "--no-verify", "-m", "state")

    def test_the_key_follows_the_migrations_and_nothing_untracked(self):
        root = self.repo()
        key = db_dump.cache_key("pytest", root)
        self.assertRegex(key, r"^migrated-db-pytest-v\d+-[0-9a-f]{40}$")
        self.assertEqual(db_dump.cache_key("pytest", root), key)
        # Not an input: ordinary code, a virtualenv, bytecode beside a migration.
        (root / "futureagi/accounts/views.py").write_text("changed\n")
        self.commit(root)
        (root / "futureagi/.venv/lib/django/migrations").mkdir(parents=True)
        (root / "futureagi/.venv/lib/django/migrations/0001_initial.py").write_text(
            "vendored\n"
        )
        (root / "futureagi/accounts/migrations/__pycache__").mkdir()
        (
            root / "futureagi/accounts/migrations/__pycache__/0001_initial.pyc"
        ).write_text("bytecode\n")
        self.assertEqual(db_dump.cache_key("pytest", root), key)
        # Inputs: an edited migration, a new migration, the lock file.
        seen = {key}
        (root / "futureagi/accounts/migrations/0001_initial.py").write_text(
            "# edited\n"
        )
        self.commit(root)
        seen.add(db_dump.cache_key("pytest", root))
        (root / "futureagi/tracer/migrations").mkdir(parents=True)
        (root / "futureagi/tracer/migrations/0001_initial.py").write_text("# new\n")
        self.commit(root)
        seen.add(db_dump.cache_key("pytest", root))
        (root / "futureagi/uv.lock").write_text("lock 2\n")
        self.commit(root)
        seen.add(db_dump.cache_key("pytest", root))
        self.assertEqual(len(seen), 4)

    def test_each_consumer_and_each_overlay_state_has_its_own_key(self):
        root = self.repo()
        pytest_key = db_dump.cache_key("pytest", root)
        self.assertNotEqual(pytest_key, db_dump.cache_key("e2e-distributed", root))
        overlay = root / "futureagi/ee/cloud"
        (overlay / "control_plane/migrations").mkdir(parents=True)
        (overlay / "control_plane/migrations/0001_initial.py").write_text("# overlay\n")
        self.git(overlay, "init", "-q")
        self.commit(overlay)
        with_overlay = db_dump.cache_key("pytest", root)
        self.assertNotEqual(with_overlay, pytest_key)
        (overlay / "control_plane/migrations/0002_more.py").write_text("# overlay 2\n")
        self.commit(overlay)
        self.assertNotEqual(db_dump.cache_key("pytest", root), with_overlay)

    def test_a_missing_input_or_an_unknown_consumer_is_an_error(self):
        root = self.repo()
        (root / "futureagi/uv.lock").unlink()
        self.commit(root)
        with self.assertRaisesRegex(db_dump.DumpError, "matches no tracked file"):
            db_dump.cache_key("pytest", root)
        with self.assertRaisesRegex(db_dump.DumpError, "unknown flavour"):
            db_dump.cache_key("mysql", root)

    def test_every_key_input_exists_in_this_repository(self):
        for flavour in db_dump.INPUTS:
            self.assertRegex(
                db_dump.cache_key(flavour),
                rf"^migrated-db-{flavour}-v{db_dump.VERSION}-",
            )


class BackendWorkflowTest(unittest.TestCase):
    """Wiring that keeps the dump safe: restore only on an exact hit, and one from-zero migrate per run."""

    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()

    def job(self, start, end):
        return self.workflow.split(f"  {start}:\n", 1)[1].split(f"\n  {end}:", 1)[0]

    def step(self, job, name):
        return job.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]

    def test_a_shard_restores_only_on_an_exact_hit_and_before_pytest(self):
        job = self.job("test", "saml-multiworker")
        lookup = self.step(job, "Look for a dump of the migrated database")
        self.assertIn("uses: actions/cache/restore@", lookup)
        self.assertIn("key: ${{ steps.db-key.outputs.key }}", lookup)
        self.assertNotRegex(job, r"(?m)^\s+restore-keys:")
        # A shard never writes the dump: only the from-zero job does.
        self.assertNotIn("actions/cache/save@", job)
        restore = self.step(job, "Restore the migrated database (exact key hit only)")
        self.assertIn("if: steps.db-dump.outputs.cache-hit == 'true'\n", restore)
        self.assertIn("db_dump.py restore", restore)
        self.assertNotIn("continue-on-error", job)
        self.assertNotRegex(restore, r"\|\|")
        self.assertLess(
            job.index("- name: Gate on service health"),
            job.index("- name: Restore the migrated database"),
        )
        self.assertLess(
            job.index("- name: Restore the migrated database"),
            job.index("- name: Run shard"),
        )

    def test_one_job_migrates_from_zero_on_every_run_and_gates_the_merge(self):
        job = self.job("migrated-database", "agentic-eval")
        migrate = self.step(job, "Migrate an empty test database from zero")
        self.assertNotIn("if:", migrate)
        self.assertIn("--create-db", migrate)
        self.assertNotIn("db_dump.py restore", job)
        compare = self.step(job, "The cached dump equals this fresh migrate")
        self.assertIn("if: steps.db-dump.outputs.cache-hit == 'true'\n", compare)
        self.assertIn("db_dump.py compare", compare)
        save = self.step(job, "Save the dump for later runs")
        self.assertIn("if: steps.db-dump.outputs.cache-hit != 'true'\n", save)
        self.assertIn("uses: actions/cache/save@", save)
        self.assertNotIn("continue-on-error", job)
        self.assertLess(
            job.index("- name: Migrate an empty test database"),
            job.index("- name: Dump the fresh database"),
        )
        self.assertLess(
            job.index("- name: Dump the fresh database"),
            job.index("- name: Save the dump for later runs"),
        )
        gate = self.workflow.split("  backend-tests:\n", 1)[1]
        self.assertIn("        migrated-database,\n", gate.split("    if:", 1)[0])

    def test_the_from_zero_job_creates_its_database_as_a_shard_does(self):
        shard = self.job("test", "saml-multiworker")
        control = self.job("migrated-database", "agentic-eval")
        for line in (
            "      LITELLM_MODE: PRODUCTION\n",
            "      CH25_DATABASE: test_tfc\n",
            "      CH_DATABASE: test_tfc\n",
            '      PYTHONHASHSEED: "0"\n',
            '      FI_CH25_SCHEMA_APPLY_STRICT: "1"\n',
            "        uses: ./.github/actions/checkout-ee\n",
            "        run: uv sync --frozen --all-extras\n",
        ):
            self.assertIn(line, shard, line)
            self.assertIn(line, control, line)
        self.assertIn("key=$(python3 -I .ci/db_dump.py key pytest)", shard)
        self.assertIn("key=$(python3 -I .ci/db_dump.py key pytest)", control)


if __name__ == "__main__":
    unittest.main()
