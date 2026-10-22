"""The Standalone bootstrap proper (deploy/standalone/bin/bootstrap.py): the
steps main() runs and their order, what a repeat boot skips, the refusals
and failures that stop a boot, and the script's entry point (retry back-off,
hand-over to the waiter, waiter mode).

The datastore steps themselves are bootstrap_install's (futureagi/tfc/
management/commands/bootstrap_install.py) and are tested with it, in
futureagi/tfc/tests/test_bootstrap_install.py; here they run from the backend
source tree as the Standalone bootstrap calls them. Django, the rest of the
backend, ClickHouse and Temporal are fakes in sys.modules that record what
the bootstrap does in one ordered log. Nothing here needs the backend's
dependencies or a running stack; the observed-attribute index SQL and the
operator command list are read from the backend source tree."""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import runpy
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest import mock

from test_standalone_first_run import fake_structlog

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "futureagi"
SCRIPT = ROOT / "deploy" / "standalone" / "bin" / "bootstrap.py"

_spec = importlib.util.spec_from_file_location("standalone_bootstrap_proper", SCRIPT)
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)

OUTBOX = "tracer.services.clickhouse.oss_outbox_cdc"
# docker-compose's service endpoints, none of them the defaults. Django's
# (PGBOUNCER_HOST, REDIS_URL) are FakeStack.settings.
SERVICES = {
    "PG_HOST": "pg.internal",
    "PG_PORT": "5433",
    "CH_HOST": "ch.internal",
    "CH_HTTP_PORT": "8124",
    "TEMPORAL_HOST": "temporal.internal:7234",
}
FIRST_BOOT = [
    "phase waiting",
    "tcp pg.internal:5433",
    "tcp ch.internal:8124",
    "tcp redis.internal:6380",
    "tcp temporal.internal:7234",
    "django.setup",
    "phase migrating",
    "command createcachetable",
    "post_migrate hook",
    "command migrate",
    "command seed_system_evals",
    "phase clickhouse",
    "clickhouse native schema",
    "property catalog",
    "phase schedules",
    "search attributes",
    "phase cdc",
    "change data capture",
    "phase schedules",
    "command register_temporal_schedules",
    "command collectstatic",
    "placeholder shutdown",
    "phase api",
]


def _operator_commands() -> frozenset[str]:
    """model_hub.apps.OPERATOR_STARTUP_MUTATION_COMMANDS, read without Django."""
    tree = ast.parse((BACKEND / "model_hub" / "apps.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and [
            getattr(target, "id", None) for target in node.targets
        ] == ["OPERATOR_STARTUP_MUTATION_COMMANDS"]:
            return frozenset(ast.literal_eval(node.value.args[0]))
    raise AssertionError("OPERATOR_STARTUP_MUTATION_COMMANDS not found")


def _split_statements():
    """The backend's own splitter; it imports nothing but re."""
    spec = importlib.util.spec_from_file_location(
        "schema_rewriter",
        BACKEND / "tracer/services/clickhouse/v2/apply_schema_rewriter.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.split_statements


split_statements = _split_statements()
OPERATOR_COMMANDS = _operator_commands()


class OutboxCDCError(ValueError):
    """The installer's own error type is a ValueError."""


class CommandError(Exception):
    """Django's; bootstrap_install.BootstrapError is one."""


class BaseCommand:
    """Django's, for bootstrap_install's Command."""


class FakeStack:
    """Postgres (through Django), ClickHouse and Temporal as the bootstrap
    sees them. ``log`` is the ordered record of what the bootstrap did."""

    def __init__(self, tmp: Path):
        self.log: list[str] = []
        self.commands: list[tuple[str, dict]] = []
        self.authorized_argv: list[list[str]] = []
        self.hooks: list[tuple] = []
        self.sql: list[tuple[str, object]] = []
        self.clickhouse: list[tuple] = []
        self.search_attribute_calls: list[tuple] = []
        self.cdc_calls: list[tuple] = []
        self.setup_env: dict[str, str] = {}
        # Where Django connects: docker-compose.yml points PGBOUNCER_HOST at
        # PG_HOST.
        self.settings = SimpleNamespace(
            BASE_DIR=str(BACKEND / "tfc"),
            DATABASES={"default": {"HOST": "pg.internal", "PORT": "5433"}},
            REDIS_URL="redis://:secret@redis.internal:6380/0",
        )
        # Postgres
        self.peerdb_slots = 0
        self.temporal_databases = 0
        self.server_version = 160004
        self.collation = ("2.36", "2.36")
        self.migrations_table = False
        self.applied: set[str] = set()
        # Outcomes
        self.authorized = True
        self.command_errors: dict[str, BaseException] = {}
        self.setup_error: BaseException | None = None
        self.native_exit: object = 0
        self.validation_rows = [(1,)]
        self.cdc_outcomes: list = [{"mode": "outbox", "ready": True}]
        self.model_hub_config = SimpleNamespace(label="model_hub")
        self.app_configs = []
        for label, names in (
            ("accounts", ("0001_initial", "0002_user_flags")),
            ("tracer", ("0001_initial",)),
        ):
            migrations = tmp / "apps" / label / "migrations"
            migrations.mkdir(parents=True)
            for name in (*names, "__init__", "helpers"):
                (migrations / f"{name}.py").touch()
            self.app_configs.append(
                SimpleNamespace(label=label, path=str(migrations.parent))
            )
        self.on_disk = {
            "accounts/0001_initial",
            "accounts/0002_user_flags",
            "tracer/0001_initial",
        }

    # -- Postgres through Django ------------------------------------------

    def cursor(self):
        stack = self

        class Cursor:
            def __init__(self):
                self.row, self.rows = None, []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                stack.sql.append((" ".join(sql.split()), params))
                if "pg_replication_slots" in sql:
                    self.row = (stack.peerdb_slots,)
                elif "FROM pg_database WHERE datname IN" in sql:
                    self.row = (stack.temporal_databases,)
                elif "server_version_num" in sql:
                    self.row = (stack.server_version,)
                elif "datcollversion" in sql:
                    self.row = stack.collation
                elif "FROM django_migrations" in sql:
                    self.rows = [tuple(m.split("/")) for m in sorted(stack.applied)]
                else:
                    raise AssertionError(f"unexpected SQL: {sql}")

            def fetchone(self):
                return self.row

            def fetchall(self):
                return self.rows

        return Cursor()

    def table_names(self):
        return ["django_migrations"] if self.migrations_table else []

    def call_command(self, name, **options):
        self.log.append(f"command {name}")
        self.commands.append((name, options))
        if name in self.command_errors:
            raise self.command_errors[name]
        if name == "migrate":
            self.migrations_table = True
            self.applied = set(self.on_disk)

    def authorize(self, argv):
        self.authorized_argv.append(list(argv))
        return self.authorized

    def post_migrate_connect(self, receiver, sender=None, dispatch_uid=None):
        self.log.append("post_migrate hook")
        self.hooks.append((receiver, sender, dispatch_uid))

    def setup(self):
        self.log.append("django.setup")
        self.setup_env = dict(os.environ)
        if self.setup_error is not None:
            raise self.setup_error

    # -- ClickHouse ---------------------------------------------------------

    def get_client(self, **kwargs):
        stack = self
        database = kwargs.get("database")
        if database is None:
            self.log.append("property catalog")
        self.clickhouse.append(("connect", kwargs))

        class Client:
            def command(self, sql, parameters=None):
                stack.clickhouse.append(("command", database, sql, parameters))

            def query(self, sql, parameters=None):
                stack.clickhouse.append(("query", database, sql, parameters))
                return SimpleNamespace(result_rows=stack.validation_rows)

            def close(self):
                stack.clickhouse.append(("close", database))

        return Client()

    def native_main(self, argv):
        self.log.append("clickhouse native schema")
        if isinstance(self.native_exit, BaseException):
            raise self.native_exit
        return self.native_exit

    def cdc_mode(self, env=None):
        """oss_outbox_cdc.cdc_mode(): the backend's default is peerdb."""
        return os.environ.get("FI_CDC_MODE", "peerdb").strip().lower()

    def ensure_installed(self, *args, **kwargs):
        self.log.append("change data capture")
        self.cdc_calls.append((args, kwargs))
        outcome = (
            self.cdc_outcomes.pop(0)
            if len(self.cdc_outcomes) > 1
            else self.cdc_outcomes[0]
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    # -- Temporal -----------------------------------------------------------

    async def temporal_client(self):
        return "temporal-client"

    async def register_search_attributes(self, client, namespace):
        self.log.append("search attributes")
        self.search_attribute_calls.append((client, namespace))
        return True

    # -- The modules ----------------------------------------------------------

    def connect_tcp(self, address, timeout=None):
        self.log.append(f"tcp {address[0]}:{address[1]}")
        return SimpleNamespace(close=lambda: None)

    def modules(self) -> dict[str, types.ModuleType]:
        def package(name, **attrs):
            module = types.ModuleType(name)
            module.__path__ = []
            module.__dict__.update(attrs)
            return module

        def seed_prompt_labels(sender, **kwargs):
            pass

        self.seed_prompt_labels = seed_prompt_labels
        connection = SimpleNamespace(
            cursor=self.cursor,
            introspection=SimpleNamespace(table_names=self.table_names),
        )
        connections = SimpleNamespace(
            close_all=lambda: self.log.append("close database connections")
        )
        apps = SimpleNamespace(
            get_app_config=lambda label: self.model_hub_config,
            get_app_configs=lambda: list(self.app_configs),
        )
        native = package(
            "tracer.services.clickhouse.oss_cdc_install", main=self.native_main
        )
        outbox = package(
            OUTBOX,
            cdc_mode=self.cdc_mode,
            ensure_installed=self.ensure_installed,
            OutboxCDCError=OutboxCDCError,
        )
        return {
            "django": package("django", setup=self.setup),
            "django.apps": package("django.apps", apps=apps),
            "django.conf": package("django.conf", settings=self.settings),
            "django.db": package(
                "django.db", connection=connection, connections=connections
            ),
            "django.db.models": package("django.db.models"),
            "django.db.models.signals": package(
                "django.db.models.signals",
                post_migrate=SimpleNamespace(connect=self.post_migrate_connect),
            ),
            "django.core": package("django.core"),
            "django.core.management": package(
                "django.core.management", call_command=self.call_command
            ),
            "django.core.management.base": package(
                "django.core.management.base",
                BaseCommand=BaseCommand,
                CommandError=CommandError,
            ),
            "model_hub": package("model_hub"),
            "model_hub.apps": package(
                "model_hub.apps",
                OPERATOR_STARTUP_MUTATION_COMMANDS=OPERATOR_COMMANDS,
                _seed_prompt_labels_after_migrate=seed_prompt_labels,
                explicit_management_mutation_authorized=self.authorize,
            ),
            "tracer": package("tracer"),
            "tracer.services": package("tracer.services"),
            "tracer.services.clickhouse": package(
                "tracer.services.clickhouse",
                oss_cdc_install=native,
                oss_outbox_cdc=outbox,
            ),
            "tracer.services.clickhouse.oss_cdc_install": native,
            OUTBOX: outbox,
            "tracer.services.clickhouse.v2": package("tracer.services.clickhouse.v2"),
            "tracer.services.clickhouse.v2.apply_schema_rewriter": package(
                "tracer.services.clickhouse.v2.apply_schema_rewriter",
                split_statements=split_statements,
            ),
            "clickhouse_connect": package(
                "clickhouse_connect", get_client=self.get_client
            ),
            "structlog": fake_structlog(),
            # Its real subpackages, bootstrap_install among them, import
            # from the source tree; the ones listed here are fakes.
            "tfc": package("tfc", __path__=[str(BACKEND / "tfc")]),
            "tfc.temporal": package("tfc.temporal", TEMPORAL_NAMESPACE="fi-ns"),
            "tfc.temporal.common": package("tfc.temporal.common"),
            "tfc.temporal.common.client": package(
                "tfc.temporal.common.client", get_client=self.temporal_client
            ),
            "tfc.temporal.eval_tasks": package("tfc.temporal.eval_tasks"),
            "tfc.temporal.eval_tasks.registration": package(
                "tfc.temporal.eval_tasks.registration",
                register_search_attributes=self.register_search_attributes,
            ),
        }


class FakePlaceholder:
    """The JSON-503 server holding the API port while the bootstrap runs."""

    def __init__(self, stack: FakeStack, address=None, handler=None):
        self.stack = stack
        if address is not None:
            stack.log.append(f"placeholder bind {address[0]}:{address[1]}")

    def serve_forever(self):
        pass

    def shutdown(self):
        self.stack.log.append("placeholder shutdown")

    def server_close(self):
        pass


class StackTest(unittest.TestCase):
    """The loaded module with the fake stack installed, its /data and
    /run/futureagi in a temporary directory, and stdout captured."""

    env: ClassVar[dict[str, str]] = {}

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        data, run = self.tmp / "data", self.tmp / "run"
        data.mkdir()
        self.stack = FakeStack(self.tmp)
        for name, value in (
            ("PROJECT_ROOT", BACKEND),
            ("READY", data / ".bootstrap-ok"),
            ("FINGERPRINT", data / ".bootstrap-fingerprint"),
            ("ADOPTED", data / ".adopted-distributed-install"),
            ("BUILD_ID", self.tmp / "build-id"),
            ("STATIC_COLLECTED", self.tmp / "static-collected"),
            ("RUN_DIR", run),
            ("STATUS_FILE", run / "status.json"),
            ("UI_READY", run / "ready"),
            ("SUMMARY_SHOWN", run / "summary-shown"),
            ("VERTEX_KEY", self.tmp / "vertex.json"),
            ("_placeholder", FakePlaceholder(self.stack)),
        ):
            self.patch(bootstrap, name, value)
        self.patch(
            bootstrap,
            "write_status",
            lambda phase: self.stack.log.append(f"phase {phase}"),
        )
        self.patch(bootstrap.socket, "create_connection", self.stack.connect_tcp)
        self.patch(sys, "path", list(sys.path))
        self.addCleanup(os.chdir, os.getcwd())
        contexts = contextlib.ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(mock.patch.dict(sys.modules, self.stack.modules()))
        contexts.enter_context(mock.patch.dict(os.environ, self.env, clear=True))
        self.out = io.StringIO()
        contexts.enter_context(contextlib.redirect_stdout(self.out))

    def fake_time(self, monotonic=(0.0,)):
        """Replaces the module's clock; returns the recorded sleeps."""
        ticks = iter(monotonic)
        last = [monotonic[-1]]

        def now():
            last[0] = next(ticks, last[0])
            return last[0]

        sleeps: list[float] = []
        self.patch(
            bootstrap,
            "time",
            SimpleNamespace(
                monotonic=now,
                sleep=sleeps.append,
                time=time.time,
                strftime=time.strftime,
                gmtime=time.gmtime,
            ),
        )
        return sleeps

    def lines(self) -> list[str]:
        return self.out.getvalue().splitlines()


# ---------------------------------------------------------------------------
# main(): the steps and their order
# ---------------------------------------------------------------------------


class MainTest(StackTest):
    env = SERVICES

    def test_first_boot_runs_every_step_in_order(self):
        bootstrap.READY.write_text("")  # left by the previous container
        seen_ready = []
        connect = self.stack.connect_tcp
        self.patch(
            bootstrap.socket,
            "create_connection",
            lambda *args, **kw: (
                seen_ready.append(bootstrap.READY.exists()) or connect(*args, **kw)
            ),
        )

        bootstrap.main()

        self.assertEqual(self.stack.log, FIRST_BOOT)
        # READY is gone before anything waits: the API and collector must not
        # start against a half-bootstrapped database.
        self.assertEqual(seen_ready, [False] * 4)
        self.assertTrue(bootstrap.READY.exists())
        fingerprint, _ = bootstrap.schema_fingerprint()
        self.assertEqual(bootstrap.FINGERPRINT.read_text(), fingerprint + "\n")
        self.assertFalse(bootstrap.ADOPTED.exists())
        self.assertIsNone(bootstrap._placeholder)
        self.assertRegex(
            self.lines()[-1],
            r"^\[bootstrap\] bootstrap done in \d+s; starting the API$",
        )
        self.assertEqual(
            self.stack.commands,
            [
                ("createcachetable", {"database": "default"}),
                ("migrate", {"interactive": False, "verbosity": 1}),
                ("seed_system_evals", {}),
                ("register_temporal_schedules", {}),
                ("collectstatic", {"interactive": False, "verbosity": 0}),
            ],
        )
        # Every mutating command goes through the operator authorization;
        # collectstatic writes no database and is not on the list.
        self.assertEqual(
            [argv[1] for argv in self.stack.authorized_argv],
            [
                "createcachetable",
                "migrate",
                "seed_system_evals",
                "register_temporal_schedules",
            ],
        )
        self.assertEqual(self.stack.cdc_calls, [((), {})])
        self.assertEqual(
            self.stack.search_attribute_calls, [("temporal-client", "fi-ns")]
        )

    def test_an_unchanged_image_skips_migrate_and_seeds_but_nothing_else(self):
        bootstrap.main()
        self.stack.log.clear()
        bootstrap.READY.unlink()
        bootstrap.STATIC_COLLECTED.touch()

        bootstrap.main()

        # ClickHouse, CDC and Temporal can be reset independently of
        # Postgres, so they are applied on every boot.
        expected = [
            step
            for step in FIRST_BOOT
            if step
            not in (
                "phase migrating",
                "command createcachetable",
                "post_migrate hook",
                "command migrate",
                "command seed_system_evals",
                "command collectstatic",
                "placeholder shutdown",
            )
        ]
        self.assertEqual(self.stack.log, expected)
        self.assertIn(
            "[bootstrap] image and migrations unchanged since the last boot; "
            "skipping migrate and seeds",
            self.lines(),
        )
        self.assertTrue(bootstrap.READY.exists())

    def test_a_reset_postgres_is_migrated_although_the_image_is_unchanged(self):
        bootstrap.main()
        self.stack.log.clear()
        self.stack.migrations_table, self.stack.applied = False, set()

        bootstrap.main()

        self.assertIn("command migrate", self.stack.log)
        self.assertIn("command seed_system_evals", self.stack.log)

    def test_a_new_image_is_migrated_again(self):
        bootstrap.main()
        self.stack.log.clear()
        bootstrap.BUILD_ID.write_text("build-2")

        bootstrap.main()

        self.assertIn("command migrate", self.stack.log)
        self.assertEqual(
            bootstrap.FINGERPRINT.read_text(),
            bootstrap.schema_fingerprint()[0] + "\n",
        )

    def test_a_failed_step_leaves_the_boot_unready_and_the_fingerprint_unwritten(self):
        self.stack.native_exit = 1

        with self.assertRaisesRegex(
            CommandError, "ClickHouse native schema install failed"
        ):
            bootstrap.main()

        self.assertEqual(self.stack.log[-1], "clickhouse native schema")
        self.assertFalse(bootstrap.READY.exists())
        # Without the fingerprint the next attempt migrates again.
        self.assertFalse(bootstrap.FINGERPRINT.exists())
        self.assertNotIn("placeholder shutdown", self.stack.log)

    def test_createcachetable_failing_does_not_stop_the_boot(self):
        self.stack.command_errors["createcachetable"] = RuntimeError("exists")

        bootstrap.main()

        self.assertIn(
            "[bootstrap] createcachetable failed (continuing): exists", self.lines()
        )
        self.assertTrue(bootstrap.READY.exists())

    def test_a_hosted_environment_without_operator_mode_refuses_to_migrate(self):
        self.stack.authorized = False

        # The refusal itself is bootstrap_install's (test_bootstrap_install.py).
        with self.assertRaises(CommandError):
            bootstrap.main()

        self.assertEqual(self.stack.log[-1], "phase migrating")
        self.assertEqual(self.stack.commands, [])
        self.assertFalse(bootstrap.READY.exists())

    def test_a_backend_image_without_bootstrap_install_says_what_to_build(self):
        # A published backend image from before the Standalone setup.
        sys.modules[bootstrap.BOOTSTRAP_INSTALL] = None

        with self.assertRaisesRegex(
            bootstrap.BootstrapError,
            r"^this backend image predates the Standalone setup.*--from-source",
        ):
            bootstrap.main()

        self.assertEqual(self.stack.log, ["phase waiting"])

    def test_waits_where_django_and_the_cdc_installer_connect(self):
        # A pooler in front of Postgres: Django connects to it, the CDC
        # installer to Postgres itself.
        self.stack.settings.DATABASES["default"].update(
            HOST="pooler.internal", PORT="6432"
        )

        bootstrap.main()

        self.assertEqual(
            [step for step in self.stack.log if step.startswith("tcp ")],
            [
                "tcp pooler.internal:6432",
                "tcp pg.internal:5433",
                "tcp ch.internal:8124",
                "tcp redis.internal:6380",
                "tcp temporal.internal:7234",
            ],
        )
        self.assertTrue(bootstrap.READY.exists())

    def test_logs_the_cdc_mode_the_installer_runs(self):
        # Unset, the installer falls back to peerdb; the log must not claim
        # a default of its own.
        bootstrap.main()

        self.assertIn(
            "[bootstrap] change data capture (FI_CDC_MODE=peerdb) ...", self.lines()
        )

    def test_the_image_runs_the_outbox_cdc_mode(self):
        dockerfile = (ROOT / "deploy" / "standalone" / "Dockerfile").read_text()
        self.assertRegex(dockerfile, r"(?m)^ENV FI_CDC_MODE=outbox$")

    def test_adopting_a_distributed_database_is_recorded_once_the_boot_succeeds(
        self,
    ):
        self.stack.peerdb_slots = 1
        os.environ["FI_ADOPT_DISTRIBUTED_INSTALL_DATA"] = "true"
        self.stack.cdc_outcomes = [OutboxCDCError("PeerDB is still running")]

        with self.assertRaises(CommandError):
            bootstrap.main()
        self.assertFalse(bootstrap.ADOPTED.exists())

        self.stack.cdc_outcomes = [{"peerdb_takeover": {"slots": ["peerflow_x"]}}]
        bootstrap.main()

        self.assertRegex(
            bootstrap.ADOPTED.read_text(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n$"
        )


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------


class ForeignDatabaseTest(StackTest):
    def assert_refused(self, reason: str):
        with self.assertRaises(bootstrap.BootstrapError) as caught:
            bootstrap.refuse_foreign_database()
        message = str(caught.exception)
        self.assertIn("belongs to a Distributed install", message)
        self.assertIn(reason, message)
        self.assertIn("FI_ADOPT_DISTRIBUTED_INSTALL_DATA=true", message)
        self.assertIn("COMPOSE_FILE=docker-compose.distributed.yml", message)
        # Moving data between setups is unsupported; the way out is a clean reinstall.
        self.assertIn("./bin/uninstall --wipe-data", message)

    def test_a_database_this_install_created_is_accepted(self):
        self.assertFalse(bootstrap.refuse_foreign_database())
        # The adopt flag does not make it an adoption.
        with mock.patch.dict(os.environ, {"FI_ADOPT_DISTRIBUTED_INSTALL_DATA": "true"}):
            self.assertFalse(bootstrap.refuse_foreign_database())
        self.assertEqual(self.stack.sql[0][1], ["peerflow%"])
        self.assertEqual(self.stack.sql[1][1], ["temporal", "temporal_visibility"])

    def test_peerdb_slots_refuse_unless_adoption_is_asked_for(self):
        self.stack.peerdb_slots = 2
        self.assert_refused("it has PeerDB replication slots")

        for value in ("true", "YES", "1"):
            with (
                self.subTest(value=value),
                mock.patch.dict(
                    os.environ, {"FI_ADOPT_DISTRIBUTED_INSTALL_DATA": value}
                ),
            ):
                self.assertTrue(bootstrap.refuse_foreign_database())
        self.assertIn(
            "[bootstrap] adopting a distributed install's database "
            "(it has PeerDB replication slots)",
            self.lines(),
        )
        for env in (
            {"FI_ADOPT_DISTRIBUTED_INSTALL_DATA": "no"},
            # A name the flag never shipped under.
            {"FI_ADOPT_FULL_INSTALL_DATA": "true"},
        ):
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                self.assert_refused("PeerDB replication slots")

    def test_temporal_databases_refuse_until_an_adopted_boot_succeeded(self):
        self.stack.temporal_databases = 1
        self.assert_refused("the Postgres server holds Temporal's databases")

        bootstrap.ADOPTED.write_text("2026-01-01T00:00:00Z\n")
        self.assertFalse(bootstrap.refuse_foreign_database())
        self.assertIn("DROP DATABASE temporal_visibility", self.out.getvalue())

    def test_peerdb_slots_refuse_even_after_an_adopted_boot(self):
        bootstrap.ADOPTED.write_text("2026-01-01T00:00:00Z\n")
        self.stack.peerdb_slots = 1
        self.assert_refused("PeerDB replication slots")

    def test_a_database_from_another_c_library_is_refused(self):
        self.stack.collation = ("2.36", None)

        with self.assertRaisesRegex(
            bootstrap.BootstrapError,
            r"created with glibc collation 2\.36.*\(postgres:16\)",
        ):
            bootstrap.refuse_foreign_database()

    def test_collation_is_not_read_before_postgres_15(self):
        self.stack.server_version = 140011
        self.stack.collation = ("2.36", None)

        self.assertFalse(bootstrap.refuse_foreign_database())
        self.assertFalse(any("datcollversion" in sql for sql, _ in self.stack.sql))

    def test_a_database_without_a_recorded_collation_is_accepted(self):
        self.stack.collation = (None, None)
        self.assertFalse(bootstrap.refuse_foreign_database())


class FingerprintTest(StackTest):
    def test_lists_only_numbered_migrations(self):
        _, migrations = bootstrap.schema_fingerprint()
        self.assertEqual(
            migrations,
            [
                "accounts/0001_initial",
                "accounts/0002_user_flags",
                "tracer/0001_initial",
            ],
        )

    def test_changes_with_every_input_that_decides_migrate(self):
        base, _ = bootstrap.schema_fingerprint()
        self.assertEqual(bootstrap.schema_fingerprint()[0], base)
        for name, env in (
            ("version", {"FUTURE_AGI_VERSION": "v2"}),
            ("edition", {"EE_LICENSE_KEY": "key-1"}),
            ("cloud", {"CLOUD_DEPLOYMENT": "aws"}),
        ):
            with self.subTest(name), mock.patch.dict(os.environ, env):
                self.assertNotEqual(bootstrap.schema_fingerprint()[0], base)
        # The edition, not the key, decides.
        with mock.patch.dict(os.environ, {"EE_LICENSE_KEY": "key-1"}):
            licensed = bootstrap.schema_fingerprint()[0]
        with mock.patch.dict(os.environ, {"EE_LICENSE_KEY": "key-2"}):
            self.assertEqual(bootstrap.schema_fingerprint()[0], licensed)

        bootstrap.BUILD_ID.write_text("build-1")
        built = bootstrap.schema_fingerprint()[0]
        self.assertNotEqual(built, base)
        migrations = Path(self.stack.app_configs[1].path, "migrations")
        (migrations / "0002_new_field.py").touch()
        self.assertNotEqual(bootstrap.schema_fingerprint()[0], built)

    def test_a_new_database_has_every_migration_unapplied(self):
        migrations = sorted(self.stack.on_disk)
        self.assertEqual(bootstrap.unapplied_migrations(migrations), set(migrations))

        self.stack.migrations_table = True
        self.stack.applied = {"accounts/0001_initial", "other/0001_initial"}
        self.assertEqual(
            bootstrap.unapplied_migrations(migrations),
            {"accounts/0002_user_flags", "tracer/0001_initial"},
        )


class ConfigureDjangoTest(StackTest):
    env: ClassVar[dict[str, str]] = {
        **SERVICES,
        "NO_STARTUP_DB_MUTATIONS": "true",
        "SERVICE_TYPE": "app",
    }

    def test_the_bootstrap_process_alone_may_mutate_the_databases(self):
        bootstrap.main()

        env = self.stack.setup_env
        self.assertEqual(env["NO_STARTUP_DB_MUTATIONS"], "false")
        self.assertEqual(env["SERVICE_TYPE"], "bootstrap")
        self.assertEqual(env["FI_SKIP_CH25_MIGRATION"], "1")
        self.assertEqual(env["DJANGO_SETTINGS_MODULE"], "tfc.settings.settings")
        self.assertEqual(sys.path[0], str(BACKEND))
        self.assertEqual(Path.cwd().resolve(), BACKEND.resolve())

    def test_explicit_settings_are_kept(self):
        os.environ.update(
            FI_SKIP_CH25_MIGRATION="0", DJANGO_SETTINGS_MODULE="tfc.settings.test"
        )

        bootstrap.configure_django()

        self.assertEqual(os.environ["FI_SKIP_CH25_MIGRATION"], "0")
        self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "tfc.settings.test")


class CollectStaticTest(StackTest):
    def test_skipped_when_the_image_build_collected(self):
        bootstrap.STATIC_COLLECTED.touch()
        bootstrap.collect_static()
        self.assertEqual(self.stack.commands, [])

    def test_collected_once_when_the_build_did_not(self):
        bootstrap.collect_static()
        bootstrap.collect_static()

        self.assertEqual(
            self.stack.commands,
            [("collectstatic", {"interactive": False, "verbosity": 0})],
        )
        self.assertTrue(bootstrap.STATIC_COLLECTED.exists())

    def test_a_failure_is_not_fatal_and_is_tried_again_next_time(self):
        self.stack.command_errors["collectstatic"] = OSError("read-only file system")

        bootstrap.collect_static()

        self.assertIn(
            "[bootstrap] collectstatic failed (continuing): read-only file system",
            self.lines(),
        )
        self.assertFalse(bootstrap.STATIC_COLLECTED.exists())

    def test_an_unwritable_marker_is_not_fatal(self):
        self.patch(bootstrap, "STATIC_COLLECTED", self.tmp / "missing" / "marker")
        bootstrap.collect_static()
        self.assertEqual(self.stack.log, ["command collectstatic"])


class WaiterEdgeTest(StackTest):
    def test_a_slow_api_gets_a_note_every_five_minutes_not_every_poll(self):
        self.fake_time(monotonic=(0.0, 61.0, 100.0, 361.0, 400.0))
        self.patch(
            bootstrap, "api_healthy", mock.Mock(side_effect=[False] * 4 + [True])
        )

        self.assertEqual(bootstrap.wait_for_api(time.time()), 0)

        notes = [line for line in self.lines() if "still waiting" in line]
        self.assertEqual(
            notes,
            [
                f"[bootstrap] still waiting for the API to answer {bootstrap.HEALTH_URL}"
                f" after {duration}; its errors are above in this log"
                for duration in ("1m 01s", "6m 01s")
            ],
        )

    def test_an_unwritable_run_dir_keeps_the_starting_page_but_is_not_fatal(self):
        # A file where the directory should be: every write fails.
        bootstrap.RUN_DIR.parent.mkdir(exist_ok=True)
        bootstrap.RUN_DIR.write_text("")
        self.patch(bootstrap, "api_healthy", lambda opener: True)

        self.assertEqual(bootstrap.wait_for_api(time.time()), 0)
        bootstrap.show_summary_once()

        self.assertIn("could not switch the UI to the app", self.out.getvalue())
        self.assertIn("Future AGI is ready in", self.out.getvalue())
        self.assertIn("Standalone setup", self.out.getvalue())
        self.assertEqual(self.stack.log[-1], "phase ready")


# ---------------------------------------------------------------------------
# The script: what supervisord runs
# ---------------------------------------------------------------------------


class EntryPointTest(unittest.TestCase):
    """bootstrap.py run as __main__, as supervisord runs it, in this process
    against the fake stack: the module's paths come from FI_DATA_DIR,
    FI_RUN_DIR and PROJECT_ROOT."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.data, self.run_dir = self.tmp / "data", self.tmp / "run"
        self.data.mkdir()
        self.stack = FakeStack(self.tmp)
        self.env = {
            **SERVICES,
            "FI_DATA_DIR": str(self.data),
            "FI_RUN_DIR": str(self.run_dir),
            "PROJECT_ROOT": str(BACKEND),
        }
        self.execv = mock.Mock(side_effect=lambda *args: self.stack.log.append("execv"))
        self.health = mock.MagicMock(status=200)
        self.health.__enter__.return_value = self.health
        self.opened = []
        self.sleep = mock.Mock()
        self.addCleanup(os.chdir, os.getcwd())

    def run_script(self, *argv, **env):
        stack = self.stack

        class Placeholder(FakePlaceholder):
            def __init__(self, address, handler):
                super().__init__(stack, address, handler)

        def open_health(opener, url, timeout=None):
            self.opened.append((url, timeout))
            return self.health

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(sys.modules, self.stack.modules()),
            mock.patch.dict(os.environ, {**self.env, **env}, clear=True),
            mock.patch.object(sys, "argv", [str(SCRIPT), *argv]),
            mock.patch.object(sys, "path", list(sys.path)),
            mock.patch("socket.create_connection", self.stack.connect_tcp),
            mock.patch("time.sleep", self.sleep),
            mock.patch("http.server.ThreadingHTTPServer", Placeholder),
            mock.patch("os.execv", self.execv),
            mock.patch("urllib.request.OpenerDirector.open", open_health),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as exit_,
        ):
            runpy.run_path(str(SCRIPT), run_name="__main__")
        return exit_.exception.code, out.getvalue(), err.getvalue()

    def status(self) -> dict:
        return json.loads((self.run_dir / "status.json").read_text())

    def test_a_successful_boot_hands_over_to_the_waiter(self):
        before = time.time()
        code, out, err = self.run_script()

        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        log = self.stack.log
        self.assertEqual(log[0], "placeholder bind 0.0.0.0:8000")
        self.assertEqual(
            log[-4:],
            [
                "command collectstatic",
                "placeholder shutdown",
                "close database connections",
                "execv",
            ],
        )
        executable, args = self.execv.call_args.args
        self.assertEqual(executable, sys.executable)
        self.assertEqual(args[:3], [sys.executable, str(SCRIPT), "--wait-for-api"])
        self.assertGreaterEqual(float(args[3]), before - 1)
        # The waiter then switched the UI to the app.
        self.assertTrue((self.data / ".bootstrap-ok").exists())
        self.assertTrue((self.data / ".bootstrap-fingerprint").exists())
        self.assertTrue((self.run_dir / "ready").exists())
        self.assertEqual(self.status()["phase"], "ready")
        self.assertEqual(self.opened, [("http://127.0.0.1:8000/health/", 3)])
        lines = out.splitlines()
        self.assertIn("Standalone setup", lines[0])
        self.assertTrue(all(line.startswith("[bootstrap] ") for line in lines))
        self.assertRegex(lines[-1], r"Future AGI is ready in \d+s\. Open http://")

    def test_a_refused_boot_says_why_and_retries_after_the_back_off(self):
        self.stack.peerdb_slots = 1

        code, out, err = self.run_script()

        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertIn(
            "[bootstrap] FAILED: this Postgres database belongs to a Distributed "
            "install",
            out,
        )
        self.assertEqual(out.splitlines()[-1], "[bootstrap] retrying in 30s")
        self.sleep.assert_called_once_with(30)
        self.assertEqual(self.status()["phase"], "retrying")
        self.assertFalse((self.data / ".bootstrap-ok").exists())
        # The placeholder keeps answering "starting" through the back-off.
        self.assertNotIn("placeholder shutdown", self.stack.log)
        self.execv.assert_not_called()

        # supervisord's next attempt within this boot repeats no summary.
        _, again, _ = self.run_script()
        self.assertIn("Future AGI · Standalone setup", out)
        self.assertNotIn("Future AGI · Standalone setup", again)
        self.assertIn("FAILED:", again)

    def assert_retried_with_a_traceback(self, error: str) -> None:
        code, out, err = self.run_script()

        self.assertEqual(code, 1)
        self.assertIn("Traceback (most recent call last)", err)
        self.assertEqual(err.strip().splitlines()[-1], error)
        self.assertNotIn("FAILED:", out)
        self.assertEqual(out.splitlines()[-1], "[bootstrap] retrying in 30s")
        self.assertEqual(self.status()["phase"], "retrying")
        self.assertFalse((self.data / ".bootstrap-ok").exists())
        self.execv.assert_not_called()

    def test_an_unexpected_error_prints_its_traceback_and_retries(self):
        self.stack.setup_error = KeyError("SECRET_KEY")
        self.assert_retried_with_a_traceback("KeyError: 'SECRET_KEY'")

    def test_a_step_calling_sys_exit_is_a_failed_attempt_not_an_exit(self):
        self.stack.command_errors["migrate"] = SystemExit(0)
        self.assert_retried_with_a_traceback("SystemExit: 0")

    def test_waiter_mode_measures_the_boot_from_the_container_start(self):
        for argv, env, duration in (
            ([str(time.time() - 100)], {}, r"1m 4[01]s"),
            (["junk"], {"FI_BOOT_STARTED_AT": str(time.time() - 200)}, r"3m 2[01]s"),
            ([], {}, r"[01]s"),
        ):
            with self.subTest(argv=argv):
                code, out, _ = self.run_script("--wait-for-api", *argv, **env)

                self.assertEqual(code, 0)
                self.assertRegex(
                    out,
                    rf"^\[bootstrap\] Future AGI is ready in {duration}\. "
                    r"Open http://localhost:3000\n$",
                )
                self.assertTrue((self.run_dir / "ready").exists())
        # The waiter never loads Django or touches a database.
        self.assertEqual(self.stack.log, [])


if __name__ == "__main__":
    unittest.main()
