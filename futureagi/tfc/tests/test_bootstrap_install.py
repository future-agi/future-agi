"""``manage.py bootstrap_install``: the Helm chart's bootstrap Job.

No datastore is contacted: every step that would reach Postgres, ClickHouse,
Redis or Temporal is replaced, and the tests pin the order, the authorization
contract and the failure behaviour the chart relies on.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from model_hub.apps import OPERATOR_STARTUP_MUTATION_COMMANDS
from tfc.management.commands import bootstrap_install as command

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART = REPO_ROOT / "deploy" / "helm" / "futureagi"


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


def test_runs_every_step_in_the_platform_bootstrap_order(
    local_operator, recorded_steps
) -> None:
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
    def __init__(self, log: list, compatible: bool = True, **connect) -> None:
        self.log = log
        self.connect = connect
        self.compatible = compatible

    def command(self, sql, parameters=None):
        self.log.append((self.connect.get("database"), sql, parameters))

    def query(self, sql, parameters=None):
        self.log.append(("query", sql, parameters))
        return SimpleNamespace(result_rows=[(1,)] if self.compatible else [(0,)])

    def close(self) -> None:
        pass


@pytest.mark.parametrize("compatible", [True, False])
def test_property_catalog_creates_the_index_users_and_grants(
    monkeypatch: pytest.MonkeyPatch, compatible: bool
) -> None:
    import clickhouse_connect

    statements: list = []
    monkeypatch.setattr(
        clickhouse_connect,
        "get_client",
        lambda **connect: FakeClickHouse(statements, compatible, **connect),
    )
    env = {
        "CH_HOST": "ch",
        "CH_HTTP_PORT": "8123",
        "CH_USERNAME": "default",
        "CH_PASSWORD": "admin",
        "CH_DATABASE": "default",
        "PROPERTY_CATALOG_DATABASE": "property_catalog",
        "PROPERTY_CATALOG_CH_PASSWORD": "reader-pw",
        "PROPERTY_CATALOG_CONSUMER_PASSWORD": "writer-pw",
    }

    if not compatible:
        with pytest.raises(command.BootstrapError, match="incompatible"):
            command.property_catalog(lambda _: None, env)
        return
    command.property_catalog(lambda _: None, env)

    sql = [entry[1] for entry in statements if entry[0] != "query"]
    assert sql[0] == "CREATE DATABASE IF NOT EXISTS `property_catalog`"
    users: dict[str, set[str]] = {}
    for database, text, parameters in statements:
        if database == "query" or not text.startswith(("CREATE USER", "ALTER USER")):
            continue
        words = text.split()
        name = words[5] if text.startswith("CREATE USER IF NOT EXISTS") else words[2]
        users.setdefault(name, set()).add(parameters["password"])
    assert users == {
        "observed_catalog_writer": {"writer-pw"},
        "observed_catalog_reader": {"reader-pw"},
    }
    assert (
        "GRANT SELECT ON `property_catalog`.observed_attribute_keys TO observed_catalog_reader"
        in sql
    )
    assert (
        "GRANT SELECT, INSERT ON `property_catalog`.observed_attribute_values "
        "TO observed_catalog_writer" in sql
    )


def test_property_catalog_refuses_to_share_the_trace_database() -> None:
    with pytest.raises(command.BootstrapError, match="its own database"):
        command.property_catalog(
            lambda _: None,
            {"CH_DATABASE": "default", "PROPERTY_CATALOG_DATABASE": "default"},
        )


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
