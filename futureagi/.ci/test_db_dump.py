"""The migrated-database dump: its cache key, its fail-closed restore and the control compare."""

import gzip
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "futureagi/.ci/db_dump.py"
SPEC = importlib.util.spec_from_file_location("db_dump", SCRIPT)
db_dump = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(db_dump)

# The two parts pg_dump writes: the schema, then the rows of django_migrations.
SCHEMA = textwrap.dedent(
    """\
    --
    -- PostgreSQL database dump
    --

    \\restrict {token}

    -- Dumped from database version {version}
    -- Dumped by pg_dump version {version}

    SET statement_timeout = 0;

    --
    -- Name: touch(); Type: FUNCTION; Schema: public; Owner: -
    --

    CREATE FUNCTION public.touch() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
    BEGIN
    {body}
      RETURN NEW;
    END
    $$;

    CREATE TABLE public.django_migrations (
        id bigint NOT NULL,
        app character varying(255) NOT NULL{extra_column}
    );

    \\unrestrict {token}

    --
    -- PostgreSQL database dump complete
    --

    """
)
DATA = textwrap.dedent(
    """\
    --
    -- PostgreSQL database dump
    --

    \\restrict {token}

    -- Dumped from database version {version}

    COPY public.django_migrations (id, app) FROM stdin;
    {rows}
    \\.

    SELECT pg_catalog.setval('public.django_migrations_id_seq', {count}, true);

    \\unrestrict {token}

    --
    -- PostgreSQL database dump complete
    --

    """
)
KEEP = "  -- keep the row's stamp"
ROWS = ("1\taccounts\t2026-11-21", "2\ttracer\t2026-11-21")


def schema(token="tokenA", version="16.4", extra_column="", body=KEEP):
    return SCHEMA.format(
        token=token, version=version, extra_column=extra_column, body=body
    )


def data(token="tokenA", version="16.4", rows=ROWS):
    return DATA.format(
        token=token, version=version, rows="\n".join(rows), count=len(rows)
    )


def sql(**changes):
    schema_args = {
        k: changes[k]
        for k in ("token", "version", "extra_column", "body")
        if k in changes
    }
    data_args = {k: changes[k] for k in ("token", "version", "rows") if k in changes}
    return schema(**schema_args) + data(**data_args)


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="db-dump-test-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)

    def gz(self, name, text):
        path = self.dir / name
        with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path


class CompareTest(Workspace):
    def test_two_correct_migrates_are_equal_despite_tokens_versions_and_times(self):
        cached = self.gz("cached.sql.gz", sql())
        later = ("1\taccounts\t2026-12-01", "2\ttracer\t2026-12-01")
        fresh = self.gz("fresh.sql.gz", sql(token="tokenB", version="16.5", rows=later))
        self.assertEqual(db_dump.compare(cached, fresh), [])

    def test_a_schema_difference_is_reported(self):
        cached = self.gz("cached.sql.gz", sql())
        fresh = self.gz("fresh.sql.gz", sql(extra_column=",\n    name text"))
        self.assertIn("+    name text", "\n".join(db_dump.compare(cached, fresh)))

    def test_a_different_number_of_applied_migrations_is_reported(self):
        cached = self.gz("cached.sql.gz", sql())
        more = ("1\taccounts\tx", "2\ttracer\tx", "3\tmodel_hub\tx")
        fresh = self.gz("fresh.sql.gz", sql(rows=more))
        difference = "\n".join(db_dump.compare(cached, fresh))
        self.assertIn("-<2 rows>", difference)
        self.assertIn("+<3 rows>", difference)

    def test_lines_that_only_look_like_comments_or_blanks_are_compared(self):
        # Inside a function body a line starting with "--" is code under
        # review, and so is a blank line of a multi-line literal.
        cached = self.gz("cached.sql.gz", sql())
        comment = self.gz("comment.sql.gz", sql(body="  -- skip the row's stamp"))
        self.assertIn(
            "+  -- skip the row's stamp", "\n".join(db_dump.compare(cached, comment))
        )
        blank = self.gz("blank.sql.gz", sql(body=KEEP + "\n"))
        self.assertNotEqual(db_dump.compare(cached, blank), [])

    def test_a_row_with_an_unusual_line_separator_counts_once(self):
        # str.splitlines() would split these; pg_dump writes them raw.
        odd = self.gz("odd.sql.gz", sql(rows=("1\ta b\tx", "2\tc\x85d\tx")))
        self.assertEqual(db_dump.compare(self.gz("cached.sql.gz", sql()), odd), [])

    def test_an_unfinished_or_unreadable_dump_is_refused(self):
        complete = self.gz("complete.sql.gz", sql())
        cut_data = data().split("\\unrestrict", 1)[0]
        cut_schema = schema().split("\\unrestrict", 1)[0]
        for name, text in (
            ("schema-only.sql.gz", schema()),
            ("cut-data.sql.gz", schema() + cut_data),
            ("cut-schema.sql.gz", cut_schema + data()),
            ("empty.sql.gz", ""),
            ("three-parts.sql.gz", sql() + data()),
        ):
            broken = self.gz(name, text)
            with self.assertRaisesRegex(db_dump.DumpError, "incomplete", msg=name):
                db_dump.compare(broken, complete)
            with self.assertRaisesRegex(db_dump.DumpError, "incomplete", msg=name):
                db_dump.compare(complete, broken)
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
        same = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "compare", str(cached), str(cached)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(same.returncode, 0, same.stdout)
        differs = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "compare", str(cached), str(fresh)],
            capture_output=True,
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
        part = "data" if "--data-only" in args else "schema"
        sys.stdout.write(plan[part + "_output"])
        code = plan[part + "_exit"]
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
PG_DUMP = ["pg_dump", "-U", "user", "--no-owner", "--no-privileges"]


class RestoreAndDumpTest(Workspace):
    def stub(self, **plan):
        script = self.dir / "stub.py"
        script.write_text(STUB)
        state = self.dir / "state.json"
        defaults = {
            "calls": [],
            "schema_output": schema(),
            "schema_exit": 0,
            "data_output": data(),
            "data_exit": 0,
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
        # psql refuses --single-transaction without -f or -c; "-f -" is the script on stdin.
        self.assertEqual(
            load[0][load[0].index("--single-transaction") :],
            ["--single-transaction", "-f", "-"],
        )
        self.assertEqual(state()["loaded"], sql())

    def test_an_incomplete_dump_never_reaches_the_database(self):
        prefix, state = self.stub()
        with self.assertRaisesRegex(db_dump.DumpError, "incomplete"):
            db_dump.restore(self.gz("dump.sql.gz", schema()), "user", "db", prefix)
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

    def test_the_dump_is_the_schema_and_the_applied_migrations_and_no_other_row(self):
        prefix, state = self.stub()
        target = self.dir / "out" / "dump.sql.gz"
        db_dump.dump(target, "user", "db", prefix)
        self.assertEqual(db_dump.read_dump(target), sql())
        self.assertEqual(
            state()["calls"],
            [
                [*PG_DUMP, "--schema-only", "db"],
                [*PG_DUMP, "--data-only", "--table=django_migrations", "db"],
            ],
        )

    def test_dump_writes_only_a_complete_file(self):
        for plan in (
            {"schema_exit": 1},
            {"data_exit": 1},
            {"data_output": data().split("\\unrestrict", 1)[0]},
            {"schema_output": schema().split("\\unrestrict", 1)[0]},
        ):
            prefix, _ = self.stub(**plan)
            failed = self.dir / "failed" / "dump.sql.gz"
            with self.assertRaises(db_dump.DumpError, msg=plan):
                db_dump.dump(failed, "user", "db", prefix)
            self.assertFalse(failed.exists(), plan)

    def test_the_command_line_passes_a_prefix_with_options_of_its_own(self):
        # CI's prefix is `docker compose -f <file> -p <project> exec -T <service>`.
        prefix, state = self.stub()
        prefix.insert(1, "-I")
        target = self.dir / "cli" / "dump.sql.gz"
        tool = [sys.executable, "-I", str(SCRIPT)]
        dumped = subprocess.run(
            [*tool, "dump", str(target), "user", "db", *prefix],
            capture_output=True,
            text=True,
        )
        self.assertEqual(dumped.returncode, 0, dumped.stdout + dumped.stderr)
        self.assertEqual(db_dump.read_dump(target), sql())
        restored = subprocess.run(
            [*tool, "restore", str(target), "user", "db", *prefix],
            capture_output=True,
            text=True,
        )
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertIn("412 applied migrations", restored.stdout)
        self.assertEqual(state()["loaded"], sql())
        missing = subprocess.run(
            [*tool, "restore", str(target), "user", "db"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing.returncode, 2)


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
            "futureagi/.ci/migrated_db.py": "producer\n",
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
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
        subprocess.run(
            ["git", "-C", str(cwd), *identity, "-c", "commit.gpgsign=false", *args],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def commit(self, root):
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "--no-verify", "-m", "state")

    def edit(self, root, name, text):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_the_key_follows_the_migrations_and_nothing_untracked(self):
        root = self.repo()
        key = db_dump.cache_key(root)
        self.assertRegex(key, r"^migrated-db-pytest-v\d+-[0-9a-f]{40}$")
        self.assertEqual(db_dump.cache_key(root), key)
        # Not an input: ordinary code, a virtualenv, bytecode beside a migration.
        self.edit(root, "futureagi/accounts/views.py", "changed\n")
        self.commit(root)
        self.edit(root, "futureagi/.venv/lib/django/migrations/0001_initial.py", "x\n")
        self.edit(
            root, "futureagi/accounts/migrations/__pycache__/0001_initial.pyc", "x\n"
        )
        self.assertEqual(db_dump.cache_key(root), key)
        # Inputs: an edited migration, a new migration, the lock file, the producer.
        seen = {key}
        for name in (
            "futureagi/accounts/migrations/0001_initial.py",
            "futureagi/tracer/migrations/0001_initial.py",
            "futureagi/uv.lock",
            "futureagi/.ci/migrated_db.py",
        ):
            self.edit(root, name, "edited\n")
            self.commit(root)
            seen.add(db_dump.cache_key(root))
        self.assertEqual(len(seen), 5)

    def test_a_checkout_with_the_private_package_has_its_own_key(self):
        root = self.repo()
        without = db_dump.cache_key(root)
        overlay = root / "futureagi/ee/cloud"
        self.edit(overlay, "control_plane/models.py", "# overlay\n")
        self.git(overlay, "init", "-q")
        self.commit(overlay)
        with_overlay = db_dump.cache_key(root)
        self.assertNotEqual(with_overlay, without)
        # Its content is not an input: the test settings do not install it,
        # and no row it could seed is in the dump.
        self.edit(overlay, "control_plane/models.py", "# overlay 2\n")
        self.commit(overlay)
        self.assertEqual(db_dump.cache_key(root), with_overlay)

    def test_a_missing_input_is_an_error(self):
        root = self.repo()
        (root / "futureagi/uv.lock").unlink()
        self.commit(root)
        with self.assertRaisesRegex(db_dump.DumpError, "matches no tracked file"):
            db_dump.cache_key(root)

    def test_every_key_input_exists_in_this_repository(self):
        self.assertRegex(
            db_dump.cache_key(), rf"^migrated-db-pytest-v{db_dump.VERSION}-"
        )


class BackendWorkflowTest(unittest.TestCase):
    """Wiring that keeps the dump safe: restore only on an exact hit, and one from-zero migrate per run."""

    PRODUCER = "uv run pytest .ci/migrated_db.py"

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
        gate = job.index("- name: Gate on service health")
        restored = job.index("- name: Restore the migrated database")
        self.assertLess(gate, restored)
        self.assertLess(restored, job.index("- name: Run shard"))

    def test_a_shard_without_a_dump_migrates_before_its_measured_run(self):
        job = self.job("test", "saml-multiworker")
        name = "Migrate the test database from zero (no dump for this key)"
        migrate = self.step(job, name)
        self.assertIn("if: steps.db-dump.outputs.cache-hit != 'true'\n", migrate)
        self.assertIn(self.PRODUCER, migrate)
        self.assertLess(job.index(f"- name: {name}"), job.index("- name: Run shard"))

    def test_one_job_migrates_from_zero_on_every_run_and_gates_the_merge(self):
        job = self.job("migrated-database", "agentic-eval")
        migrate = self.step(job, "Migrate an empty test database from zero")
        self.assertNotIn("if:", migrate)
        self.assertIn(self.PRODUCER + " --create-db", migrate)
        self.assertNotIn("db_dump.py restore", job)
        compare = self.step(job, "The cached dump equals this fresh migrate")
        self.assertIn("if: steps.db-dump.outputs.cache-hit == 'true'\n", compare)
        self.assertIn("db_dump.py compare", compare)
        save = self.step(job, "Save the dump for later runs")
        self.assertIn("if: steps.db-dump.outputs.cache-hit != 'true'\n", save)
        self.assertIn("uses: actions/cache/save@", save)
        self.assertNotIn("continue-on-error", job)
        migrated = job.index("- name: Migrate an empty test database")
        dumped = job.index("- name: Dump the fresh database")
        self.assertLess(migrated, dumped)
        self.assertLess(dumped, job.index("- name: Save the dump for later runs"))
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
        self.assertIn("key=$(python3 -I .ci/db_dump.py key)", shard)
        self.assertIn("key=$(python3 -I .ci/db_dump.py key)", control)

    def test_the_producer_sets_up_the_database_and_runs_no_test_against_it(self):
        # A case that used the database would truncate or roll back; the dump
        # must be what migrate left.
        source = (ROOT / "futureagi/.ci/migrated_db.py").read_text()
        self.assertIn("(django_db_setup):", source)
        for word in ("transactional_db", "mark.django_db", "(db)", "(db,"):
            self.assertNotIn(word, source)
        self.assertIn("futureagi/.ci/migrated_db.py", db_dump.INPUTS)

    def test_the_e2e_stack_does_not_restore_a_dump(self):
        # Its Postgres is shared with Temporal, and a migration registers
        # Temporal search attributes: a restored database would skip that.
        e2e = (ROOT / ".github/workflows/e2e-ci.yml").read_text()
        self.assertNotIn("db_dump.py", e2e)


if __name__ == "__main__":
    unittest.main()
