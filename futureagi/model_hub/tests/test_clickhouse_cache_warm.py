import sys
from unittest.mock import Mock

import pytest

from model_hub.apps import (
    ModelHubConfig,
    cloud_startup_environment,
    guarded_management_command,
    operator_startup_mutation_authorized,
    startup_db_mutations_disabled,
)


@pytest.fixture(autouse=True)
def _local_mutation_guard_environment(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "local")
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.delenv("NO_STARTUP_DB_MUTATIONS", raising=False)
    monkeypatch.delenv("STARTUP_DB_MUTATION_MODE", raising=False)
    monkeypatch.delenv("SERVICE_TYPE", raising=False)


def test_startup_db_mutation_guard_defaults_to_false(monkeypatch):
    assert startup_db_mutations_disabled() is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ENV_TYPE", "prod"),
        ("ENV_TYPE", "production"),
        ("ENV_TYPE", "staging"),
        ("CLOUD_DEPLOYMENT", "US"),
        ("CLOUD_DEPLOYMENT", "EU"),
        ("CLOUD_DEPLOYMENT", "DEV"),
    ],
)
def test_cloud_startup_is_always_mutation_free(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "false")

    assert cloud_startup_environment() is True
    assert startup_db_mutations_disabled() is True


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("true", True)])
def test_startup_db_mutation_guard_accepts_only_explicit_literals(
    monkeypatch, value, expected
):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", value)

    assert startup_db_mutations_disabled() is expected


@pytest.mark.parametrize("value", ["", "TRUE", "False", " true ", "1", "yes"])
def test_startup_db_mutation_guard_rejects_ambiguous_values(monkeypatch, value):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", value)

    with pytest.raises(RuntimeError, match="must be exactly 'true' or 'false'"):
        startup_db_mutations_disabled()


def test_ready_explicit_true_skips_every_startup_mutation_path(monkeypatch):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.setattr(sys, "argv", ["python", "-I", "/sos/run_clickhouse_only_ab.py"])
    seed_evals = Mock()
    create_tables = Mock()
    ensure_schema = Mock()
    warm_cache = Mock()
    monkeypatch.setattr(
        "model_hub.management.commands.seed_system_evals.seed_evals", seed_evals
    )
    monkeypatch.setattr(
        ModelHubConfig, "check_and_create_clickhouse_tables", create_tables
    )
    monkeypatch.setattr(ModelHubConfig, "_ensure_analytics_schema", ensure_schema)
    monkeypatch.setattr(ModelHubConfig, "_warm_ch_cache", warm_cache)

    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    seed_evals.assert_not_called()
    create_tables.assert_not_called()
    ensure_schema.assert_not_called()
    warm_cache.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "ch25_apply_schema",
        "createcachetable",
        "future_schema_command",
        "makemigrations",
        "migrate",
        "register_temporal_schedules",
        "seed_system_evals",
    ],
)
def test_mutation_guard_rejects_unsafe_management_commands(command):
    assert guarded_management_command(["manage.py", command]) == command


@pytest.mark.parametrize(
    "command",
    [
        "ch25_apply_schema",
        "ch25_remove_pg",
        "createcachetable",
        "drop_legacy_observation_span",
        "migrate",
        "register_temporal_schedules",
        "seed_system_evals",
    ],
)
def test_operator_bootstrap_authorizes_only_explicit_commands(monkeypatch, command):
    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")

    assert operator_startup_mutation_authorized(["manage.py", command]) is True


@pytest.mark.parametrize(
    "argv",
    [
        ["manage.py", "shell"],
        ["manage.py", "future_schema_command"],
        ["granian", "--interface", "asgi", "tfc.asgi:application"],
        ["celery", "-A", "tfc", "worker"],
    ],
)
def test_operator_bootstrap_does_not_authorize_open_ended_processes(monkeypatch, argv):
    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")

    assert operator_startup_mutation_authorized(argv) is False


@pytest.mark.parametrize(
    "argv",
    [
        ["manage.py", "check", "--database", "default"],
        ["manage.py", "collectstatic", "--noinput"],
        ["manage.py", "generate_swagger", "/tmp/swagger.json"],
        ["/app/backend/manage.py", "grpcrunaioserver"],
        ["/usr/lib/python3/site-packages/django/__main__.py", "runserver"],
        ["python", "-m", "django", "start_temporal_worker"],
        ["python", "-I", "/sos/run_clickhouse_only_ab.py"],
        ["granian", "--interface", "asgi", "tfc.asgi:application"],
        ["celery", "-A", "tfc", "worker"],
    ],
)
def test_mutation_guard_allows_required_read_only_and_server_commands(argv):
    assert guarded_management_command(argv) is None


def test_ready_rejects_unsafe_management_command_before_pytest_shortcut(monkeypatch):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.setattr(sys, "argv", ["manage.py", "migrate"])

    with pytest.raises(RuntimeError, match="^migrate is disabled"):
        ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()


def test_cloud_ready_blocks_manage_py_shell_before_any_startup_mutation(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    monkeypatch.setattr(sys, "argv", ["manage.py", "shell"])
    create_tables = Mock()
    ensure_schema = Mock()
    monkeypatch.setattr(
        ModelHubConfig, "check_and_create_clickhouse_tables", create_tables
    )
    monkeypatch.setattr(ModelHubConfig, "_ensure_analytics_schema", ensure_schema)

    with pytest.raises(RuntimeError, match="^shell is disabled"):
        ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    create_tables.assert_not_called()
    ensure_schema.assert_not_called()


def test_cloud_operator_schema_command_skips_appconfig_mutations(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")
    monkeypatch.setattr(sys, "argv", ["manage.py", "ch25_apply_schema"])
    seed_evals = Mock()
    create_tables = Mock()
    ensure_schema = Mock()
    monkeypatch.setattr(
        "model_hub.management.commands.seed_system_evals.seed_evals", seed_evals
    )
    monkeypatch.setattr(
        ModelHubConfig, "check_and_create_clickhouse_tables", create_tables
    )
    monkeypatch.setattr(ModelHubConfig, "_ensure_analytics_schema", ensure_schema)

    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    seed_evals.assert_not_called()
    create_tables.assert_not_called()
    ensure_schema.assert_not_called()


def test_cloud_operator_migrate_registers_prompt_label_seed(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")
    monkeypatch.setattr(sys, "argv", ["manage.py", "migrate"])
    connect = Mock()
    monkeypatch.setattr("model_hub.apps.post_migrate.connect", connect)

    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    connect.assert_called_once()
    assert connect.call_args.kwargs["dispatch_uid"] == (
        "model_hub_seed_default_prompt_labels"
    )


def test_cloud_direct_schema_reconciliation_fails_before_clickhouse_client(
    monkeypatch,
):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    get_client = Mock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client", get_client
    )

    with pytest.raises(RuntimeError, match="Implicit database schema/data mutations"):
        ModelHubConfig._ensure_analytics_schema(Mock())

    get_client.assert_not_called()


def test_cache_warmer_never_queries_retired_tracer_trace(monkeypatch):
    ch = Mock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.schema.should_drop_legacy_chain",
        lambda: True,
    )

    ModelHubConfig._warm_ch_cache(ch)

    assert ch.execute_read.call_count == 3
    assert all(
        "tracer_trace" not in call.args[0] for call in ch.execute_read.call_args_list
    )
