"""Pure topology/DDL/checkpoint contracts; live acceptance is a separate gate."""

import re
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.v2.cluster_apply import apply_plan, preflight
from tracer.services.clickhouse.v2.cluster_schema import (
    ClusterSchemaError,
    ClusterTopology,
    compile_definitions,
    native_plan,
)


@pytest.fixture
def topology():
    return ClusterTopology("test_cluster_v1", "test_cluster", "/tests/cluster")


@pytest.fixture
def plan(topology):
    return native_plan(topology)


def test_native_logical_names_and_engines(plan):
    names = {s.name: s for s in plan}
    for table in (
        "spans",
        "traces",
        "end_users",
        "trace_sessions",
        "spans_per_session",
        "spans_hourly_rollup",
        "span_user_rollup",
        "dashboard_attr_rollup",
        "eval_per_config",
        "tracer_eval_logger_v2",
    ):
        assert names[table].engine == "Distributed"
        assert names[table + "_local"].engine.startswith("Replicated")
    assert not any(
        re.search(r"\b(DROP|TRUNCATE|RENAME|POPULATE)\b", s.sql) for s in plan
    )
    assert all(
        "IF NOT EXISTS" in s.sql and "ON CLUSTER 'test_cluster'" in s.sql for s in plan
    )


def test_mv_fanout_is_local_only(plan):
    for step in plan:
        if step.engine == "MaterializedView":
            assert re.search(r"\bTO \w+\.\w+_local\b", step.sql)
            assert re.search(r"\bFROM (?:\w+\.)?\w+_local\b", step.sql)
            assert "Distributed" not in step.sql
    assert len([s for s in plan if s.engine == "MaterializedView"]) == 6


def test_paths_are_unique_by_database_shard_table(plan):
    paths = [
        re.search(r"Replicated\w+\('([^']+)'", s.sql)[1]
        for s in plan
        if s.engine.startswith("Replicated")
    ]
    assert len(paths) == len(set(paths))
    assert all(
        path.startswith("/tests/cluster/test_cluster_v1/{shard}/") for path in paths
    )


def test_trace_routing_does_not_depend_on_mutable_membership(plan):
    sql = next(s.sql for s in plan if s.name == "spans")
    assert "cityHash64(project_id, trace_id)" in sql
    assert "trace_session_id" not in sql and "end_user_id" not in sql
    remap = next(s.sql for s in plan if s.name == "end_user_id_remap")
    assert "cityHash64(old_id)" in remap


def test_dictionaries_resolve_global_aliases_before_span_ddl(plan):
    for name, table in (
        ("trace_dict", "traces"),
        ("end_users_dict", "end_users"),
        ("trace_sessions_dict", "trace_sessions"),
    ):
        step = next(s for s in plan if s.name == name)
        assert f"TABLE '{table}'" in step.sql
        assert "DB 'test_cluster_v1'" in step.sql
        assert "_local'" not in step.sql
    names = [s.name for s in plan]
    assert (
        names.index("traces") < names.index("trace_dict") < names.index("spans_local")
    )


@pytest.mark.parametrize(
    "change",
    [
        {"database": "default"},
        {"database": "bad;DROP"},
        {"cluster": "x';DROP"},
        {"keeper_prefix": "/x/{shard}"},
        {"replicas": 1},
        {"insert_quorum": 3},
        {"shards": 0},
    ],
)
def test_invalid_topology_rejected(topology, change):
    with pytest.raises(ValueError):
        replace(topology, **change)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE spans",
        "CREATE OR REPLACE TABLE spans (id UInt64) ENGINE=MergeTree ORDER BY id",
        "CREATE TABLE IF NOT EXISTS other.spans (id UInt64) ENGINE=MergeTree ORDER BY id",
        "ALTER TABLE spans DELETE WHERE 1",
    ],
)
def test_compiler_refuses_unsafe_definitions(topology, sql):
    with pytest.raises(ClusterSchemaError):
        compile_definitions([sql], topology, version="test")


def test_unsupported_engine_is_not_silently_local(topology):
    with pytest.raises(Exception, match="no recognised engine"):
        compile_definitions(
            ["CREATE TABLE IF NOT EXISTS data (id UInt64) ENGINE=Memory"],
            topology,
            version="test",
        )


def test_same_schema_has_deterministic_plan(topology, plan):
    assert [(s.key, s.checksum) for s in plan] == [
        (s.key, s.checksum) for s in native_plan(topology)
    ]


class FakeCluster:
    def __init__(self, topology, steps):
        self.topology, self.steps = topology, steps
        self.objects, self.receipts, self.commands = {}, [], []
        self.fail = None

    def query(self, sql, parameters=None, settings=None):
        if "system.tables" in sql:
            rows = [
                (f"node{n}", name, engine, ddl)
                for name, (engine, ddl) in self.objects.items()
                for n in range(6)
            ]
        elif "cluster_schema_versions" in sql:
            rows = self.receipts
        else:
            raise AssertionError(sql)
        return SimpleNamespace(result_rows=rows)

    def command(self, sql, settings=None):
        self.commands.append(sql)
        if "CREATE TABLE IF NOT EXISTS test_cluster_v1.cluster_schema_versions" in sql:
            self.objects["cluster_schema_versions"] = ("ReplicatedMergeTree", sql)
        for step in self.steps:
            if sql == step.sql:
                self.objects[step.name] = (step.engine, sql)
                if step.key == self.fail:
                    raise RuntimeError("DDL timed out after some nodes applied it")

    def insert(self, table, rows, column_names, settings):
        assert settings["async_insert"] == 0
        self.receipts.extend(tuple(row) for row in rows)


def test_resume_idempotent_after_partial_ddl(topology, plan):
    client = FakeCluster(topology, plan)
    client.fail = plan[2].key
    with patch("tracer.services.clickhouse.v2.cluster_apply.preflight"):
        with pytest.raises(RuntimeError):
            apply_plan(client, topology, plan)
        assert len(client.receipts) == 2
        client.fail = None
        assert apply_plan(client, topology, plan) == len(plan) - 2
        assert apply_plan(client, topology, plan) == 0
        assert len(client.receipts) == len(plan)


def test_drift_rejected_before_next_step(topology, plan):
    client = FakeCluster(topology, plan)
    with patch("tracer.services.clickhouse.v2.cluster_apply.preflight"):
        apply_plan(client, topology, plan)
        before = len(client.commands)
        with pytest.raises(ClusterSchemaError, match="immutable"):
            apply_plan(
                client, topology, [replace(plan[0], sql=plan[0].sql + " ")] + plan[1:]
            )
        assert all(
            "cluster_schema_versions" in sql or "CREATE DATABASE" in sql
            for sql in client.commands[before:]
        )


def test_existing_single_node_schema_not_adopted(topology, plan):
    client = FakeCluster(topology, plan)
    client.objects["spans"] = ("ReplacingMergeTree", "foreign")
    with patch("tracer.services.clickhouse.v2.cluster_apply.preflight"):
        with pytest.raises(ClusterSchemaError, match="unowned"):
            apply_plan(client, topology, plan)
    assert client.commands == []


def test_foreign_keeper_path_not_adopted(topology, plan):
    client = FakeCluster(topology, plan)
    client.objects["cluster_schema_versions"] = ("ReplicatedMergeTree", "ledger")
    client.objects[plan[0].name] = (
        plan[0].engine,
        plan[0].sql.replace("/tests/cluster/", "/foreign/"),
    )
    with patch("tracer.services.clickhouse.v2.cluster_apply.preflight"):
        with pytest.raises(ClusterSchemaError, match="foreign Keeper"):
            apply_plan(client, topology, plan)
    assert client.commands == []


def test_preflight_rejects_no_internal_replication(topology):
    client = SimpleNamespace(
        query=lambda *a, **k: SimpleNamespace(
            result_rows=[(s, r, False) for s in range(1, 4) for r in range(1, 3)]
        )
    )
    with pytest.raises(ClusterSchemaError, match="internal_replication"):
        preflight(client, topology)


def test_cdc_plan_keeps_real_eval_alias_and_versioned_updates(topology):
    from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import source_client
    from tracer.services.clickhouse.v2.cluster_cdc import cdc_plan

    steps = cdc_plan(
        source_client().inspect_source(), topology, include_usage_schema=True
    )
    named = {s.name: s for s in steps}
    assert named["tracer_eval_logger"].engine == "Distributed"
    assert "cityHash64(id)" in named["tracer_eval_logger"].sql
    assert (
        "_peerdb_version, _peerdb_is_deleted" in named["tracer_eval_logger_local"].sql
    )
    assert named["simulate_calls"].engine == "View"
    assert all("ON CLUSTER 'test_cluster'" in s.sql for s in steps)


def test_outbox_adds_columns_to_local_then_logical_cluster_tables(
    monkeypatch, topology
):
    from tracer.services.clickhouse import oss_outbox_cdc as cdc
    from tracer.services.clickhouse.v2 import cluster_cdc

    calls = []
    client = SimpleNamespace(command=lambda sql, **kwargs: calls.append(sql))
    monkeypatch.setenv("CH_CLUSTER_ENABLED", "true")
    monkeypatch.setattr(
        cluster_cdc.ClusterTopology, "from_env", lambda *a, **k: topology
    )
    monkeypatch.setattr(cdc, "_reset_snapshots", lambda *a: calls.append("snapshot"))
    source = SimpleNamespace(columns=[("id", "UUID"), ("new_field", "String")])
    assert cdc.add_source_columns(
        None, client, "tracer_eval_logger", source, ["id"]
    ) == ["tracer_eval_logger.new_field"]
    assert calls[0] == "snapshot"
    assert (
        "tracer_eval_logger_local ON CLUSTER 'test_cluster' ADD COLUMN IF NOT EXISTS `new_field` String"
        in calls[1]
    )
    assert (
        "tracer_eval_logger ON CLUSTER 'test_cluster' ADD COLUMN IF NOT EXISTS `new_field` String"
        in calls[2]
    )


def test_application_policy_overrides_shard_local_optimizations(monkeypatch):
    from tracer.services.clickhouse.application_read_policy import (
        application_read_settings,
    )

    monkeypatch.setenv("CH_CLUSTER_ENABLED", "true")
    settings = application_read_settings(
        {
            "distributed_group_by_no_merge": 1,
            "distributed_product_mode": "local",
            "skip_unavailable_shards": 1,
        }
    )
    assert settings["distributed_group_by_no_merge"] == 0
    assert settings["distributed_product_mode"] == "global"
    assert settings["skip_unavailable_shards"] == 0


def test_bootstrap_uses_cluster_command_not_legacy_installer(monkeypatch):
    from tfc.management.commands import bootstrap_install

    calls = []
    monkeypatch.setenv("CH_CLUSTER_ENABLED", "true")
    monkeypatch.setattr(
        bootstrap_install,
        "call",
        lambda name, log, **kwargs: calls.append((name, kwargs)),
    )
    bootstrap_install.clickhouse_native_schema(lambda _: None, 120)
    assert calls == [("ch25_cluster_schema", {"apply": True})]


def test_legacy_apply_refuses_cluster_database(monkeypatch):
    from django.core.management.base import CommandError

    from tracer.management.commands.ch25_apply_schema import Command

    monkeypatch.setenv("CH_CLUSTER_ENABLED", "true")
    with pytest.raises(CommandError, match="historical migrations"):
        Command().handle()


def test_additive_upgrade_updates_both_schemas(topology):
    from tracer.services.clickhouse.v2.cluster_schema import compile_addition

    steps = compile_addition(
        "ALTER TABLE spans ADD COLUMN IF NOT EXISTS new_field String DEFAULT ''",
        topology,
        version="002",
        known_tables={"spans"},
    )
    assert [s.name for s in steps] == ["spans_local", "spans"]
    assert all("ON CLUSTER 'test_cluster'" in s.sql for s in steps)
    index = compile_addition(
        "ALTER TABLE spans ADD INDEX IF NOT EXISTS test_index trace_id TYPE bloom_filter GRANULARITY 1",
        topology,
        version="003",
        known_tables={"spans"},
    )
    assert [s.name for s in index] == ["spans_local"]


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE spans MODIFY COLUMN id String",
        "ALTER TABLE spans DROP COLUMN id",
        "ALTER TABLE unknown ADD COLUMN IF NOT EXISTS id String",
        "ALTER TABLE spans ADD COLUMN IF NOT EXISTS extra String, DROP COLUMN id",
    ],
)
def test_upgrade_refuses_destructive_or_unknown_tables(topology, sql):
    from tracer.services.clickhouse.v2.cluster_schema import compile_addition

    with pytest.raises(ClusterSchemaError):
        compile_addition(sql, topology, version="bad", known_tables={"spans"})


def test_unknown_table_requires_reviewed_shard_key(topology):
    with pytest.raises(ClusterSchemaError, match="immutable shard key"):
        compile_definitions(
            [
                "CREATE TABLE IF NOT EXISTS new_data (id UUID) ENGINE=MergeTree ORDER BY id"
            ],
            topology,
            version="test",
        )


def test_nullable_dead_letter_project_is_not_routing_key(plan):
    step = next(s for s in plan if s.name == "spans_v2_dead_letter")
    assert "cityHash64(pg_id)" in step.sql
