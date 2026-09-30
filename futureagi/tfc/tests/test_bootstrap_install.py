"""``manage.py bootstrap_install``: the Helm chart's bootstrap Job, whose
steps the Standalone install's deploy/standalone/bin/bootstrap.py runs too.

No datastore is contacted: every step that would reach Postgres, ClickHouse,
Redis or Temporal is replaced, and the tests pin the order, the authorization
contract and the failure behaviour both install paths rely on.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from model_hub.apps import OPERATOR_STARTUP_MUTATION_COMMANDS
from tfc.management.commands import bootstrap_install as command

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART = REPO_ROOT / "deploy" / "helm" / "futureagi"
STANDALONE_BOOTSTRAP = REPO_ROOT / "deploy" / "standalone" / "bin" / "bootstrap.py"
CATALOG_SCHEMA = (
    REPO_ROOT / "futureagi/tracer/services/clickhouse/v2/observed_catalog/schema.sql"
)
CATALOG_VALIDATION = (
    REPO_ROOT / "futureagi/scripts/property_catalog_oss/validate_clickhouse.sql"
)
CATALOG_SHELL_BOOTSTRAP = (
    REPO_ROOT / "futureagi/scripts/property_catalog_oss/bootstrap_clickhouse.sh"
)
# Before any fixture replaces it.
REGISTER_SEARCH_ATTRIBUTES = command.register_search_attributes


@pytest.fixture
def local_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment the chart gives its bootstrap Job (non-hosted form)."""
    monkeypatch.setenv("ENV_TYPE", "local")
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "false")
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)


@pytest.fixture
def recorded_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every step with a recorder; returns the order they ran in."""
    steps: list[str] = []
    monkeypatch.setattr(
        command,
        "datastore_endpoints",
        lambda env=None: [("Postgres", "pg", 5432), ("Temporal", "temporal", 7233)],
    )
    monkeypatch.setattr(
        command,
        "wait_tcp",
        lambda name, host, port, timeout, log, **_: steps.append(f"wait {name}"),
    )
    monkeypatch.setattr(
        command, "migrate_and_seed", lambda log: steps.append("migrate")
    )
    monkeypatch.setattr(
        command,
        "clickhouse_native_schema",
        lambda log, timeout: steps.append(f"clickhouse {timeout}"),
    )
    monkeypatch.setattr(
        command, "property_catalog", lambda log: steps.append("catalog")
    )
    monkeypatch.setattr(
        command,
        "register_search_attributes",
        lambda log: steps.append("search attributes"),
    )
    monkeypatch.setattr(
        command,
        "change_data_capture",
        lambda log, attempts, delay: steps.append("cdc"),
    )
    monkeypatch.setattr(
        command, "call", lambda name, log, **options: steps.append(name)
    )
    return steps


def test_runs_every_step_in_order(local_operator, recorded_steps) -> None:
    call_command("bootstrap_install", "--clickhouse-timeout", "321")

    assert recorded_steps == [
        "wait Postgres",
        "wait Temporal",
        "migrate",
        "clickhouse 321",
        "catalog",
        "search attributes",
        "cdc",
        "register_temporal_schedules",
    ]


@pytest.mark.skipif(
    not STANDALONE_BOOTSTRAP.is_file(), reason="deploy/ is not in this tree"
)
def test_the_standalone_bootstrap_runs_the_same_steps_in_the_same_order(
    local_operator, recorded_steps, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    call_command("bootstrap_install")
    job = list(recorded_steps)
    recorded_steps.clear()
    spec = importlib.util.spec_from_file_location(
        "standalone_bootstrap", STANDALONE_BOOTSTRAP
    )
    standalone = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone)
    # Only its own steps are replaced: no volume, no status file, a first boot
    # of this image on the install's own database, static files collected.
    for name, value in (
        ("READY", tmp_path / ".bootstrap-ok"),
        ("FINGERPRINT", tmp_path / ".bootstrap-fingerprint"),
        ("STATIC_COLLECTED", tmp_path / "static-collected"),
        ("configure_django", lambda: None),
        ("write_status", lambda phase: None),
        ("refuse_foreign_database", lambda: False),
        ("schema_fingerprint", lambda: ("first boot", [])),
    ):
        monkeypatch.setattr(standalone, name, value)
    (tmp_path / "static-collected").touch()
    monkeypatch.setattr("django.setup", lambda: None)

    standalone.main()

    assert recorded_steps == job


def test_property_catalog_can_be_skipped(local_operator, recorded_steps) -> None:
    call_command("bootstrap_install", "--skip-property-catalog")

    assert "catalog" not in recorded_steps
    assert recorded_steps[-1] == "register_temporal_schedules"


@pytest.mark.parametrize(
    ("env", "hint"),
    [
        ({"ENV_TYPE": "local"}, "NO_STARTUP_DB_MUTATIONS=false"),
        ({"ENV_TYPE": "production"}, "STARTUP_DB_MUTATION_MODE=operator"),
        (
            {"ENV_TYPE": "production", "NO_STARTUP_DB_MUTATIONS": "false"},
            "SERVICE_TYPE=bootstrap",
        ),
    ],
)
def test_refuses_to_change_databases_without_authorization(
    monkeypatch: pytest.MonkeyPatch, recorded_steps, env, hint
) -> None:
    for name in ("NO_STARTUP_DB_MUTATIONS", "SERVICE_TYPE", "STARTUP_DB_MUTATION_MODE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(command.BootstrapError, match=hint):
        call_command("bootstrap_install")

    assert recorded_steps == []


def test_hosted_env_type_runs_as_an_operator_job(
    monkeypatch: pytest.MonkeyPatch, recorded_steps
) -> None:
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "false")
    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")

    call_command("bootstrap_install")

    assert recorded_steps[-1] == "register_temporal_schedules"


@pytest.mark.xfail(
    "bootstrap_install" not in OPERATOR_STARTUP_MUTATION_COMMANDS,
    reason=(
        "`manage.py bootstrap_install` needs 'bootstrap_install' in "
        "model_hub.apps.OPERATOR_STARTUP_MUTATION_COMMANDS, or "
        "ModelHubConfig.ready() refuses to start it"
    ),
    strict=True,
)
@pytest.mark.parametrize(
    "env",
    [
        {"ENV_TYPE": "local", "NO_STARTUP_DB_MUTATIONS": "false"},
        {
            "ENV_TYPE": "production",
            "NO_STARTUP_DB_MUTATIONS": "false",
            "SERVICE_TYPE": "bootstrap",
            "STARTUP_DB_MUTATION_MODE": "operator",
        },
    ],
)
def test_startup_guard_lets_the_bootstrap_job_start(
    monkeypatch: pytest.MonkeyPatch, env
) -> None:
    from model_hub.apps import explicit_management_mutation_authorized

    # The test settings may export a hosted CLOUD_DEPLOYMENT; a self-hosted
    # bootstrap Job never has one.
    for name in ("CLOUD_DEPLOYMENT", "SERVICE_TYPE", "STARTUP_DB_MUTATION_MODE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert explicit_management_mutation_authorized(["manage.py", "bootstrap_install"])


def test_migrate_and_seed_order_and_prompt_label_hook(
    local_operator, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[tuple[str, dict]] = []
    connected: list[str] = []

    def fake_call_command(name, **options):
        if name == "createcachetable":
            raise RuntimeError("cache table exists in another schema")
        ran.append((name, options))

    monkeypatch.setattr("django.core.management.call_command", fake_call_command)
    monkeypatch.setattr(
        "django.db.models.signals.post_migrate.connect",
        lambda receiver, sender=None, dispatch_uid=None: connected.append(dispatch_uid),
    )
    logged: list[str] = []

    command.migrate_and_seed(logged.append)

    # createcachetable failing is logged and does not stop the bootstrap.
    assert [name for name, _ in ran] == ["migrate", "seed_system_evals"]
    assert ran[0][1] == {"interactive": False, "verbosity": 1}
    assert connected == ["model_hub_seed_default_prompt_labels"]
    assert any("createcachetable failed (continuing)" in line for line in logged)


def test_clickhouse_native_schema_failure_is_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse import oss_cdc_install

    seen: list[list[str]] = []

    def fake_main(argv):
        seen.append(argv)
        return 1

    monkeypatch.setattr(oss_cdc_install, "main", fake_main)

    with pytest.raises(command.BootstrapError, match="ClickHouse native schema"):
        command.clickhouse_native_schema(lambda _: None, 42)

    assert seen == [["--phase", "native", "--apply", "--timeout", "42"]]


def test_wait_tcp_gives_up_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args, **_kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr(command.socket, "create_connection", refuse)
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    logged: list[str] = []
    with pytest.raises(command.BootstrapError, match="ClickHouse at ch:8123"):
        command.wait_tcp(
            "ClickHouse",
            "ch",
            8123,
            5,
            logged.append,
            clock=lambda: now[0],
            sleep=sleep,
        )
    assert logged == ["waiting for ClickHouse at ch:8123"]


def test_with_retries_retries_transient_errors_only() -> None:
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("temporal not serving yet")

    command.with_retries(
        "step", flaky, lambda _: None, attempts=5, sleep=lambda _: None
    )
    assert len(calls) == 3

    def final() -> None:
        calls.append(1)
        raise command.BootstrapError("configuration is wrong")

    calls.clear()
    with pytest.raises(command.BootstrapError):
        command.with_retries(
            "step", final, lambda _: None, attempts=5, sleep=lambda _: None
        )
    assert len(calls) == 1


def _outbox_module_in(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Import the outbox module from ``directory`` only: as an image without
    the module when it is empty."""
    import tracer.services.clickhouse as package

    monkeypatch.setattr(package, "__path__", [str(directory)])
    monkeypatch.delattr(package, "oss_outbox_cdc", raising=False)
    monkeypatch.delitem(sys.modules, command.OUTBOX_CDC_MODULE, raising=False)


def test_change_data_capture_skips_an_image_without_the_outbox_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _outbox_module_in(monkeypatch, tmp_path)
    logged: list[str] = []

    command.change_data_capture(logged.append, attempts=1, delay=0)

    assert logged == [
        "this image has no outbox CDC installer; skipping change data capture"
    ]


def test_change_data_capture_reports_an_import_error_inside_the_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "oss_outbox_cdc.py").write_text(
        "import fi_missing_dependency_for_this_test\n", encoding="utf-8"
    )
    _outbox_module_in(monkeypatch, tmp_path)
    logged: list[str] = []

    # Not mistaken for an image without the module.
    with pytest.raises(ModuleNotFoundError) as raised:
        command.change_data_capture(logged.append, attempts=1, delay=0)

    assert raised.value.name == "fi_missing_dependency_for_this_test"
    assert logged == []


def test_change_data_capture_installer_errors_are_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse import oss_outbox_cdc

    monkeypatch.setenv("FI_CDC_MODE", "outbox")
    attempts: list[int] = []

    def refuse():
        attempts.append(1)
        raise oss_outbox_cdc.OutboxCDCError("a running PeerDB still owns the slots")

    monkeypatch.setattr(oss_outbox_cdc, "ensure_installed", refuse)

    with pytest.raises(command.BootstrapError, match="FI_CDC_MODE=outbox"):
        command.change_data_capture(lambda _: None, attempts=5, delay=0)
    assert attempts == [1]


def test_change_data_capture_hides_driver_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse import oss_outbox_cdc

    monkeypatch.setenv("FI_CDC_MODE", "outbox")

    def leak():
        raise OSError("password=hunter2 rejected")

    monkeypatch.setattr(oss_outbox_cdc, "ensure_installed", leak)
    monkeypatch.setattr(command.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError) as raised:
        command.change_data_capture(lambda _: None, attempts=2, delay=0)
    assert "hunter2" not in str(raised.value)
    assert "OSError" in str(raised.value)
    # `from None`: the traceback carries no driver text either.
    assert raised.value.__suppress_context__


def test_datastore_endpoints_follow_the_configured_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        command,
        "settings",
        SimpleNamespace(
            DATABASES={"default": {"HOST": "pg.data", "PORT": "5432"}},
            REDIS_URL="redis://:secret@redis.data:6380/0",
        ),
    )
    env = {
        "PG_HOST": "pg.data",
        "PG_PORT": "5432",
        "CH_HOST": "ch.data",
        "CH_HTTP_PORT": "8123",
        "TEMPORAL_HOST": "temporal-frontend.temporal:7233",
    }

    assert command.datastore_endpoints(env) == [
        ("Postgres", "pg.data", 5432),
        ("ClickHouse", "ch.data", 8123),
        ("Redis", "redis.data", 6380),
        ("Temporal", "temporal-frontend.temporal", 7233),
    ]


class FakeClickHouse:
    """Records connections, statements and closes in one ordered log."""

    def __init__(self, fake: SimpleNamespace, **connect) -> None:
        self.fake = fake
        self.database = connect.get("database")
        fake.log.append(("connect", connect))

    def command(self, sql, parameters=None):
        self.fake.log.append(("command", self.database, sql, parameters))
        if self.fake.fail_on and self.fake.fail_on in sql:
            raise RuntimeError("read-only")

    def query(self, sql, parameters=None):
        self.fake.log.append(("query", self.database, sql, parameters))
        return SimpleNamespace(result_rows=self.fake.rows)

    def close(self) -> None:
        self.fake.log.append(("close", self.database))


@pytest.fixture
def clickhouse(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """ClickHouse as property_catalog() sees it: ``log`` is what it did,
    ``rows`` the validation result, ``fail_on`` a statement that fails."""
    import clickhouse_connect

    fake = SimpleNamespace(log=[], rows=[(1,)], fail_on=None)
    monkeypatch.setattr(
        clickhouse_connect,
        "get_client",
        lambda **connect: FakeClickHouse(fake, **connect),
    )
    return fake


CATALOG_ENV = {
    "CH_HOST": "ch",
    "CH_HTTP_PORT": "8124",
    "CH_USERNAME": "admin",
    "CH_PASSWORD": "ch-secret",
    "CH_DATABASE": "traces",
    "PROPERTY_CATALOG_DATABASE": "catalog",
    "PROPERTY_CATALOG_CONSUMER_PASSWORD": "writer-secret",
    "PROPERTY_CATALOG_CH_PASSWORD": "reader-secret",
}


def _user_and_grant_statements(log: list) -> list:
    return [
        event
        for event in log
        if event[0] == "command"
        and event[2].startswith(("CREATE USER", "ALTER USER", "GRANT"))
    ]


def test_property_catalog_creates_the_index_users_and_grants(clickhouse) -> None:
    from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements

    logged: list[str] = []

    command.property_catalog(logged.append, CATALOG_ENV)

    connect = {"host": "ch", "port": 8124, "username": "admin", "password": "ch-secret"}
    identified = "IDENTIFIED WITH sha256_password BY {password:String} HOST ANY"
    writer = {"password": "writer-secret"}
    reader = {"password": "reader-secret"}
    tables = split_statements(CATALOG_SCHEMA.read_text())
    assert len(tables) == 2
    assert all(sql.startswith("CREATE TABLE IF NOT EXISTS") for sql in tables)
    grants = []
    for table in ("observed_attribute_keys", "observed_attribute_values"):
        grants += [
            (
                "command",
                None,
                f"GRANT SELECT, INSERT ON `catalog`.{table} TO observed_catalog_writer",
                None,
            ),
            (
                "command",
                None,
                f"GRANT SELECT ON `catalog`.{table} TO observed_catalog_reader",
                None,
            ),
        ]
    assert clickhouse.log == [
        ("connect", connect),
        ("command", None, "CREATE DATABASE IF NOT EXISTS `catalog`", None),
        ("connect", {"database": "catalog", **connect}),
        *[("command", "catalog", sql, None) for sql in tables],
        ("close", "catalog"),
        (
            "query",
            None,
            CATALOG_VALIDATION.read_text().strip().rstrip(";"),
            {"database": "catalog"},
        ),
        (
            "command",
            None,
            f"CREATE USER IF NOT EXISTS observed_catalog_writer {identified}",
            writer,
        ),
        ("command", None, f"ALTER USER observed_catalog_writer {identified}", writer),
        (
            "command",
            None,
            f"CREATE USER IF NOT EXISTS observed_catalog_reader {identified}",
            reader,
        ),
        (
            "command",
            None,
            f"ALTER USER observed_catalog_reader {identified} SETTINGS readonly=2",
            reader,
        ),
        *grants,
        ("close", None),
    ]
    assert logged == [
        "observed-attribute index in catalog ...",
        "observed-attribute index done",
    ]


def test_property_catalog_defaults_match_the_compose_files(clickhouse) -> None:
    command.property_catalog(lambda _: None, {"CH_USER": "legacy", "CH_HTTP_PORT": ""})

    assert clickhouse.log[0] == (
        "connect",
        {"host": "clickhouse", "port": 8123, "username": "legacy", "password": ""},
    )
    assert clickhouse.log[1][2] == "CREATE DATABASE IF NOT EXISTS `property_catalog`"
    assert [
        parameters["password"]
        for _, _, sql, parameters in _user_and_grant_statements(clickhouse.log)
        if "USER" in sql
    ] == [command.CATALOG_WRITER_DEFAULT] * 2 + [command.CATALOG_READER_DEFAULT] * 2


def test_property_catalog_users_and_grants_match_the_shell_bootstrap(
    clickhouse,
) -> None:
    """The Distributed stack runs bootstrap_clickhouse.sh instead (no Python
    in the ClickHouse image); both must create the same users and grants."""
    script = CATALOG_SHELL_BOOTSTRAP.read_text()
    script = re.sub(
        r"(?ms)^[ \t]*for table in ([\w ]+); do\n(.*?)^[ \t]*done$",
        lambda loop: "".join(
            loop[2].replace("$table", table) for table in loop[1].split()
        ),
        script,
    )
    passwords = {
        "WRITER": {"password": "writer-secret"},
        "READER": {"password": "reader-secret"},
        "": None,
    }
    shell = [
        (
            sql.replace("\\`", "`").replace("$TARGET_DATABASE", "catalog"),
            passwords[password],
        )
        for password, sql in re.findall(
            r'clickhouse (?:--param_password "\$(WRITER|READER)_PARAMETER" )?'
            r'--query "((?:CREATE USER|ALTER USER|GRANT) [^"]*)"',
            script,
        )
    ]

    command.property_catalog(lambda _: None, CATALOG_ENV)

    assert [
        (sql, parameters)
        for _, _, sql, parameters in _user_and_grant_statements(clickhouse.log)
    ] == shell
    assert dict(
        re.findall(r"(?m)^(WRITER|READER)_PASSWORD=\$\{\w+:-([^}]*)\}$", script)
    ) == {
        "WRITER": command.CATALOG_WRITER_DEFAULT,
        "READER": command.CATALOG_READER_DEFAULT,
    }


@pytest.mark.parametrize("rows", [[(1,)], [(True,)]])
def test_property_catalog_accepts_a_compatible_index(clickhouse, rows) -> None:
    clickhouse.rows = rows

    command.property_catalog(lambda _: None, CATALOG_ENV)

    assert len(_user_and_grant_statements(clickhouse.log)) == 8


@pytest.mark.parametrize("rows", [[(0,)], [(False,)], []])
def test_property_catalog_grants_nothing_on_an_incompatible_index(
    clickhouse, rows
) -> None:
    clickhouse.rows = rows

    with pytest.raises(
        command.BootstrapError,
        match="^observed-attribute index in catalog is incompatible",
    ):
        command.property_catalog(lambda _: None, CATALOG_ENV)

    assert _user_and_grant_statements(clickhouse.log) == []
    assert clickhouse.log[-1] == ("close", None)


def test_property_catalog_closes_both_clients_when_a_statement_fails(
    clickhouse,
) -> None:
    clickhouse.fail_on = "CREATE TABLE"

    with pytest.raises(RuntimeError, match="read-only"):
        command.property_catalog(lambda _: None, CATALOG_ENV)

    assert clickhouse.log[-2:] == [("close", "catalog"), ("close", None)]
    assert not [event for event in clickhouse.log if event[0] == "query"]


def test_summary_never_prints_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        command,
        "settings",
        SimpleNamespace(
            DATABASES={
                "default": {
                    "HOST": "pg",
                    "PORT": "5432",
                    "NAME": "futureagi",
                    "USER": "futureagi",
                    "PASSWORD": "pg-secret",
                }
            },
            REDIS_URL="redis://:redis-secret@redis:6379/0",
        ),
    )
    text = "\n".join(
        command.summary({"CH_PASSWORD": "ch-secret", "FUTURE_AGI_VERSION": "v9.9.9"})
    )

    assert "v9.9.9" in text
    for secret in ("pg-secret", "redis-secret", "ch-secret"):
        assert secret not in text


@pytest.mark.skipif(not CHART.is_dir(), reason="the Helm chart is not in this tree")
def test_the_helm_chart_bootstrap_job_runs_this_command() -> None:
    job = (CHART / "templates" / "bootstrap" / "job.yaml").read_text()

    assert '"manage.py", "bootstrap_install"' in job
    assert "NO_STARTUP_DB_MUTATIONS" in (CHART / "templates" / "_env.tpl").read_text()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("temporal-frontend:7234", ("temporal-frontend", 7234)),
        ("temporal-frontend", ("temporal-frontend", 7233)),
        ("[fd00::1]:7234", ("fd00::1", 7234)),
        ("[fd00::1]", ("fd00::1", 7233)),
    ],
)
def test_endpoint_takes_host_and_port_or_a_bare_host(value, expected) -> None:
    assert command.endpoint(value, 7233) == expected


@pytest.mark.parametrize(
    "value", ["temporal:grpc", "temporal:", ":7233", "[fd00::1]:port"]
)
def test_endpoint_refuses_a_port_that_is_not_a_number(value) -> None:
    # Otherwise the whole value is a hostname and the wait times out.
    with pytest.raises(command.BootstrapError, match="the port must be a number"):
        command.endpoint(value, 7233)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("redis://:secret@redis.data:6380/0", ("redis.data", 6380)),
        ("redis://redis.data/0", ("redis.data", 6379)),
        ("redis://redis.data:port/0", None),
        ("unix:///run/redis.sock", None),
        ("", None),
    ],
)
def test_url_endpoint_needs_a_host_and_a_numeric_port(url, expected) -> None:
    assert command.url_endpoint(url, 6379) == expected


@pytest.mark.parametrize("redis_url", ["", "unix:///run/redis.sock"])
def test_datastore_endpoints_fall_back_to_redis_host_and_the_defaults(
    monkeypatch: pytest.MonkeyPatch, redis_url: str
) -> None:
    monkeypatch.setattr(
        command,
        "settings",
        SimpleNamespace(
            DATABASES={"default": {"HOST": "pgbouncer", "PORT": 6432}},
            REDIS_URL=redis_url,
        ),
    )
    env = {
        "PG_HOST": "postgres",
        "PG_PORT": "",
        "CH_HTTP_PORT": "",
        "REDIS_HOST": "cache",
        "REDIS_PORT": "6390",
    }

    # A pooler in front of Postgres: both it and Postgres itself are waited for.
    assert command.datastore_endpoints(env) == [
        ("Postgres", "pgbouncer", 6432),
        ("Postgres", "postgres", 5432),
        ("ClickHouse", "clickhouse", 8123),
        ("Redis", "cache", 6390),
        ("Temporal", "localhost", 7233),
    ]


def test_wait_tcp_returns_once_the_port_accepts_and_announces_every_30s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[str, int]] = []
    closed: list[bool] = []

    def connect(address, timeout):
        attempts.append(address)
        if len(attempts) <= 3:
            raise ConnectionRefusedError
        return SimpleNamespace(close=lambda: closed.append(True))

    monkeypatch.setattr(command.socket, "create_connection", connect)
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += 20 * seconds

    logged: list[str] = []
    command.wait_tcp(
        "Redis", "redis", 6379, 100, logged.append, clock=lambda: now[0], sleep=sleep
    )

    assert attempts == [("redis", 6379)] * 4
    assert closed == [True]
    # At 0 s and 40 s; not again at 20 s.
    assert logged == ["waiting for Redis at redis:6379"] * 2


def test_run_cli_turns_returns_and_exits_into_an_exit_code() -> None:
    def exits(code):
        def main(argv):
            raise SystemExit(code)

        return main

    seen: list[list[str]] = []
    assert command.run_cli(seen.append, ["--phase", "native"]) == 0
    assert seen == [["--phase", "native"]]
    assert command.run_cli(lambda argv: 3, []) == 3
    assert command.run_cli(exits(0), []) == 0
    assert command.run_cli(exits(4), []) == 4
    # sys.exit("message") prints the message and exits 1.
    assert command.run_cli(exits("invalid --phase"), []) == 1


@pytest.fixture
def unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hosted process without the operator/bootstrap pair."""
    for name in ("NO_STARTUP_DB_MUTATIONS", "SERVICE_TYPE", "STARTUP_DB_MUTATION_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV_TYPE", "production")


def test_call_refuses_a_mutation_command_this_process_may_not_run(
    unauthorized, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []
    monkeypatch.setattr(
        "django.core.management.call_command",
        lambda name, **options: ran.append(name),
    )

    with pytest.raises(
        command.BootstrapError,
        match="migrate is not authorized here.*SERVICE_TYPE=bootstrap",
    ):
        command.call("migrate", lambda _: None)
    # A failing createcachetable is otherwise logged and skipped; a refusal
    # stops the bootstrap before migrate.
    with pytest.raises(
        command.BootstrapError, match="createcachetable is not authorized"
    ):
        command.migrate_and_seed(lambda _: None)
    assert ran == []

    # Commands outside the mutation allowlist need no authorization.
    command.call("check", lambda _: None)
    assert ran == ["check"]


def test_clickhouse_native_schema_success_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse import oss_cdc_install

    monkeypatch.setattr(oss_cdc_install, "main", lambda argv: 0)
    logged: list[str] = []

    command.clickhouse_native_schema(logged.append, 600)

    assert logged[0] == "ClickHouse native schema ..."
    assert logged[1].startswith("ClickHouse native schema done in ")
    assert len(logged) == 2


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"CH_DATABASE": "traces; DROP USER default"},
            "source database must be a ClickHouse identifier",
        ),
        (
            {"CH_DATABASE": "traces", "PROPERTY_CATALOG_DATABASE": "catalog`x"},
            "PROPERTY_CATALOG_DATABASE database must be a ClickHouse identifier",
        ),
        # FI_CH_DATABASE, then CH25_DATABASE, is the trace database over CH_DATABASE.
        (
            {"FI_CH_DATABASE": "property_catalog", "CH_DATABASE": "default"},
            "its own database",
        ),
        (
            {"CH25_DATABASE": "property_catalog", "CH_DATABASE": "default"},
            "its own database",
        ),
        (
            {"CH_DATABASE": "default", "PROPERTY_CATALOG_DATABASE": "default"},
            "its own database",
        ),
        ({"PROPERTY_CATALOG_DATABASE": "System"}, "its own database"),
        ({"PROPERTY_CATALOG_DATABASE": "information_schema"}, "its own database"),
    ],
)
def test_property_catalog_checks_database_names_before_connecting(
    monkeypatch: pytest.MonkeyPatch, env, message
) -> None:
    import clickhouse_connect

    monkeypatch.setattr(
        clickhouse_connect, "get_client", lambda **_: pytest.fail("connected")
    )

    with pytest.raises(command.BootstrapError, match=message):
        command.property_catalog(lambda _: None, env)


class FakeOperatorService:
    """Temporal's operator API: the namespace's custom search attributes."""

    def __init__(self, existing) -> None:
        self.existing = set(existing)
        self.requests: list[tuple] = []

    async def list_search_attributes(self, request):
        self.requests.append(("list", request.namespace))
        return SimpleNamespace(custom_attributes=dict.fromkeys(self.existing, 2))

    async def add_search_attributes(self, request):
        added = sorted(request.search_attributes)
        self.requests.append(("add", request.namespace, added))
        self.existing.update(added)


def test_register_search_attributes_adds_only_the_missing_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tfc.temporal
    from tfc.temporal.common import client as temporal_client
    from tfc.temporal.eval_tasks.search_attributes import SEARCH_ATTRIBUTE_NAMES

    service = FakeOperatorService(SEARCH_ATTRIBUTE_NAMES[:1])

    async def get_client():
        return SimpleNamespace(operator_service=service)

    monkeypatch.setattr(temporal_client, "get_client", get_client)
    monkeypatch.setattr(tfc.temporal, "TEMPORAL_NAMESPACE", "futureagi-helm")
    logged: list[str] = []

    command.register_search_attributes(logged.append)
    command.register_search_attributes(logged.append)

    assert service.requests == [
        ("list", "futureagi-helm"),
        ("add", "futureagi-helm", sorted(SEARCH_ATTRIBUTE_NAMES[1:])),
        ("list", "futureagi-helm"),
    ]
    assert logged == [
        "registered the eval-task search attributes",
        "eval-task search attributes already registered",
    ]


def test_the_job_retries_search_attributes_until_temporal_serves(
    local_operator, recorded_steps, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tfc.temporal.common import client as temporal_client
    from tfc.temporal.eval_tasks.search_attributes import SEARCH_ATTRIBUTE_NAMES

    connects: list[int] = []

    async def get_client():
        connects.append(1)
        if len(connects) == 1:
            raise RuntimeError("Temporal not serving yet")
        return SimpleNamespace(
            operator_service=FakeOperatorService(SEARCH_ATTRIBUTE_NAMES)
        )

    monkeypatch.setattr(temporal_client, "get_client", get_client)
    monkeypatch.setattr(
        command, "register_search_attributes", REGISTER_SEARCH_ATTRIBUTES
    )
    sleeps: list[float] = []
    with_retries = command.with_retries
    monkeypatch.setattr(
        command,
        "with_retries",
        lambda *args, **kwargs: with_retries(*args, **kwargs, sleep=sleeps.append),
    )
    out = io.StringIO()

    call_command("bootstrap_install", "--temporal-attempts", "2", stdout=out)

    assert len(connects) == 2 and sleeps == [5]
    lines = out.getvalue().splitlines()
    assert (
        "[bootstrap] Temporal search attributes failed "
        "(RuntimeError: Temporal not serving yet); retrying in 5s"
    ) in lines
    assert "[bootstrap] eval-task search attributes already registered" in lines
    assert recorded_steps[-2:] == ["cdc", "register_temporal_schedules"]

    # Out of attempts: the Job fails before change data capture, without
    # sleeping after its last attempt.
    connects.clear()
    recorded_steps.clear()
    sleeps.clear()
    with pytest.raises(RuntimeError, match="Temporal not serving yet"):
        call_command("bootstrap_install", "--temporal-attempts", "1", stdout=out)
    assert "cdc" not in recorded_steps
    assert sleeps == []


def test_an_unknown_cdc_mode_fails_before_the_installer_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse import oss_outbox_cdc

    monkeypatch.setenv("FI_CDC_MODE", "outbax")
    monkeypatch.setattr(
        oss_outbox_cdc, "ensure_installed", lambda: pytest.fail("installer ran")
    )
    logged: list[str] = []

    with pytest.raises(
        command.BootstrapError, match="FI_CDC_MODE must be one of"
    ) as raised:
        command.change_data_capture(logged.append, attempts=3, delay=0)

    assert raised.value.__suppress_context__
    assert logged == []


def test_change_data_capture_retries_transient_failures_and_logs_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse import oss_outbox_cdc

    monkeypatch.setenv("FI_CDC_MODE", " Outbox ")
    outcomes = iter(
        [
            ConnectionError("clickhouse restarting"),
            {"mode": "outbox", "ready": True, "run": uuid.UUID(int=7)},
        ]
    )

    def ensure_installed():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(oss_outbox_cdc, "ensure_installed", ensure_installed)
    logged: list[str] = []

    command.change_data_capture(logged.append, attempts=2, delay=0)

    assert logged == [
        "change data capture (FI_CDC_MODE=outbox) ...",
        "change data capture failed "
        "(RuntimeError: ConnectionError from the CDC installer); retrying in 0s",
        'change data capture: {"mode": "outbox", "ready": true, '
        '"run": "00000000-0000-0000-0000-000000000007"}',
    ]
