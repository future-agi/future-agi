"""Behavioural tests for ``migrate_legacy_default_tables_to_replicated``.

ClickHouseVectorDB is mocked; we assert on the SQL the command sends (name-
aligned column list, per-table dedup clause, one-shot guard, CREATE DATABASE
ordering, replica-aware parity) and on the conditions that must raise
CommandError. No real CH — cross-replica convergence is covered by the live
2-replica harness; here we pin the SQL and the safety branches.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

_CMD = "migrate_legacy_default_tables_to_replicated"
_DOTTED = (
    "model_hub.management.commands."
    "migrate_legacy_default_tables_to_replicated.ClickHouseVectorDB"
)


@pytest.fixture(autouse=True)
def _reset_clustered_cache():
    from agentic_eval.core.database import ch_vector

    ch_vector.ClickHouseVectorDB._is_clustered_cached = None
    yield
    ch_vector.ClickHouseVectorDB._is_clustered_cached = None


def _make_db_client(
    *,
    source_count: int = 10,
    target: dict | None = None,
    table_exists: bool = True,
    clustered: bool = True,
    replicas: tuple = ("ch-0", "ch-1", "ch-2"),
    columns: tuple = ("id", "eval_id", "vector"),
    insert_reaches: int | None = None,
):
    """ClickHouseVectorDB stand-in with scripted query returns.

    ``target`` is an optional per-host row-count dict (defaults to all-zero).
    ``insert_reaches`` overrides the per-host count an INSERT drives the target
    to (defaults to source_count, i.e. it converges); set it below source_count
    to simulate a replica that never catches up.
    """
    state = {h: ((target or {}).get(h, 0)) for h in replicas}
    reach = source_count if insert_reaches is None else insert_reaches
    db = MagicMock()
    executed: list[str] = []

    def ex(sql, params=None, settings=None):
        executed.append(sql)
        lc = sql.lower()
        if "from system.tables" in lc:
            return [(1 if table_exists else 0,)]
        if "from system.columns" in lc:
            return [(c,) for c in columns]
        if "from system.clusters" in lc:  # _expected_replica_count
            return [(len(replicas),)]
        if "hostname()" in lc and "clusterallreplicas" in lc:
            return [(h, c) for h, c in state.items()]
        if lc.lstrip().startswith("insert into"):
            for h in state:
                state[h] = max(state[h], reach)
            return []
        if "clusterallreplicas" in lc:  # source count (uniqExact / count())
            return [(source_count,)]
        return []

    db.client.execute.side_effect = ex
    db._is_clustered = MagicMock(return_value=clustered)
    return db, executed, state


def _run(*tables, db_client=None, dry_run=False, target="futureagi", source="default"):
    out, err = StringIO(), StringIO()
    args = [
        _CMD,
        f"--source-database={source}",
        f"--target-database={target}",
        f"--tables={','.join(tables)}",
    ]
    if dry_run:
        args.append("--dry-run")
    with patch(_DOTTED, return_value=db_client):
        call_command(*args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _inserts(executed):
    return [s for s in executed if s.lower().lstrip().startswith("insert into")]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_refuses_when_source_equals_target():
    with pytest.raises(CommandError, match="must differ"):
        call_command(_CMD, "--source-database=default", "--target-database=default",
                     "--tables=llm_logs", stdout=StringIO())


def test_refuses_invalid_identifier():
    with pytest.raises(CommandError, match="valid ClickHouse identifier"):
        call_command(_CMD, "--source-database=default", "--target-database=bad-name",
                     "--tables=llm_logs", stdout=StringIO())


def test_refuses_unknown_table():
    with pytest.raises(CommandError, match="unknown entries"):
        call_command(_CMD, "--source-database=default", "--target-database=futureagi",
                     "--tables=not_a_table", stdout=StringIO())


def test_runs_on_single_node_ch():
    """Single-node CH is a valid target: the engine choice per env is
    owned by the per-table ``ensure_*`` helpers, which fall back to plain
    engines when the cluster has one replica. The CREATE DATABASE
    bootstrap also drops ``ON CLUSTER`` on the same signal so the
    Keeper-less local CH accepts the DDL.
    """
    db_client, executed, _ = _make_db_client(
        clustered=False, replicas=("ch-0",), source_count=5
    )
    out, _ = _run("cluster_centroids", db_client=db_client)
    assert "cluster_centroids:" in out
    assert any(s.lower().lstrip().startswith("insert into") for s in executed)
    create_db_sql = [s for s in executed if "create database" in s.lower()]
    assert create_db_sql, "CREATE DATABASE should still run on single-node"
    assert all("on cluster" not in s.lower() for s in create_db_sql)


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def test_dry_run_issues_no_writes():
    db_client, executed, _ = _make_db_client()
    out, _ = _run("cluster_centroids", db_client=db_client, dry_run=True)
    joined = " ".join(executed).lower()
    assert "create database" not in joined
    assert "insert into" not in joined
    assert "create table" not in joined
    assert "would copy" in out


# ---------------------------------------------------------------------------
# Real run — keyed tables
# ---------------------------------------------------------------------------

def test_creates_database_on_cluster_before_table_work():
    db_client, executed, _ = _make_db_client()
    _run("cluster_centroids", db_client=db_client)
    create_db = next(i for i, s in enumerate(executed) if "create database" in s.lower())
    first_insert = next(i for i, s in enumerate(executed)
                        if s.lower().lstrip().startswith("insert into"))
    assert "ON CLUSTER 'cluster'" in executed[create_db]
    assert create_db < first_insert


@pytest.mark.parametrize("table,expected_where", [
    ("trace_input_embeddings",
     "WHERE (project_id, trace_id) NOT IN "
     "(SELECT project_id, trace_id FROM futureagi.trace_input_embeddings)"),
    ("error_embeddings",
     "WHERE (id) NOT IN (SELECT id FROM futureagi.error_embeddings)"),
    ("cluster_centroids",
     "WHERE (cluster_id) NOT IN (SELECT cluster_id FROM futureagi.cluster_centroids)"),
])
def test_keyed_table_backfill_uses_its_dedup_clause(table, expected_where):
    db_client, executed, _ = _make_db_client()
    _run(table, db_client=db_client)
    ins = _inserts(executed)
    assert len(ins) == 1
    assert f"INSERT INTO futureagi.{table}" in ins[0]
    assert "clusterAllReplicas('cluster', default." in ins[0]
    assert expected_where in ins[0]


def test_backfill_uses_explicit_column_list_not_select_star():
    # Regression: positional SELECT * silently misaligns a drifted source.
    db_client, executed, _ = _make_db_client(columns=("id", "eval_id", "vector"))
    _run("error_embeddings", db_client=db_client)
    ins = _inserts(executed)[0]
    assert "SELECT *" not in ins
    assert "INSERT INTO futureagi.error_embeddings (`id`, `eval_id`, `vector`)" in ins
    assert "SELECT `id`, `eval_id`, `vector` FROM" in ins


def test_keyed_table_idempotent_rerun_copies_zero():
    db_client, _, _ = _make_db_client(
        source_count=10, target={"ch-0": 10, "ch-1": 10, "ch-2": 10})
    out, _ = _run("error_embeddings", db_client=db_client)
    assert "copied 0 rows" in out


# ---------------------------------------------------------------------------
# Real run — append-only log tables (one-shot, no key)
# ---------------------------------------------------------------------------

def test_one_shot_inserts_without_where_when_empty_everywhere():
    db_client, executed, _ = _make_db_client(source_count=95)  # target all 0
    _run("llm_logs", db_client=db_client)
    ins = _inserts(executed)
    assert len(ins) == 1
    assert "INSERT INTO futureagi.llm_logs" in ins[0]
    assert "NOT IN" not in ins[0] and "WHERE" not in ins[0]


def test_one_shot_no_op_when_already_converged():
    # Re-run after a successful one-shot: target == source on every replica.
    db_client, executed, _ = _make_db_client(
        source_count=95, target={"ch-0": 95, "ch-1": 95, "ch-2": 95})
    out, _ = _run("llm_logs", db_client=db_client)
    assert not _inserts(executed)
    assert "one-shot no-op" in out


def test_one_shot_REFUSES_when_a_single_replica_is_empty():
    # THE critical bug: min() across replicas let one empty replica wave the
    # copy through and duplicate the keyless log. Must now refuse + fail.
    db_client, executed, _ = _make_db_client(
        source_count=95, target={"ch-0": 95, "ch-1": 95, "ch-2": 0})
    with pytest.raises(CommandError, match="did not fully converge"):
        _run("llm_logs", db_client=db_client)
    assert not _inserts(executed)  # never inserted -> no duplication


def test_one_shot_refuses_on_partial_target():
    db_client, executed, _ = _make_db_client(
        source_count=95, target={"ch-0": 42, "ch-1": 42, "ch-2": 42})
    with pytest.raises(CommandError, match="did not fully converge"):
        _run("llm_logs", db_client=db_client)
    assert not _inserts(executed)


def test_source_missing_creates_target_and_skips_copy():
    db_client, executed, _ = _make_db_client(table_exists=False)
    out, _ = _run("events", db_client=db_client)
    assert not _inserts(executed)
    assert "nothing to copy" in out


# ---------------------------------------------------------------------------
# Replica-aware parity + failure propagation
# ---------------------------------------------------------------------------

def test_parity_failure_raises_nonzero(monkeypatch):
    # A replica that never reaches source_count must fail the whole command so
    # an exit-code-gated CH_DATABASE flip cannot proceed.
    import model_hub.management.commands.migrate_legacy_default_tables_to_replicated as m
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(m.time, "monotonic", lambda: clock.__setitem__("t", clock["t"] + 100) or clock["t"])
    db_client, _, _ = _make_db_client(source_count=100, insert_reaches=1)  # never converges
    with pytest.raises(CommandError, match="did not fully converge"):
        _run("cluster_centroids", db_client=db_client)


def test_multi_table_run_processes_each():
    db_client, executed, _ = _make_db_client()
    _run("trace_input_embeddings", "error_embeddings", "cluster_centroids",
         db_client=db_client)
    for table in ("trace_input_embeddings", "error_embeddings", "cluster_centroids"):
        assert any(f"INSERT INTO futureagi.{table}" in s for s in executed)
