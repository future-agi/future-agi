"""The migrated-database dump: its cache key, its fail-closed restore and the control compare."""

import gzip
import importlib.util
import json
import os
import pathlib
import shutil
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

# One pg_dump of the migrated database: schema, every row, every sequence.
# The sample holds no schema statement on purpose: the update-safety gate
# scans every source file for them and asks for a reviewed declaration. The
# tool compares lines, so any multi-line statement exercises it.
DUMP = textwrap.dedent(
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

    DO $$
    BEGIN
    {body}
      NULL;
    END
    $$;

    SELECT
        id,
        app{extra_column}
    FROM public.django_migrations;

    COPY public.django_migrations (id, app, name, applied) FROM stdin;
    {rows}
    \\.

    COPY public.some_seed (id, created_at, slug) FROM stdin;
    {seeds}
    \\.

    COPY public.empty_table (id) FROM stdin;
    \\.

    SELECT pg_catalog.setval('public.django_migrations_id_seq', {count}, true);

    \\unrestrict {token}

    --
    -- PostgreSQL database dump complete
    --

    """
)
KEEP = "  -- keep the row's stamp"
ROWS = ("1\taccounts\t0001\t2026-11-21", "2\ttracer\t0002\t2026-11-21")
# Rows a migration seeds: a generated id and write time, then real content.
SEEDS = (
    "6f1c2a3b-1111-2222-3333-444455556666\t2026-11-27 11:08:40.123456+00\tcluster-rca",
    "0a1b2c3d-aaaa-bbbb-cccc-ddddeeeeffff\t2026-11-27 11:08:40.2+00\tdebug-traces",
)
# The same content, migrated again: other ids, other times.
SEEDS_AGAIN = (
    "99999999-8888-7777-6666-555544443333\t2026-12-01 09:00:00+00\tdebug-traces",
    "12345678-1234-1234-1234-123456789abc\t2026-12-01 09:00:01.5+00\tcluster-rca",
)


def sql(
    token="tokenA",
    version="16.4",
    extra_column="",
    body=KEEP,
    rows=ROWS,
    seeds=SEEDS,
):
    return DUMP.format(
        token=token,
        version=version,
        extra_column=extra_column,
        body=body,
        rows="\n".join(rows),
        count=len(rows),
        seeds="\n".join(seeds),
    )


def cut(text):
    return text.split("\\unrestrict", 1)[0]


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
    def test_two_correct_migrates_are_equal_despite_tokens_order_ids_and_times(self):
        cached = self.gz("cached.sql.gz", sql())
        later = ("1\taccounts\t0001\t2026-12-01", "2\ttracer\t0002\t2026-12-01")
        fresh = self.gz(
            "fresh.sql.gz", sql(token="tokenB", rows=later[::-1], seeds=SEEDS_AGAIN)
        )
        self.assertEqual(db_dump.compare(cached, fresh), [])

    def test_a_schema_difference_is_reported(self):
        cached = self.gz("cached.sql.gz", sql())
        fresh = self.gz("fresh.sql.gz", sql(extra_column=",\n    name"))
        self.assertIn("+    name", "\n".join(db_dump.compare(cached, fresh)))

    def test_a_different_number_of_applied_migrations_is_reported(self):
        cached = self.gz("cached.sql.gz", sql())
        more = (*ROWS, "3\tmodel_hub\t0001\tx")
        fresh = self.gz("fresh.sql.gz", sql(rows=more))
        self.assertIn("+model_hub\t0001", "\n".join(db_dump.compare(cached, fresh)))

    def test_seeded_content_is_compared_with_only_generated_values_masked(self):
        cached = self.gz("cached.sql.gz", sql())
        renamed = (SEEDS[0], SEEDS[1].replace("debug-traces", "debug-spans"))
        fresh = self.gz("fresh.sql.gz", sql(seeds=renamed))
        self.assertIn(
            "+<uuid>\t<time>\tdebug-spans", "\n".join(db_dump.compare(cached, fresh))
        )
        missing = self.gz("missing.sql.gz", sql(seeds=SEEDS[:1]))
        self.assertTrue(db_dump.compare(cached, missing))
        twice = self.gz("twice.sql.gz", sql(seeds=(*SEEDS, SEEDS_AGAIN[0])))
        self.assertTrue(db_dump.compare(cached, twice))
        # Only a whole field is a generated value, never part of real content.
        inside = (
            SEEDS[0],
            SEEDS[1].replace("debug-traces", "id 0a1b2c3d-aaaa-bbbb-cccc-ddddeeeeffff"),
        )
        moved = (
            SEEDS[0],
            SEEDS[1].replace("debug-traces", "id 99999999-aaaa-bbbb-cccc-ddddeeeeffff"),
        )
        self.assertTrue(
            db_dump.compare(
                self.gz("inside.sql.gz", sql(seeds=inside)),
                self.gz("moved.sql.gz", sql(seeds=moved)),
            )
        )

    def test_same_count_different_identity_or_tool_version_is_not_a_match(self):
        cached = self.gz("cached.gz", sql())
        self.assertTrue(db_dump.compare(cached, self.gz("new.gz", sql(version="16.5"))))
        wrong = (ROWS[0], ROWS[1].replace("0002", "9999"))
        self.assertTrue(db_dump.compare(cached, self.gz("wrong.gz", sql(rows=wrong))))

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

    def test_unusual_line_separator_in_the_timestamp_is_not_a_row_boundary(self):
        odd = tuple(row + " x" for row in ROWS)
        self.assertEqual(
            db_dump.compare(self.gz("base", sql()), self.gz("odd", sql(rows=odd))), []
        )

    def test_an_unfinished_or_unreadable_dump_is_refused(self):
        complete = self.gz("complete.sql.gz", sql())
        for name, text in (
            ("cut.sql.gz", cut(sql())),
            ("empty.sql.gz", ""),
            ("two-dumps.sql.gz", sql() + sql()),
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
        truncated = self.dir / "truncated.sql.gz"
        truncated.write_bytes(complete.read_bytes()[:-20])
        with self.assertRaisesRegex(db_dump.DumpError, "not a readable gzip"):
            db_dump.read_dump(truncated)
        for invalid in (
            "COPY public.t (id) FROM stdin;\n1\n",
            db_dump.LEDGER + "\n1\ta\tb\tt\n",
            sql(rows=(ROWS[0], ROWS[0])),
            sql(rows=("one-field",)),
            sql(rows=()),
            sql().replace(db_dump.LEDGER, "COPY public.other (id) FROM stdin;"),
            sql().replace("COPY public.empty_table (id) FROM stdin;", db_dump.LEDGER),
        ):
            with self.assertRaises(db_dump.DumpError):
                db_dump.normalise(invalid)

    def test_tables_names_only_tables_that_hold_rows_with_their_counts(self):
        path = self.gz("dump.sql.gz", sql(seeds=(*SEEDS, SEEDS_AGAIN[0])))
        self.assertEqual(
            db_dump.tables(path),
            [("public.django_migrations", 2), ("public.some_seed", 3)],
        )

    def test_the_command_line_fails_on_a_difference_and_says_what_to_do(self):
        cached = self.gz("cached.sql.gz", sql())
        fresh = self.gz("fresh.sql.gz", sql(extra_column=",\n    name"))
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
        self.assertEqual(differs.returncode, db_dump.COMPARE_DIFFERENT)
        self.assertIn(
            "Add it to KEY_FILES or KEY_DIRS, or raise VERSION", differs.stdout
        )
        # The fresh side can hold private schema: never print the diff itself.
        self.assertNotIn("name", differs.stdout.replace("KEY_", ""))

        for broken in (self.dir / "missing", self.gz("invalid.gz", cut(sql()))):
            refused = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(SCRIPT),
                    "compare",
                    str(cached),
                    str(broken),
                ],
                capture_output=True,
            )
            self.assertNotIn(refused.returncode, (0, db_dump.COMPARE_DIFFERENT))


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
PG_DUMP = ["pg_dump", "-U", "user", "--no-owner", "--no-privileges"]


class RestoreAndDumpTest(Workspace):
    def stub(self, **plan):
        script = self.dir / "stub.py"
        script.write_text(STUB)
        state = self.dir / "state.json"
        defaults = {
            "calls": [],
            "dump_output": sql(),
            "dump_exit": 0,
            "load_exit": 0,
            "replies": [["0\n", 0], ["tracer\t0002\naccounts\t0001\n", 0]],
            "loaded": None,
        }
        state.write_text(json.dumps({**defaults, **plan}))
        return [sys.executable, str(script), str(state)], lambda: json.loads(
            state.read_text()
        )

    def test_a_complete_dump_restores_into_an_empty_database(self):
        prefix, state = self.stub()
        applied = db_dump.restore(self.gz("dump.sql.gz", sql()), "user", "db", prefix)
        self.assertEqual(applied, 2)
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
            db_dump.restore(self.gz("dump.sql.gz", cut(sql())), "user", "db", prefix)
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
        with self.assertRaisesRegex(db_dump.DumpError, "identities do not match"):
            db_dump.restore(self.gz("dump.sql.gz", sql()), "user", "db", prefix)

    def test_the_dump_is_one_pg_dump_of_the_whole_database(self):
        prefix, state = self.stub()
        target = self.dir / "out" / "dump.sql.gz"
        db_dump.dump(target, "user", "db", prefix)
        self.assertEqual(db_dump.read_dump(target), sql())
        self.assertEqual(state()["calls"], [[*PG_DUMP, "db"]])

    def test_dump_writes_only_a_complete_file(self):
        for plan in (
            {"dump_exit": 1},
            {"dump_output": cut(sql())},
            {"dump_output": sql() + sql()},
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
        self.assertIn("2 applied migrations", restored.stdout)
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
            "futureagi/.ci/db_dump.py": "tool\n",
            "futureagi/.ci/migrated_db.py": "producer\n",
            "futureagi/.ci/collected_ids.py": "plugin\n",
            "futureagi/accounts/views.py": "view\n",
            "futureagi/accounts/migrations.py": "not a migrations package\n",
            "README.md": "outside the backend\n",
        }
        for name, text in files.items():
            self.edit(root, name, text)
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

    def test_a_change_to_a_migrate_input_misses(self):
        root = self.repo()
        key = db_dump.inputs_key(root)
        self.assertRegex(key, r"^migrated-db-v\d+-[0-9a-f]{40}$")
        for name in (
            "futureagi/accounts/migrations/0002_new.py",
            "futureagi/ee/usage/migrations/0001_initial.py",
            "futureagi/uv.lock",
            "futureagi/tfc/settings/test.py",
            "futureagi/conftest.py",
            "futureagi/.ci/db_dump.py",
            # Data a migration reads before post_migrate.
            "futureagi/ee/falcon_ai/builtin_skills/cluster-rca.yaml",
            "futureagi/ee/falcon_ai/builtin_skills_loader.py",
        ):
            self.edit(root, name, "changed\n")
            self.commit(root)
            self.assertNotEqual(db_dump.inputs_key(root), key, name)
            key = db_dump.inputs_key(root)

    def test_code_migrate_does_not_read_and_uncommitted_files_keep_the_key(self):
        root = self.repo()
        key = db_dump.inputs_key(root)
        for name in (
            "futureagi/accounts/views.py",
            "futureagi/accounts/migrations.py",
            "futureagi/.ci/collected_ids.py",
            "README.md",
        ):
            self.edit(root, name, "changed\n")
            self.commit(root)
            self.assertEqual(db_dump.inputs_key(root), key, name)
        self.edit(root, "futureagi/accounts/migrations/0003_draft.py", "untracked\n")
        self.edit(root, "futureagi/.venv/stale", "ignored")
        self.assertEqual(db_dump.inputs_key(root), key)

    def test_the_private_overlay_is_never_part_of_the_key(self):
        root = self.repo()
        key = db_dump.inputs_key(root)
        overlay = root / "futureagi/ee/cloud"
        self.edit(overlay, "control_plane/migrations/0001_initial.py", "private\n")
        self.git(overlay, "init", "-q")
        self.commit(overlay)
        self.assertEqual(db_dump.inputs_key(root), key)

    def test_the_recipe_is_the_inputs_key_plus_the_runtime(self):
        root = self.repo()
        key = db_dump.inputs_key(root)
        recipe = db_dump.cache_key(root, "pg-image-A|tools-A")
        self.assertRegex(recipe, rf"^{key}-[0-9a-f]{{16}}$")
        self.assertEqual(recipe.rpartition("-")[0], key)
        self.assertEqual(db_dump.cache_key(root, "pg-image-A|tools-A"), recipe)
        self.assertNotEqual(db_dump.cache_key(root, "pg-image-B|tools-A"), recipe)
        with self.assertRaisesRegex(db_dump.DumpError, "runtime"):
            db_dump.cache_key(root, " ")


class SeedParityTest(unittest.TestCase):
    def snapshot(self, rows, sequence=2, table="some_seed"):
        return (
            f"COPY public.{table} (id, value) FROM stdin;\n"
            + "\n".join(rows)
            + "\n\\.\n"
            + f"sequence {sequence}\n"
            + db_dump.COMPLETE
            + "\n"
        ).encode()

    def test_all_seed_tables_values_multiplicity_and_counters_are_compared(self):
        rows = ("1\ta", "2\tb")
        original = db_dump.fingerprint_rows(self.snapshot(rows))
        self.assertEqual(original, db_dump.fingerprint_rows(self.snapshot(rows[::-1])))
        for different in [
            self.snapshot(rows, 3),
            self.snapshot(rows, table="other"),
            self.snapshot((*rows, rows[0])),
            self.snapshot((rows[0], "2\tc")),
        ]:
            self.assertNotEqual(original, db_dump.fingerprint_rows(different))
        with self.assertRaises(db_dump.DumpError):
            db_dump.fingerprint_rows(b"COPY public.t (v) FROM stdin;\nx\n")

    def test_table_and_sequence_order_is_not_data(self):
        # A data-only dump orders tables by foreign keys, ties by object id,
        # and a restore renumbers object ids.
        first = self.snapshot(("1\ta",), table="first")
        second = self.snapshot(("2\tb",), sequence=5, table="second")

        def body(dump):
            return dump.rsplit(db_dump.COMPLETE.encode(), 1)[0]

        end = db_dump.COMPLETE.encode() + b"\n"
        self.assertEqual(
            db_dump.fingerprint_rows(body(first) + body(second) + end),
            db_dump.fingerprint_rows(body(second) + body(first) + end),
        )
        # A row moved to another table is a different database.
        moved = self.snapshot(("2\tb",), table="first")
        self.assertNotEqual(
            db_dump.fingerprint_rows(body(first) + body(second) + end),
            db_dump.fingerprint_rows(
                body(self.snapshot((), table="first"))
                + body(moved)
                + body(self.snapshot(("1\ta",), sequence=5, table="second"))
                + end
            ),
        )


class SetupTest(Workspace):
    def test_service_or_sync_failure_is_not_hidden_and_only_one_up_runs(self):
        script = ROOT / ".github/actions/backend-test-packages/environment/setup.sh"
        for name, contents in {
            "uv": 'exit "$SYNC_STATUS"',
            "docker": 'echo "$*" >> "$RUNNER_TEMP/services"; exit "$HEALTH_STATUS"',
        }.items():
            tool = self.dir / name
            tool.write_text("#!/bin/sh\n" + contents + "\n")
            tool.chmod(0o755)
        for profile in ("baseline", "overlap", "tuned"):
            for sync, health in [(1, 0), (0, 1), (1, 1)]:
                calls = self.dir / "services"
                calls.unlink(missing_ok=True)
                env = dict(
                    os.environ,
                    RUNNER_TEMP=str(self.dir),
                    PROFILE=profile,
                    SYNC_STATUS=str(sync),
                    HEALTH_STATUS=str(health),
                    PATH=f"{self.dir}:{os.environ['PATH']}",
                )
                result = subprocess.run(
                    ["bash", str(script)], env=env, capture_output=True, text=True
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"dependency sync={sync} service health={health}", result.stderr
                )
                self.assertEqual(len(calls.read_text().splitlines()), 1)
                self.assertIn("up -d --wait --wait-timeout 180", calls.read_text())


class ProducerTest(Workspace):
    scripts = ROOT / ".github/actions/backend-test-packages/database"

    def setUp(self):
        super().setUp()
        self.work = self.dir / "backend"
        (self.work / "ee/cloud").mkdir(parents=True)
        self.stage = self.dir / "migrated-db"
        self.migrates = self.dir / "migrates"
        tools = self.dir / "bin"
        tools.mkdir()
        self.env = dict(
            os.environ,
            PATH=f"{tools}:{os.environ['PATH']}",
            FOUND="false",
            PRODUCE="true",
            RECIPE="recipe",
            RUNNER_TEMP=str(self.dir),
            DIGEST="a" * 64,
            REPLAYED="a" * 64,
            MIGRATES=str(self.migrates),
        )
        # uv stands in for pytest's migrate: it records the boundary digest and
        # a dump that says whether the private checkout was present.
        tools_by_name = {
            "docker": "exit 0",
            "uv": """overlay=public; if [ -d ee/cloud ]; then overlay=private; fi
              echo "${PG_TEST_DB:-test_tfc_test} $overlay" >> "$MIGRATES"
              digest=$DIGEST; if [ "${PG_TEST_DB:-}" = test_ci_roundtrip ]; then digest=$REPLAYED; fi
              if [ -n "${CI_PRESEED_DIGEST:-}" ]; then echo "$digest" > "$CI_PRESEED_DIGEST"; fi
              if [ -n "${CI_PRESEED_DUMP:-}" ]; then echo "$overlay" > "$CI_PRESEED_DUMP"; fi""",
            "python3": """case "$3" in
              restore) exit "${RESTORE_STATUS:-0}" ;;
              dump) echo restored > "$4" ;;
              compare) exit "${COMPARE_STATUS:-0}" ;;
              *) exit 1 ;;
            esac""",
        }
        for name, body in tools_by_name.items():
            tool = tools / name
            tool.write_text("#!/bin/sh\n" + body + "\n")
            tool.chmod(0o755)

    def produce(self, **env):
        shutil.rmtree(self.dir / "fresh-db", ignore_errors=True)
        self.migrates.unlink(missing_ok=True)
        return subprocess.run(
            ["bash", str(self.scripts / "producer.sh")],
            cwd=self.work,
            env={**self.env, **env},
            capture_output=True,
            text=True,
        )

    def verify(self, recipe="recipe"):
        return subprocess.run(
            ["bash", str(self.scripts / "verify-record.sh"), str(self.stage), recipe],
            capture_output=True,
        )

    def staged(self):
        return sorted(path.name for path in self.stage.iterdir())

    def test_a_dev_push_saves_only_public_bytes_that_replay_to_the_control(self):
        result = self.produce()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "ee/cloud").is_dir())
        self.assertEqual((self.stage / "dump.sql.gz").read_text(), "public\n")
        # The control and the replay of the public dump ran with the overlay.
        self.assertEqual(
            self.migrates.read_text().splitlines(),
            [
                "test_tfc_test private",
                "test_ci_public public",
                "test_ci_roundtrip private",
            ],
        )
        self.assertEqual(self.verify().returncode, 0)
        self.assertNotEqual(self.verify("other").returncode, 0)
        (self.stage / "dump.sql.gz").write_bytes(b"corrupt")
        self.assertNotEqual(self.verify().returncode, 0)

    def test_without_the_overlay_the_control_itself_is_the_public_dump(self):
        shutil.rmtree(self.work / "ee")
        self.assertEqual(self.produce().returncode, 0)
        self.assertEqual((self.stage / "dump.sql.gz").read_text(), "public\n")
        self.assertEqual(
            self.migrates.read_text().splitlines(),
            ["test_tfc_test public", "test_ci_roundtrip public"],
        )

    def test_a_run_that_is_not_a_dev_push_saves_nothing(self):
        self.assertEqual(self.produce(PRODUCE="false").returncode, 0)
        self.assertEqual(self.staged(), [])
        self.assertEqual(len(self.migrates.read_text().splitlines()), 1)

    def test_a_replay_that_changes_the_public_dump_saves_nothing(self):
        result = self.produce(REPLAYED="b" * 64)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning title=Database dump::", result.stdout)
        self.assertEqual(self.staged(), [])

    def test_public_plus_overlay_unlike_the_control_saves_nothing(self):
        result = self.produce(COMPARE_STATUS=str(db_dump.COMPARE_DIFFERENT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "ee/cloud").is_dir())
        self.assertEqual(self.staged(), [])

    def test_a_comparison_error_fails_without_a_record(self):
        self.assertNotEqual(self.produce(COMPARE_STATUS="1").returncode, 0)
        self.assertTrue((self.work / "ee/cloud").is_dir())
        self.assertEqual(self.staged(), [])

    def test_a_failed_seed_comparison_fails_without_a_record(self):
        cmp = self.dir / "bin/cmp"
        cmp.write_text("#!/bin/sh\nexit 2\n")
        cmp.chmod(0o755)
        self.assertEqual(self.produce().returncode, 2)
        self.assertEqual(self.staged(), [])

    def test_a_partial_restore_is_an_error_and_nothing_is_saved(self):
        self.assertNotEqual(self.produce(RESTORE_STATUS="1").returncode, 0)
        self.assertEqual(self.staged(), [])

    def test_a_reused_dump_must_equal_this_checkout_or_the_run_fails(self):
        self.assertEqual(self.produce().returncode, 0)
        record = self.staged()
        for found in (
            {},
            {"PRODUCE": "false"},
        ):
            result = self.produce(FOUND="true", **found)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.staged(), record)
            # A reused dump is checked, not produced again.
            self.assertEqual(
                self.migrates.read_text().splitlines(),
                ["test_tfc_test private", "test_ci_roundtrip private"],
            )
        self.assertEqual(
            self.produce(
                FOUND="true", COMPARE_STATUS=str(db_dump.COMPARE_DIFFERENT)
            ).returncode,
            db_dump.COMPARE_DIFFERENT,
        )
        self.assertNotEqual(self.produce(FOUND="true", REPLAYED="b" * 64).returncode, 0)
        self.assertNotEqual(self.produce(FOUND="true", RECIPE="other").returncode, 0)
        # Not found, but something is staged: not a clean directory.
        self.assertNotEqual(self.produce().returncode, 0)


class BackendWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        self.jobs = ROOT / ".github/actions/backend-test-packages"
        self.producer = (self.jobs / "database/producer.sh").read_text()
        self.control = (self.jobs / "database/action.yml").read_text()
        self.shard = self.workflow.split("\n  test:\n", 1)[1].split(
            "\n  saml-multiworker:", 1
        )[0]

    def test_queue_runs_the_same_suite_without_forwarding_secrets(self):
        queue = (ROOT / ".github/workflows/backend-queue-ci.yml").read_text()
        self.assertIn("\n  merge_group:\n", queue)
        self.assertNotIn("\n  merge_group:\n", self.workflow)
        self.assertIn("\n  workflow_call:\n", self.workflow)
        self.assertIn("uses: ./.github/workflows/backend-ci.yml", queue)
        self.assertNotIn("secrets:", queue)
        self.assertNotIn("secrets.", queue)
        self.assertIn("name: Backend Tests Pass", queue)
        self.assertIn("needs: backend", queue)
        self.assertIn("actions: read", queue)
        self.assertIn("\n    name: backend\n", queue)
        self.assertIn("VERDICT: ${{ needs.backend.outputs.verdict }}", queue)
        self.assertIn(
            'test "$BACKEND_RESULT" = success && test "$VERDICT" = success', queue
        )
        self.assertIn("value: ${{ jobs.backend-tests.outputs.verdict }}", self.workflow)
        self.assertIn("verdict: ${{ steps.verdict.outcome }}", self.workflow)

    def test_every_optional_ee_checkout_requires_a_known_internal_event(self):
        self.assertIn(
            "IS_INTERNAL_RUN: ${{ needs.changes.outputs.internal }}", self.workflow
        )
        checkout = "uses: future-agi/future-agi/.github/actions/checkout-ee@"
        steps = [step for step in self.workflow.split("\n      - ") if checkout in step]
        self.assertEqual(len(steps), 2)  # shards, control
        for step in steps:
            condition = next(
                line.strip()
                for line in step.splitlines()
                if line.strip().startswith("if:")
            )
            self.assertTrue(condition.endswith("env.IS_INTERNAL_RUN == 'true'"))
            self.assertNotIn("||", condition)

    def test_main_bound_queue_uses_target_branch_for_checkout_and_tests(self):
        step = self.workflow.split("      - id: queue\n")[1].split("\n      - ")[0]
        self.assertIn("if: github.event_name == 'merge_group'", step)
        self.assertIn("BASE_REF: ${{ github.event.merge_group.base_ref }}", step)
        script = textwrap.dedent(step.split("        run: |\n")[1])
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "output"
            for ref, expected in (
                ("refs/heads/main", "main"),
                ("refs/heads/release/x", "release/x"),
                ("", None),
                ("refs/tags/main", None),
            ):
                output.write_text("")
                result = subprocess.run(
                    ["bash", "-ec", script],
                    env={**os.environ, "GITHUB_OUTPUT": str(output), "BASE_REF": ref},
                    check=False,
                )
                if expected is None:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), "")
                else:
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), f"target={expected}\n")
        self.assertIn("queue_target: ${{ steps.queue.outputs.target }}", self.workflow)
        for variable in ("EE_BASE_REF", "EE_REPO_BRANCH"):
            self.assertIn(
                variable + ": ${{ needs.changes.outputs.queue_target ||", self.workflow
            )
        self.assertIn("branch: ${{ env.EE_REPO_BRANCH }}", self.workflow)
        self.assertIn("base-ref: ${{ env.EE_BASE_REF }}", self.workflow)

    def test_manual_trials_have_independent_concurrency_groups(self):
        self.assertIn(
            "group: backend-ci-${{ github.event_name == 'workflow_dispatch' && github.run_id || github.event_name == 'push' && (github.ref == 'refs/heads/dev' && 'dev-push' || github.sha) || github.ref }}",
            self.workflow,
        )

    def test_shards_restore_only_this_runs_copy_of_a_dev_push_dump(self):
        plan = self.workflow.split("\n  plan:\n", 1)[1].split("\n  test:\n", 1)[0]
        self.assertIn("needs: [changes, plan]", self.shard)
        # One store: a dev-push artifact the plan job reads once per run.
        # actions/cache is not readable from queue refs and evicts at 10 GB.
        for text in (self.shard, self.control, plan):
            self.assertNotIn("migrated-db-pytest", text)
        self.assertNotIn("actions/cache", self.shard + self.control)
        self.assertIn("python3 -I futureagi/.ci/fetch_migrated_db.py", plan)
        self.assertIn("actions: read", plan)
        self.assertNotIn("secrets.", plan)
        self.assertIn("name: ${{ steps.saved-db.outputs.artifact }}", plan)
        self.assertIn(
            "needs.plan.outputs.db_recipe == steps.db-key.outputs.key", self.shard
        )
        self.assertLess(
            self.shard.index("verify-record.sh"), self.shard.index("db_dump.py restore")
        )
        self.assertLess(
            self.shard.index('cmp "$RUNNER_TEMP/replayed"'),
            self.shard.index("- name: Run shard"),
        )
        self.assertIn("if: steps.saved-db.outcome != 'success'", self.shard)
        self.assertNotIn("backend_cold_fanout", self.workflow)

    def test_only_a_dev_push_saves_a_dump_and_it_is_named_by_its_inputs(self):
        control_job = self.workflow.split("\n  migrated-database:\n")[1].split(
            "\n  agentic-eval:"
        )[0]
        self.assertIn(
            "save: ${{ github.event_name == 'push' && github.ref == 'refs/heads/dev' }}",
            control_job,
        )
        self.assertIn("artifact: ${{ needs.plan.outputs.db_artifact }}", control_job)
        self.assertIn("recipe: ${{ needs.plan.outputs.db_recipe }}", control_job)
        self.assertIn('echo "name=${key%-*}"', self.control)
        upload = self.control.split("- if: steps.record.outputs.saved == 'true'")[1]
        self.assertIn("name: ${{ steps.key.outputs.name }}", upload)
        self.assertIn("if: inputs.save == 'true'", self.control)

    def test_the_from_zero_job_creates_its_database_as_a_shard_does(self):
        control_job = self.workflow.split("\n  migrated-database:\n")[1].split(
            "\n  agentic-eval:"
        )[0]
        env = self.workflow.split("env: &backend_database_env\n")[1].split(
            "\n    steps:"
        )[0]
        for line in (
            "LITELLM_MODE: PRODUCTION",
            "CH25_DATABASE: test_tfc",
            "CH_DATABASE: test_tfc",
            'PYTHONHASHSEED: "0"',
            'FI_CH25_SCHEMA_APPLY_STRICT: "1"',
        ):
            self.assertIn(line, env)
        for job in (self.shard, control_job):
            self.assertIn("env: *backend_database_env", job)
        self.assertIn("with: &ee_checkout", self.shard)
        self.assertIn("with: *ee_checkout", control_job)
        self.assertIn(
            "uses: ./.github/actions/backend-test-packages/database\n", control_job
        )
        for job in (self.control, self.shard):
            self.assertIn(
                "uses: ./.github/actions/backend-test-packages/environment\n", job
            )
        self.assertIn(
            "uv sync --frozen --all-extras",
            (self.jobs / "environment/setup.sh").read_text(),
        )
        self.assertIn("database/key.sh)", self.shard)
        self.assertIn('bash "$GITHUB_ACTION_PATH/key.sh"', self.control)
        key_script = self.jobs / "database/key.sh"
        key_text = key_script.read_text()
        self.assertIn("python3 -I .ci/db_dump.py key", key_text)
        runtime = next(
            line for line in key_text.splitlines() if line.startswith("runtime=")
        )
        self.assertNotIn("ImageVersion", runtime)
        for recipe_input in ("$ids", "$config", '"$(uname -m)"', '"$tools"'):
            self.assertIn(recipe_input, runtime)
        # The Compose file's bytes are the whole service definition only while
        # it interpolates nothing from the environment.
        self.assertIn("config=$(sha256sum < docker-compose.test.yml)\n", key_text)
        self.assertNotIn("${", (ROOT / "futureagi/docker-compose.test.yml").read_text())

        # Exercise key.sh's exact recipe output against a stable, synthetic
        # service snapshot. Changing only the runner release must not change
        # the recipe every job of the run computes.
        with tempfile.TemporaryDirectory() as directory:
            work = pathlib.Path(directory) / "futureagi"
            (work / ".ci").mkdir(parents=True)
            (work / ".ci/db_dump.py").symlink_to(SCRIPT)
            shutil.copy(ROOT / "futureagi/docker-compose.test.yml", work)
            bin_dir = pathlib.Path(directory) / "bin"
            bin_dir.mkdir()
            (work / ".venv/bin").mkdir(parents=True)
            fakes = {
                work / ".venv/bin/python": "echo 3.12.3",
                bin_dir / "uv": "echo 'uv 0.13.9'",
                bin_dir / "docker": textwrap.dedent(
                    """\
                    case "$*" in
                      *"config --services") echo test-db ;;
                      *"ps -q test-db") echo container-1 ;;
                      "inspect --format {{.Image}} container-1") echo "sha256:$(printf 'a%.0s' $(seq 64))" ;;
                      # Includes a bare `compose config`: its rendering
                      # varies by Compose release, so the key never reads it.
                      *) exit 2 ;;
                    esac
                    """
                ),
            }
            for path, body in fakes.items():
                path.write_text("#!/bin/sh\n" + body)
                path.chmod(0o755)
            keys = []
            for runner_release in ("runner-release-a", "runner-release-b"):
                env = {
                    **os.environ,
                    "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "ImageVersion": runner_release,
                }
                result = subprocess.run(
                    ["bash", str(key_script)],
                    cwd=work,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                keys.append(result.stdout.strip())
            self.assertRegex(keys[0], r"^migrated-db-v3-[0-9a-f]{40}-[0-9a-f]{16}$")
            self.assertEqual(keys[0], keys[1])
            self.assertEqual(keys[0].rpartition("-")[0], db_dump.inputs_key(ROOT))

    def test_only_public_qualified_output_is_transferred(self):
        self.assertIn("--create-db", self.producer)
        self.assertIn('mv ee/cloud "$RUNNER_TEMP/held-ee-checkout"', self.producer)
        self.assertIn(
            'compare "$fresh/replayed.sql.gz" "$fresh/control.sql.gz"', self.producer
        )
        self.assertLess(
            self.producer.index("[[ $PRODUCE == true ]] || exit 0"),
            self.producer.index('cp "$public" "$stage/dump.sql.gz"'),
        )
        self.assertNotIn("fresh-db/", self.control)
        self.assertNotIn("continue-on-error", self.shard + self.control)
        self.assertIn("        migrated-database,", self.workflow)

    def test_e2e_and_saml_are_not_dump_consumers(self):
        e2e = (ROOT / ".github/workflows/e2e-ci.yml").read_text()
        saml = self.workflow.split("\n  saml-multiworker:\n")[1].split(
            "\n  migrated-database:"
        )[0]
        self.assertIn("--create-db", saml)
        for job in (e2e, saml):
            self.assertNotIn("db_dump.py", job)


if __name__ == "__main__":
    unittest.main()
