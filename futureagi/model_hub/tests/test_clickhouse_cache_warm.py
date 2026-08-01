import sys
from unittest.mock import Mock

import pytest

from model_hub.apps import (
    ModelHubConfig,
    guarded_management_command,
    startup_db_mutations_disabled,
)


@pytest.mark.parametrize("value", ["true", "TRUE", " true "])
def test_startup_db_mutation_gate_disables_mutations(monkeypatch, value):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", value)

    assert startup_db_mutations_disabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", " false "])
def test_startup_db_mutation_gate_preserves_default_startup(monkeypatch, value):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", value)

    assert startup_db_mutations_disabled() is False


def test_startup_db_mutation_gate_fails_closed_on_invalid_value(monkeypatch):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "maybe")

    with pytest.raises(RuntimeError, match="must be exactly"):
        startup_db_mutations_disabled()


def test_ready_skips_clickhouse_schema_setup_when_mutations_disabled(monkeypatch):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.setattr(sys, "argv", ["manage.py", "runserver"])
    create_tables = Mock()
    ensure_schema = Mock()
    monkeypatch.setattr(
        ModelHubConfig, "check_and_create_clickhouse_tables", create_tables
    )
    monkeypatch.setattr(ModelHubConfig, "_ensure_analytics_schema", ensure_schema)

    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()

    create_tables.assert_not_called()
    ensure_schema.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "ch25_apply_schema",
        "createcachetable",
        "future_schema_command",
        "makemigrations",
        "migrate",
        "seed_system_evals",
    ],
)
def test_ready_rejects_schema_mutation_commands_when_disabled(monkeypatch, command):
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.setattr(sys, "argv", ["manage.py", command])

    with pytest.raises(RuntimeError, match=rf"^{command} is disabled"):
        ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()


@pytest.mark.parametrize(
    "argv",
    [
        ["manage.py", "check", "--database", "default"],
        ["/app/backend/manage.py", "collectstatic", "--noinput"],
        ["granian", "--interface", "asgi", "tfc.asgi:application"],
    ],
)
def test_management_command_guard_allows_only_startup_commands(argv):
    assert guarded_management_command(argv) is None


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["django-admin", "migrate"], "migrate"),
        (["/usr/local/bin/django-admin.py", "makemigrations"], "makemigrations"),
        (["python", "-m", "django", "migrate"], "migrate"),
        (["python3.11", "-m", "django", "ch25_apply_schema"], "ch25_apply_schema"),
    ],
)
def test_management_command_guard_covers_all_django_entrypoints(argv, command):
    assert guarded_management_command(argv) == command


def _warmup_sql(monkeypatch, *, drops_legacy_chain: bool) -> list[str]:
    monkeypatch.setattr(
        "tracer.services.clickhouse.schema.should_drop_legacy_chain",
        lambda: drops_legacy_chain,
    )
    client = Mock()
    existing = (
        {"traces"} if drops_legacy_chain else {"tracer_trace", "span_metrics_hourly"}
    )
    client.table_exists.side_effect = lambda table: table in existing
    ModelHubConfig._warm_ch_cache(client)
    return [call.args[0] for call in client.execute_read.call_args_list]


def test_ch25_cache_warm_reads_traces_instead_of_dropped_cdc_table(monkeypatch):
    queries = _warmup_sql(monkeypatch, drops_legacy_chain=True)

    assert any("FROM traces " in query for query in queries)
    assert not any("FROM tracer_trace " in query for query in queries)


def test_legacy_cache_warm_keeps_cdc_trace_table(monkeypatch):
    queries = _warmup_sql(monkeypatch, drops_legacy_chain=False)

    assert any("FROM tracer_trace " in query for query in queries)
    assert not any("FROM traces " in query for query in queries)
    assert any("FROM span_metrics_hourly " in query for query in queries)


def test_legacy_cache_warm_skips_dropped_metrics_table(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.schema.should_drop_legacy_chain",
        lambda: False,
    )
    client = Mock()
    client.table_exists.side_effect = lambda table: table == "tracer_trace"

    ModelHubConfig._warm_ch_cache(client)

    queries = [call.args[0] for call in client.execute_read.call_args_list]
    assert any("FROM tracer_trace " in query for query in queries)
    assert not any("FROM span_metrics_hourly " in query for query in queries)


def test_legacy_cache_warm_skips_metrics_query_when_table_probe_fails(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.schema.should_drop_legacy_chain",
        lambda: False,
    )
    client = Mock()

    def table_exists(table):
        if table == "tracer_trace":
            return True
        if table == "span_metrics_hourly":
            raise RuntimeError("schema probe unavailable")
        return False

    client.table_exists.side_effect = table_exists

    ModelHubConfig._warm_ch_cache(client)

    queries = [call.args[0] for call in client.execute_read.call_args_list]
    assert any("FROM tracer_trace " in query for query in queries)
    assert not any("FROM span_metrics_hourly " in query for query in queries)


def test_cache_warm_is_bounded_to_subsecond_reads(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.schema.should_drop_legacy_chain",
        lambda: True,
    )
    client = Mock()
    client.table_exists.side_effect = lambda table: table == "traces"

    ModelHubConfig._warm_ch_cache(client)

    assert client.execute_read.call_count > 0
    for call in client.execute_read.call_args_list:
        assert call.kwargs["timeout_ms"] == 750
        assert call.kwargs["settings"]["max_threads"] == 2
        assert call.kwargs["settings"]["max_memory_usage"] == 128 * 1024 * 1024
        assert call.kwargs["settings"]["timeout_overflow_mode"] == "break"


def test_cache_warm_skips_trace_query_when_neither_trace_table_exists(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.schema.should_drop_legacy_chain",
        lambda: False,
    )
    client = Mock()
    client.table_exists.return_value = False

    ModelHubConfig._warm_ch_cache(client)

    queries = [call.args[0] for call in client.execute_read.call_args_list]
    assert not any("FROM traces " in query for query in queries)
    assert not any("FROM tracer_trace " in query for query in queries)
