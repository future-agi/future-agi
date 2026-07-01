"""Engine-selection tests for the legacy ``default.*`` ClickHouse tables.

These five tables (``cluster_centroids``, ``trace_input_embeddings``,
``error_embeddings``, ``llm_logs``, ``events``) are created by ad-hoc
``ensure_*`` helpers rather than the managed v2 schema. Each helper must emit a
``Replicated*`` engine ``ON CLUSTER`` on a multi-replica cluster and the plain
engine on single-node CH — and must preserve its own sub-family: the centroid /
trace-input tables stay ``Replacing`` (with their version column), while the
append-only / soft-delete tables stay plain ``MergeTree``.

No real CH: the client is mocked and the cluster-introspection result forced.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentic_eval.core.database.ch_vector import build_replicated_engine
from model_hub.services.legacy_ch_tables import (
    ensure_events_table,
    ensure_llm_logs_table,
)
from tracer.services.clickhouse.clustering_tables import (
    ensure_centroid_table,
    ensure_error_embeddings_table,
    ensure_trace_inputs_table,
)


@pytest.fixture(autouse=True)
def _reset_clustered_cache():
    """`is_clustered` caches on the class; reset so each test forces its own."""
    from agentic_eval.core.database import ch_vector

    ch_vector.ClickHouseVectorDB._is_clustered_cached = None
    yield
    ch_vector.ClickHouseVectorDB._is_clustered_cached = None


# (ensure_fn, takes_vectordb, table, replicated_engine, plain_engine, version_col)
_CASES = [
    (
        ensure_centroid_table,
        True,
        "cluster_centroids",
        "ReplicatedReplacingMergeTree",
        "ReplacingMergeTree(last_updated)",
        "last_updated",
    ),
    (
        ensure_trace_inputs_table,
        True,
        "trace_input_embeddings",
        "ReplicatedReplacingMergeTree",
        "ReplacingMergeTree(created_at)",
        "created_at",
    ),
    (
        ensure_error_embeddings_table,
        True,
        "error_embeddings",
        "ReplicatedMergeTree",
        "MergeTree()",
        None,
    ),
    (
        ensure_events_table,
        False,
        "events",
        "ReplicatedMergeTree",
        "MergeTree()",
        None,
    ),
    (
        ensure_llm_logs_table,
        False,
        "llm_logs",
        "ReplicatedMergeTree",
        "MergeTree",
        None,
    ),
]

_IDS = [c[2] for c in _CASES]


def _run_ensure(ensure_fn, takes_vectordb, *, clustered, database=None) -> str:
    """Invoke an ensure helper with a mocked client and return the CREATE SQL."""
    client = MagicMock()
    # The legacy helpers probe system.clusters through this same client.
    client.execute.return_value = [[1 if clustered else 0]]

    if takes_vectordb:
        db = MagicMock()
        db._is_clustered.return_value = clustered
        db.client = client
        ensure_fn(db, database=database)
    else:
        ensure_fn(client, database=database)

    for call in reversed(client.execute.call_args_list):
        if "CREATE TABLE" in call.args[0]:
            return call.args[0]
    raise AssertionError("no CREATE TABLE call captured")


@pytest.mark.parametrize(
    "ensure_fn,takes_vectordb,table,replicated_engine,plain_engine,version_col",
    _CASES,
    ids=_IDS,
)
def test_clustered_emits_replicated_engine_on_cluster(
    ensure_fn, takes_vectordb, table, replicated_engine, plain_engine, version_col
):
    sql = _run_ensure(ensure_fn, takes_vectordb, clustered=True)

    assert f"ENGINE = {replicated_engine}(" in sql
    assert "ON CLUSTER 'cluster'" in sql
    assert f"'/clickhouse/tables/{{shard}}/{table}'" in sql
    if version_col:
        # Replacing sub-family keeps its version column as the trailing arg.
        assert f"'{{replica}}', {version_col})" in sql


@pytest.mark.parametrize(
    "ensure_fn,takes_vectordb,table,replicated_engine,plain_engine,version_col",
    _CASES,
    ids=_IDS,
)
def test_single_node_emits_plain_engine_no_on_cluster(
    ensure_fn, takes_vectordb, table, replicated_engine, plain_engine, version_col
):
    sql = _run_ensure(ensure_fn, takes_vectordb, clustered=False)

    assert f"ENGINE = {plain_engine}" in sql
    assert "ON CLUSTER" not in sql
    assert "Replicated" not in sql


@pytest.mark.parametrize(
    "ensure_fn,takes_vectordb,table,replicated_engine,plain_engine,version_col",
    _CASES,
    ids=_IDS,
)
def test_database_qualifies_target_and_zk_path(
    ensure_fn, takes_vectordb, table, replicated_engine, plain_engine, version_col
):
    sql = _run_ensure(ensure_fn, takes_vectordb, clustered=True, database="futureagi")

    assert f"CREATE TABLE IF NOT EXISTS futureagi.{table}" in sql
    assert f"'/clickhouse/tables/{{shard}}/futureagi/{table}'" in sql


def test_append_only_and_softdelete_tables_never_become_replacing():
    """Regression guard: the ticket title says "ReplicatedReplacingMergeTree",
    but forcing Replacing onto llm_logs/events (no unique ORDER BY key) or
    error_embeddings (soft-delete, no version col) would silently drop rows.
    """
    for ensure_fn, takes_vectordb, table in (
        (ensure_error_embeddings_table, True, "error_embeddings"),
        (ensure_events_table, False, "events"),
        (ensure_llm_logs_table, False, "llm_logs"),
    ):
        sql = _run_ensure(ensure_fn, takes_vectordb, clustered=True)
        assert "ReplacingMergeTree" not in sql, table
        assert "ENGINE = ReplicatedMergeTree(" in sql, table


def test_build_replicated_engine_rejects_unknown_engine():
    with pytest.raises(ValueError):
        build_replicated_engine("TinyLog", "whatever", clustered=True)


def test_build_replicated_engine_passthrough_when_single_node():
    engine, on_cluster = build_replicated_engine(
        "ReplacingMergeTree(ts)", "t", clustered=False, database="futureagi"
    )
    assert engine == "ReplacingMergeTree(ts)"
    assert on_cluster == ""


def test_centroid_ddl_is_shared_across_all_three_clustering_modules():
    """cluster_centroids had three identical DDL copies; they must now resolve
    to the one shared helper so a schema edit can't drift between them.
    """
    from tracer.queries import error_clustering, eval_clustering, scan_clustering
    from tracer.services.clickhouse import clustering_tables

    assert (
        scan_clustering.ensure_centroid_table
        is eval_clustering.ensure_centroid_table
        is clustering_tables.ensure_centroid_table
    )
    # error_clustering keeps a thin method wrapper, but it delegates to the
    # same module function rather than carrying its own DDL string.
    assert "clustering_tables" in error_clustering.ErrorClusteringDB.ensure_centroid_table.__code__.co_names
