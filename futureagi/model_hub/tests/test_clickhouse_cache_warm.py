import sys
from unittest.mock import Mock

import pytest

from model_hub.apps import (
    ModelHubConfig,
    explicit_management_mutation_authorized,
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


def test_startup_db_mutation_guard_defaults_to_fail_closed(monkeypatch):
    assert startup_db_mutations_disabled() is True


@pytest.mark.parametrize("value", ["false", "true"])
def test_startup_db_mutation_guard_literals_cannot_enable_implicit_mutations(
    monkeypatch, value
):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", value)

    assert startup_db_mutations_disabled() is True


@pytest.mark.parametrize("value", ["", "TRUE", "False", " true ", "1", "yes"])
def test_startup_db_mutation_guard_rejects_ambiguous_values(monkeypatch, value):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", value)

    with pytest.raises(RuntimeError, match="must be exactly 'true' or 'false'"):
        startup_db_mutations_disabled()


def test_python_c_without_deployment_env_skips_every_startup_mutation_path(monkeypatch):
    monkeypatch.delenv("ENV_TYPE", raising=False)
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.delenv("NO_STARTUP_DB_MUTATIONS", raising=False)
    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    monkeypatch.setattr(sys, "argv", ["python", "-I", "/sos/run_clickhouse_only_ab.py"])
    connect = Mock()
    monkeypatch.setattr("model_hub.apps.post_migrate.connect", connect)

    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    connect.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "backfill_score_tracer_project",
        "ch25_apply_schema",
        "ch25_property_catalog_dev_rollout",
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
        "backfill_score_tracer_project",
        "ch25_apply_schema",
        "ch25_property_catalog_dev_rollout",
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


@pytest.mark.parametrize("command", ["createcachetable", "migrate"])
def test_local_entrypoint_authorizes_explicit_database_commands(monkeypatch, command):
    monkeypatch.setenv("ENV_TYPE", "development")
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "false")

    assert explicit_management_mutation_authorized(["manage.py", command]) is True


@pytest.mark.parametrize("env_type", ["prod", "production", "staging"])
def test_hosted_backend_cannot_use_local_explicit_database_mode(monkeypatch, env_type):
    monkeypatch.setenv("ENV_TYPE", env_type)
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "false")

    assert explicit_management_mutation_authorized(["manage.py", "migrate"]) is False


def test_local_migrate_registers_only_post_migrate_seed(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "development")
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "false")
    monkeypatch.setattr(sys, "argv", ["manage.py", "migrate"])
    connect = Mock()
    monkeypatch.setattr("model_hub.apps.post_migrate.connect", connect)

    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    connect.assert_called_once()


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
        ["manage.py", "ch25_activate_attribute_catalog"],
        ["manage.py", "ch25_backfill_attribute_catalog"],
        ["manage.py", "ch25_property_catalog_oss_supervisor"],
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
    with pytest.raises(RuntimeError, match="^shell is disabled"):
        ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()


def test_cloud_operator_schema_command_skips_appconfig_mutations(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")
    monkeypatch.setattr(sys, "argv", ["manage.py", "ch25_apply_schema"])
    connect = Mock()
    monkeypatch.setattr("model_hub.apps.post_migrate.connect", connect)

    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    connect.assert_not_called()


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


def test_appconfig_exposes_no_implicit_database_bootstrap_hooks():
    assert not hasattr(ModelHubConfig, "_ensure_analytics_schema")
    assert not hasattr(ModelHubConfig, "check_and_create_clickhouse_tables")
    assert not hasattr(ModelHubConfig, "_warm_ch_cache")
