"""Pure/mock coverage for CH25 schema topology safety."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.management.base import CommandError

from tracer.management.commands import ch25_apply_schema as apply_command
from tracer.management.commands import ch25_cutover as cutover_command
from tracer.services.clickhouse.v2 import apply_schema
from tracer.services.clickhouse.v2.schema_topology import (
    CATALOG_TOPOLOGY_GUARD_TABLES,
    SchemaTopology,
    SchemaTopologyError,
    is_hosted_production,
)


def _command_options(**overrides):
    options = {
        "status": False,
        "force": False,
        "files": None,
        "replicated": False,
        "cluster": None,
        "zk_table_path_prefix": None,
    }
    options.update(overrides)
    return options


@pytest.mark.parametrize(
    "options",
    [
        {"replicated": False, "cluster": "prod"},
        {"replicated": False, "zk_table_path_prefix": "/clickhouse/ch25"},
        {"replicated": True, "cluster": None, "zk_table_path_prefix": "/ch25"},
        {"replicated": True, "cluster": "prod", "zk_table_path_prefix": None},
        {
            "replicated": True,
            "cluster": "prod'; DROP TABLE spans; --",
            "zk_table_path_prefix": "/ch25",
        },
        {
            "replicated": True,
            "cluster": "prod",
            "zk_table_path_prefix": "/ch25/{shard}/",
        },
    ],
)
def test_topology_rejects_incomplete_or_unsafe_combinations(options):
    with pytest.raises(SchemaTopologyError):
        SchemaTopology.from_options(**options)


def test_topology_identity_separates_local_and_each_replicated_target():
    local = SchemaTopology.from_options(replicated=False)
    us = SchemaTopology.from_options(
        replicated=True,
        cluster="us_prod",
        zk_table_path_prefix="/clickhouse/tables/ch25",
    )
    eu = SchemaTopology.from_options(
        replicated=True,
        cluster="eu_prod",
        zk_table_path_prefix="/clickhouse/tables/ch25",
    )

    assert len({local.version_identity, us.version_identity, eu.version_identity}) == 3
    assert local.includes_legacy_local_versions is True
    assert us.includes_legacy_local_versions is False
    assert is_hosted_production("production", "US") is True
    assert is_hosted_production("production", "DEV") is False


def test_hosted_apply_command_refuses_before_loading_connection_config(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    with mock.patch.object(apply_command, "get_v2_config") as get_config:
        with pytest.raises(CommandError, match="hosted production"):
            apply_command.Command().handle(**_command_options(status=True))

    get_config.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [apply_command.Command(), cutover_command.Command()],
)
def test_management_parsers_expose_replicated_topology_flags(command):
    parser = command.create_parser("manage.py", "schema-command")

    options = vars(
        parser.parse_args(
            [
                "--replicated",
                "--cluster",
                "us_prod",
                "--zk-table-path-prefix",
                "/clickhouse/tables/ch25",
            ]
        )
    )

    assert options["replicated"] is True
    assert options["cluster"] == "us_prod"
    assert options["zk_table_path_prefix"] == "/clickhouse/tables/ch25"


def test_apply_command_forwards_explicit_replicated_topology(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "EU")
    monkeypatch.delenv("CH_PASSWORD", raising=False)
    config = {
        "host": "mock-clickhouse",
        "http_port": 8123,
        "user": "schema-user",
        "password": "mock-password",
        "database": "analytics",
    }
    options = _command_options(
        status=True,
        replicated=True,
        cluster="eu_prod",
        zk_table_path_prefix="/clickhouse/tables/ch25",
    )
    with (
        mock.patch.object(apply_command, "get_v2_config", return_value=config),
        mock.patch.object(apply_schema, "main", return_value=0) as apply_main,
    ):
        apply_command.Command().handle(**options)

    argv = apply_main.call_args.args[0]
    assert argv[-5:] == [
        "--replicated",
        "--cluster",
        "eu_prod",
        "--zk-table-path-prefix",
        "/clickhouse/tables/ch25",
    ]


def test_cutover_schema_phase_forwards_topology_options():
    options = _command_options(
        replicated=True,
        cluster="us_prod",
        zk_table_path_prefix="/clickhouse/tables/ch25",
    )
    with mock.patch.object(cutover_command, "call_command") as call_command:
        cutover_command.Command()._run_phase("schema", options)

    call_command.assert_called_once_with(
        "ch25_apply_schema",
        replicated=True,
        cluster="us_prod",
        zk_table_path_prefix="/clickhouse/tables/ch25",
    )


def test_cutover_rejects_topology_options_when_schema_phase_is_absent():
    options = _command_options(
        phase="validate",
        yes=True,
        backfill="no",
        sample_size=10,
        report=None,
        replicated=True,
        cluster="us_prod",
        zk_table_path_prefix="/clickhouse/tables/ch25",
    )
    with pytest.raises(CommandError, match="require the schema phase"):
        cutover_command.Command().handle(**options)


def test_low_level_hosted_apply_refuses_before_connect(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    with mock.patch.object(apply_schema.clickhouse_connect, "get_client") as connect:
        assert apply_schema.main(["--status"]) == 1

    connect.assert_not_called()


def test_low_level_rejects_topology_flags_without_replicated_before_connect(
    monkeypatch,
):
    monkeypatch.setenv("ENV_TYPE", "test")
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    with mock.patch.object(apply_schema.clickhouse_connect, "get_client") as connect:
        assert apply_schema.main(["--status", "--cluster", "us_prod"]) == 1

    connect.assert_not_called()


def test_local_mergetree_conflict_scan_is_cluster_wide_and_allowlisted():
    rows = [
        ("replica-2", "span_attribute_value_catalog", "AggregatingMergeTree"),
        ("replica-1", "schema_versions", "MergeTree"),
        (
            "replica-3",
            "span_attribute_key_catalog",
            "ReplicatedAggregatingMergeTree",
        ),
        ("replica-1", "unrelated_table", "MergeTree"),
    ]
    client = mock.Mock()
    client.query.return_value = SimpleNamespace(result_rows=rows)

    conflicts = apply_schema.find_local_merge_tree_conflicts(
        client,
        database="analytics",
        cluster="us_prod",
    )

    assert [(item.host, item.table, item.engine) for item in conflicts] == [
        ("replica-1", "schema_versions", "MergeTree"),
        ("replica-2", "span_attribute_value_catalog", "AggregatingMergeTree"),
    ]
    sql = client.query.call_args.args[0]
    assert "clusterAllReplicas(%(cluster)s, system.tables)" in sql
    assert client.query.call_args.kwargs["parameters"] == {
        "cluster": "us_prod",
        "database": "analytics",
    }
    for table in CATALOG_TOPOLOGY_GUARD_TABLES:
        assert f"'{table}'" in sql


def test_replicated_main_refuses_local_conflict_before_first_ddl(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "test")
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    client = mock.Mock()
    client.query.return_value = SimpleNamespace(
        result_rows=[("replica-1", "schema_versions", "MergeTree")]
    )
    with (
        mock.patch.object(
            apply_schema.clickhouse_connect,
            "get_client",
            return_value=client,
        ),
        mock.patch.object(apply_schema, "ensure_versions_table") as ensure_versions,
    ):
        rc = apply_schema.main(
            [
                "--status",
                "--replicated",
                "--cluster",
                "us_prod",
                "--zk-table-path-prefix",
                "/clickhouse/tables/ch25",
            ]
        )

    assert rc == 3
    ensure_versions.assert_not_called()
    client.command.assert_not_called()
    client.insert.assert_not_called()


def test_replicated_main_fails_closed_when_preflight_cannot_complete(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "test")
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    client = mock.Mock()
    client.query.side_effect = RuntimeError("mock topology lookup failed")
    with (
        mock.patch.object(
            apply_schema.clickhouse_connect,
            "get_client",
            return_value=client,
        ),
        mock.patch.object(apply_schema, "ensure_versions_table") as ensure_versions,
    ):
        rc = apply_schema.main(
            [
                "--status",
                "--replicated",
                "--cluster",
                "us_prod",
                "--zk-table-path-prefix",
                "/clickhouse/tables/ch25",
            ]
        )

    assert rc == 3
    ensure_versions.assert_not_called()
    client.command.assert_not_called()
    client.insert.assert_not_called()


@pytest.mark.parametrize(
    ("topology", "include_legacy"),
    [
        (SchemaTopology.from_options(replicated=False), 1),
        (
            SchemaTopology.from_options(
                replicated=True,
                cluster="us_prod",
                zk_table_path_prefix="/clickhouse/tables/ch25",
            ),
            0,
        ),
    ],
)
def test_fetch_applied_is_scoped_to_exact_topology(topology, include_legacy):
    client = mock.Mock()
    client.query.return_value = SimpleNamespace(
        result_rows=[("002_spans_v2.sql", b"a" * 64)]
    )

    assert apply_schema.fetch_applied(client, topology=topology) == {
        "002_spans_v2.sql": "a" * 64
    }
    kwargs = client.query.call_args.kwargs
    assert kwargs["parameters"] == {
        "topology_identity": topology.version_identity,
        "include_legacy_local": include_legacy,
    }
    assert "notes = %(topology_identity)s" in client.query.call_args.args[0]


def test_apply_file_records_the_same_identity_used_for_rewrite(tmp_path):
    schema_path = tmp_path / "999_test.sql"
    schema_path.write_text(
        "CREATE TABLE IF NOT EXISTS topology_test (value UInt8) ENGINE = MergeTree "
        "ORDER BY value;\n"
    )
    topology = SchemaTopology.from_options(
        replicated=True,
        cluster="us_prod",
        zk_table_path_prefix="/clickhouse/tables/ch25",
    )
    client = mock.Mock()

    applied = apply_schema.apply_file(
        client,
        apply_schema.SchemaFile.from_path(schema_path),
        "test-user",
        replicated=True,
        cluster="us_prod",
        zk_prefix="/clickhouse/tables/ch25",
        topology=topology,
    )

    assert applied == 1
    assert "ReplicatedMergeTree" in client.command.call_args.args[0]
    assert "ON CLUSTER 'us_prod'" in client.command.call_args.args[0]
    assert client.insert.call_args.args[1][0][3] == topology.version_identity


def test_status_passes_and_reports_local_topology(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "test")
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    client = mock.Mock()
    with (
        mock.patch.object(
            apply_schema.clickhouse_connect,
            "get_client",
            return_value=client,
        ),
        mock.patch.object(apply_schema, "ensure_versions_table"),
        mock.patch.object(apply_schema, "fetch_applied", return_value={}) as fetch,
        mock.patch.object(apply_schema, "log") as log,
    ):
        assert apply_schema.main(["--status"]) == 0

    topology = fetch.call_args.kwargs["topology"]
    assert topology == SchemaTopology.from_options(replicated=False)
    log.info.assert_any_call(
        "status_topology",
        topology=topology.version_identity,
    )
