"""Behavioural tests for ``ClickHouseVectorDB.create_table``.

The engine is chosen by introspecting ``system.clusters`` for any row with
``replica_num > 1``. Multi-replica cluster -> ReplicatedReplacingMergeTree
ON CLUSTER; single-node -> plain ReplacingMergeTree. Each test patches the
CH client and forces the introspection result.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_clustered_cache():
    """The clustered-detection result is cached on the class. Reset between
    tests so each test sees its own forced value.
    """
    from agentic_eval.core.database import ch_vector

    ch_vector.ClickHouseVectorDB._is_clustered_cached = None
    yield
    ch_vector.ClickHouseVectorDB._is_clustered_cached = None


@pytest.fixture
def captured_sql_client():
    """Patch ``ClickHouseVectorDB.__init__`` so no real CH connection is made
    and ``self.client`` is a MagicMock whose ``execute`` we can inspect.
    """
    from agentic_eval.core.database import ch_vector

    def _no_init(self, *_args, **_kwargs):
        self.client = MagicMock()

    with patch.object(ch_vector.ClickHouseVectorDB, "__init__", _no_init):
        instance = ch_vector.ClickHouseVectorDB()
        yield instance


def _last_ddl(captured_sql_client) -> str:
    """The last execute call that isn't the system.macros probe."""
    calls = captured_sql_client.client.execute.call_args_list
    # The DDL CREATE TABLE is the only call carrying settings=
    for call in reversed(calls):
        if "settings" in call.kwargs:
            return call.args[0]
    raise AssertionError("no CREATE TABLE call captured")


def _force_clustered(client_mock, clustered: bool) -> None:
    """Stub the system.clusters probe to return the desired clustered state."""
    client_mock.client.execute.return_value = [[1 if clustered else 0]]


def test_create_table_single_node_emits_plain_replacing_mergetree(captured_sql_client):
    _force_clustered(captured_sql_client, clustered=False)

    captured_sql_client.create_table("feedbacks")

    sql = _last_ddl(captured_sql_client)
    assert "ENGINE = ReplacingMergeTree()" in sql
    assert "ReplicatedReplacingMergeTree" not in sql
    assert "ON CLUSTER" not in sql


def test_create_table_clustered_emits_replicated_engine_and_on_cluster(captured_sql_client):
    _force_clustered(captured_sql_client, clustered=True)

    captured_sql_client.create_table("ground_truths")

    sql = _last_ddl(captured_sql_client)
    assert "ENGINE = ReplicatedReplacingMergeTree" in sql
    assert "ON CLUSTER 'cluster'" in sql
    assert (
        "ReplicatedReplacingMergeTree("
        "'/clickhouse/tables/{shard}/ground_truths', '{replica}'"
        ")"
    ) in sql


def test_create_table_table_name_substituted_into_zk_path(captured_sql_client):
    """Each table gets its own ZK path; otherwise two tables would coordinate
    on the same Keeper znode and corrupt each other's replication queue.
    Without an explicit database the path matches the historical convention
    (no database segment); with `database=` the path picks up the segment
    so two replicated tables with the same short name in different databases
    don't collide either.
    """
    _force_clustered(captured_sql_client, clustered=True)

    captured_sql_client.create_table("feedbacks")
    sql_fb = _last_ddl(captured_sql_client)
    assert "'/clickhouse/tables/{shard}/feedbacks'" in sql_fb

    captured_sql_client.client.execute.reset_mock()
    _force_clustered(captured_sql_client, clustered=True)
    captured_sql_client.create_table("ground_truths")
    sql_gt = _last_ddl(captured_sql_client)
    assert "'/clickhouse/tables/{shard}/ground_truths'" in sql_gt
    assert "feedbacks" not in sql_gt


def test_create_table_qualifies_table_and_zk_path_with_database(captured_sql_client):
    """`database=` qualifies the CREATE TABLE target AND inserts a database
    segment into the Keeper path. Mutating ``connection.database`` after the
    handshake does NOT reroute queries on the clickhouse-driver wire — this
    parameter is the only way to land a table in a non-default database from
    a connection that opened against `default`.
    """
    _force_clustered(captured_sql_client, clustered=True)

    captured_sql_client.create_table("feedbacks", database="futureagi")

    sql = _last_ddl(captured_sql_client)
    assert "CREATE TABLE IF NOT EXISTS futureagi.feedbacks" in sql
    assert "'/clickhouse/tables/{shard}/futureagi/feedbacks'" in sql
    # The bare-table variant must not still be present (regression guard).
    assert "CREATE TABLE IF NOT EXISTS feedbacks " not in sql
    assert "'/clickhouse/tables/{shard}/feedbacks'" not in sql


def test_create_table_database_default_unqualified(captured_sql_client):
    """When `database` is unset the SQL stays unqualified so the table lands
    in the connection's current database (CH_DATABASE). This is the runtime
    EmbeddingManager path and must not regress.
    """
    _force_clustered(captured_sql_client, clustered=False)

    captured_sql_client.create_table("feedbacks")

    sql = _last_ddl(captured_sql_client)
    assert "CREATE TABLE IF NOT EXISTS feedbacks " in sql
    assert "." not in sql.split("CREATE TABLE IF NOT EXISTS")[1].split("(")[0]


def test_create_table_clustered_keeps_required_columns(captured_sql_client):
    """Engine swap must not silently drop columns the rest of the codebase
    depends on (id, eval_id, vector, metadata, deleted).
    """
    _force_clustered(captured_sql_client, clustered=True)

    captured_sql_client.create_table("feedbacks")

    sql = _last_ddl(captured_sql_client)
    for column_signature in (
        "id UUID",
        "eval_id UUID",
        "vector Array(Float32)",
        "metadata Nested",
        "key String",
        "value Nullable(String)",
        "deleted UInt8 DEFAULT 0",
    ):
        assert column_signature in sql, f"missing column in CREATE TABLE: {column_signature}"


def test_create_table_order_by_preserved(captured_sql_client):
    """ReplacingMergeTree dedups by ORDER BY; if the key is wrong, replication
    semantics drift from the legacy table.
    """
    _force_clustered(captured_sql_client, clustered=True)

    captured_sql_client.create_table("feedbacks")

    sql = _last_ddl(captured_sql_client)
    assert "ORDER BY id" in sql


def test_is_clustered_cached_per_process(captured_sql_client):
    """The probe runs once; subsequent calls reuse the cached answer to avoid
    one ``system.clusters`` round trip per create_table.
    """
    captured_sql_client.client.execute.return_value = [[1]]

    first = captured_sql_client._is_clustered()
    second = captured_sql_client._is_clustered()

    assert first is True and second is True
    # Only one system.clusters probe across both calls.
    probes = [
        c for c in captured_sql_client.client.execute.call_args_list
        if "system.clusters" in c.args[0]
    ]
    assert len(probes) == 1


def test_is_clustered_single_node_with_replica_macro_returns_false(captured_sql_client):
    """Local single-node CH set up via Helm / docker-compose can have the
    ``replica`` macro pre-baked even though it has only one replica and no
    DDLWorker. The previous heuristic (probe ``system.macros``) treated such
    a node as clustered and would emit ``ON CLUSTER`` queries that fail at
    runtime. The replica-count check at ``system.clusters`` is the precise
    signal: only count replicas with ``replica_num > 1`` (i.e. a sibling
    replica exists), and a 1-replica cluster falls through to the plain
    MergeTree path.
    """
    captured_sql_client.client.execute.return_value = [[0]]

    assert captured_sql_client._is_clustered() is False
    probe_sql = captured_sql_client.client.execute.call_args_list[0].args[0]
    assert "system.clusters" in probe_sql
    assert "replica_num > 1" in probe_sql


def test_is_clustered_fails_safe_to_false(captured_sql_client):
    """If the introspection itself raises, default to non-clustered so we
    don't accidentally CREATE TABLE ON CLUSTER on a local single-node CH.
    """
    captured_sql_client.client.execute.side_effect = RuntimeError("boom")

    assert captured_sql_client._is_clustered() is False


def test_is_clustered_transient_probe_failure_does_not_poison_cache(
    captured_sql_client,
):
    """A first-call CH outage returns ``False`` but must NOT be cached.

    If a transient failure poisoned the process cache with ``False``, every
    subsequent table create in that worker would silently emit a
    non-replicated engine on what is actually a clustered CH, a large
    blast radius for a low-probability event. The next call after CH comes
    back must re-probe and return the true value.
    """
    from agentic_eval.core.database import ch_vector

    ch_vector.ClickHouseVectorDB._is_clustered_cached = None
    captured_sql_client.client.execute.side_effect = RuntimeError("boom")

    assert captured_sql_client._is_clustered() is False
    assert ch_vector.ClickHouseVectorDB._is_clustered_cached is None, (
        "transient probe failure must not be cached"
    )

    # CH recovers: the next probe returns a real answer and the cache picks it up.
    captured_sql_client.client.execute.side_effect = None
    captured_sql_client.client.execute.return_value = [[2]]
    assert captured_sql_client._is_clustered() is True
    assert ch_vector.ClickHouseVectorDB._is_clustered_cached is True


def test_create_table_threads_explicit_cluster_into_on_cluster(captured_sql_client):
    """The cluster name must come from the caller (migration --cluster) or
    `get_clickhouse_cluster_name()`, not be hardcoded; otherwise a deployment
    whose `remote_servers` config calls the cluster anything other than
    `'default'` would fail every CREATE TABLE call.
    """
    _force_clustered(captured_sql_client, clustered=True)

    captured_sql_client.create_table("feedbacks", cluster="fi_cluster")

    sql = _last_ddl(captured_sql_client)
    assert "ON CLUSTER 'fi_cluster'" in sql
    assert "ON CLUSTER 'default'" not in sql


def test_create_table_default_cluster_resolves_to_env_or_deployment_default(
    captured_sql_client, monkeypatch
):
    """When the caller doesn't pass `cluster`, the helper resolves to
    `$CH_CLUSTER_NAME` and falls back to `'cluster'` (the name pinned in every
    Future AGI ClickHouseInstallation manifest under `deployment/*`). This is
    the runtime path (EmbeddingManager.parallel_process_metadata) where the
    migration's `--cluster` flag isn't in play.
    """
    monkeypatch.setenv("CH_CLUSTER_NAME", "future_cluster")
    _force_clustered(captured_sql_client, clustered=True)

    captured_sql_client.create_table("feedbacks")

    sql = _last_ddl(captured_sql_client)
    assert "ON CLUSTER 'future_cluster'" in sql

    captured_sql_client.client.execute.reset_mock()
    _force_clustered(captured_sql_client, clustered=True)
    monkeypatch.delenv("CH_CLUSTER_NAME", raising=False)

    captured_sql_client.create_table("feedbacks")
    sql = _last_ddl(captured_sql_client)
    assert "ON CLUSTER 'cluster'" in sql
