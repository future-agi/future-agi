# ruff: noqa: F811 -- imported pytest fixture
"""Ordinary views must follow the same topology as their backing tables."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2 import apply_schema
from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    extract_table_name,
    rewrite_for_replicated,
    split_statements,
)
from tracer.tests.test_derived_metric_clickhouse import SCHEMA, metric_ch  # noqa: F401


@pytest.mark.parametrize(
    "prefix", ["CREATE VIEW", "CREATE VIEW IF NOT EXISTS", "CREATE OR REPLACE VIEW"]
)
@pytest.mark.parametrize(
    "name",
    [
        "derived_metrics",
        "`analytics`.`derived_metrics`",
        '"analytics"."derived_metrics"',
    ],
)
def test_ordinary_view_uses_the_configured_cluster(prefix, name):
    # SQL literals in the SELECT must not suppress attachment or be rewritten as
    # engines. The object is a view, so it needs no MergeTree engine of its own.
    sql = (
        f"{prefix} {name} AS SELECT 'ON CLUSTER', 'CREATE TABLE', 'ENGINE = MergeTree'"
    )
    assert extract_table_name(sql) == "derived_metrics"
    rewritten = rewrite_for_replicated(
        sql,
        table_name="derived_metrics",
        cluster="metrics_cluster",
        zk_prefix="/ch/tables",
    )
    expected = f"{prefix} {name} ON CLUSTER 'metrics_cluster' AS SELECT 'ON CLUSTER', 'CREATE TABLE', 'ENGINE = MergeTree'"
    assert rewritten == expected
    assert (
        rewrite_for_replicated(
            rewritten,
            table_name="derived_metrics",
            cluster="metrics_cluster",
            zk_prefix="/ch/tables",
        )
        == rewritten
    )


@pytest.mark.parametrize("replicated", [False, True])
@pytest.mark.parametrize(
    "filename",
    ["029_derived_metric_uncertainty.sql", "030_derived_metric_view_refresh.sql"],
)
def test_uncertainty_upgrade_through_the_actual_apply_runner(replicated, filename):
    client = Mock()
    schema = apply_schema.SchemaFile.from_path(SCHEMA / filename)
    statements = split_statements(schema.path.read_text())
    assert apply_schema.apply_file(
        client, schema, "test", replicated=replicated, cluster="metrics_cluster"
    ) == len(statements)
    emitted = [call.args[0] for call in client.command.call_args_list]
    assert len(emitted) == len(statements)
    assert emitted[-1].startswith("CREATE OR REPLACE VIEW derived_metrics")
    for original, rewritten in zip(statements, emitted, strict=True):
        if replicated:
            assert "ON CLUSTER 'metrics_cluster'" in rewritten
            assert rewritten.replace(" ON CLUSTER 'metrics_cluster'", "") == original
        else:
            assert rewritten == original
    client.insert.assert_called_once()
    assert client.insert.call_args.args[1][0][:2] == [schema.path.name, schema.sha256]


def test_failed_view_refresh_never_records_a_successful_receipt():
    client = Mock()

    def fail_view(sql):
        if sql.startswith("CREATE OR REPLACE VIEW"):
            raise RuntimeError("view refresh failed")

    client.command.side_effect = fail_view
    with pytest.raises(RuntimeError, match="view refresh failed"):
        apply_schema.apply_file(
            client,
            apply_schema.SchemaFile.from_path(
                SCHEMA / "029_derived_metric_uncertainty.sql"
            ),
            "test",
            replicated=True,
            cluster="metrics_cluster",
        )
    client.insert.assert_not_called()


class NativeAdapter:
    """Adapt clickhouse-driver to the runner's clickhouse-connect API, no DB mocks."""

    def __init__(self, client):
        self.client = client

    def command(self, sql):
        return self.client.execute(sql)

    def insert(self, table, rows, column_names):
        return self.client.execute(
            f"INSERT INTO {table} ({', '.join(column_names)}) VALUES", rows
        )

    def query(self, sql, parameters):
        return SimpleNamespace(result_rows=self.client.execute(sql, parameters))


def test_local_upgrade_runner_refreshes_projection_and_records_receipt(metric_ch):
    runner_client = NativeAdapter(metric_ch)
    metric_ch.execute("DROP VIEW derived_metrics")
    metric_ch.execute(
        "CREATE VIEW derived_metrics AS SELECT value FROM derived_metric_results"
    )
    apply_schema.ensure_versions_table(runner_client)
    schema = apply_schema.SchemaFile.from_path(
        SCHEMA / "029_derived_metric_uncertainty.sql"
    )
    for _ in range(2):
        apply_schema.apply_file(runner_client, schema, "test")
        assert apply_schema.fetch_applied(runner_client) == {
            schema.path.name: schema.sha256
        }
        names = {row[0] for row in metric_ch.execute("DESCRIBE TABLE derived_metrics")}
        assert {"std_error", "ci_low", "ci_high", "effective_n", "ci_status"} <= names
        assert metric_ch.execute("SELECT count() FROM derived_metrics") == [(0,)]
    # A database with the old successful 029 receipt but a stale replica view
    # still receives the independent repair receipt. Keep both source hashes.
    metric_ch.execute(
        "CREATE OR REPLACE VIEW derived_metrics AS SELECT value FROM derived_metric_results"
    )
    repair = apply_schema.SchemaFile.from_path(
        SCHEMA / "030_derived_metric_view_refresh.sql"
    )
    for _ in range(2):
        apply_schema.apply_file(runner_client, repair, "test")
        assert apply_schema.fetch_applied(runner_client) == {
            schema.path.name: schema.sha256,
            repair.path.name: repair.sha256,
        }
        names = {row[0] for row in metric_ch.execute("DESCRIBE TABLE derived_metrics")}
        assert {"std_error", "ci_low", "ci_high", "effective_n", "ci_status"} <= names
        assert metric_ch.execute("SELECT count() FROM derived_metrics") == [(0,)]
