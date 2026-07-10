"""Behavioural tests for ``migrate_ch_vector_tables``.

ClickHouseVectorDB is mocked so no real CH is required. We assert on the
SQL the command actually sends, the stdout it writes, and the conditions
that should raise CommandError.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


# Canonical vector-table columns for tests. The exact set only matters for
# assertions that check the explicit column list appears in the INSERT.
_VECTOR_COLUMNS = [
    "id",
    "eval_id",
    "vector",
    "metadata.Key",
    "metadata.Value",
    "deleted",
]


def _build_mock_db_client(
    *,
    source_distinct: int = 14,
    target_count_before: int = 0,
    table_exists: bool = True,
    clustered: bool = True,
    expected_replicas: int = 3,
    replicas_present: int | None = None,
    columns: list[str] | None = None,
):
    """A ClickHouseVectorDB stand-in with scripted query returns.

    ``expected_replicas`` sets the ``system.clusters`` count.
    ``replicas_present`` (default: same as ``expected_replicas``) sets how
    many replicas appear in ``per_replica_counts``: set it below
    ``expected_replicas`` to simulate a follower that hasn't registered.
    ``columns`` overrides the shared-columns view of ``system.columns``.
    """
    db_client = MagicMock()
    target_count_state = {"value": target_count_before}
    cols = columns if columns is not None else _VECTOR_COLUMNS
    present = expected_replicas if replicas_present is None else replicas_present

    def execute_side_effect(sql, params=None):
        sql_lc = sql.lower()
        # system.clusters -> expected replica count
        if "from system.clusters" in sql_lc:
            return [(expected_replicas,)]
        # system.columns -> shared_columns
        if "from system.columns" in sql_lc:
            return [(c,) for c in cols]
        # per_replica_counts reads system.tables.total_rows via clusterAllReplicas
        if (
            "from clusterallreplicas" in sql_lc
            and "system.tables" in sql_lc
            and "total_rows" in sql_lc
        ):
            return [
                (f"ch-replica-{i}", target_count_state["value"])
                for i in range(1, present + 1)
            ]
        # source_exists probe against system.tables (no clusterAllReplicas)
        if "from system.tables" in sql_lc:
            return [(1 if table_exists else 0,)]
        if "uniqexact(id)" in sql_lc and "clusterallreplicas" in sql_lc:
            return [(source_distinct,)]
        # Dry-run anti-join preview: count() FROM clusterAllReplicas(...) WHERE id NOT IN
        if (
            "clusterallreplicas" in sql_lc
            and "not in" in sql_lc
            and "count()" in sql_lc
        ):
            return [(max(0, source_distinct - target_count_state["value"]),)]
        # Dry-run target row count: plain count() FROM {target_qualified}
        if sql_lc.lstrip().startswith("select count()") and "clusterallreplicas" not in sql_lc:
            return [(target_count_state["value"],)]
        if sql_lc.lstrip().startswith("insert into"):
            target_count_state["value"] = max(
                target_count_state["value"], source_distinct
            )
            return []
        return []

    db_client.client.execute.side_effect = execute_side_effect
    db_client.client.connection.database = "default"
    db_client.create_table = MagicMock()
    db_client._is_clustered = MagicMock(return_value=clustered)
    return db_client, target_count_state


def _run(
    *tables: str,
    db_client=None,
    dry_run: bool = False,
    cluster: str = "cluster",
) -> str:
    """Invoke the command with the standard source/target and return stdout."""
    out = StringIO()
    args = [
        "migrate_ch_vector_tables",
        "--source-database=default",
        "--target-database=futureagi",
        f"--tables={','.join(tables)}",
        f"--cluster={cluster}",
    ]
    if dry_run:
        args.append("--dry-run")
    cm = patch(
        "model_hub.management.commands.migrate_ch_vector_tables.ClickHouseVectorDB",
        return_value=db_client,
    ) if db_client is not None else patch("builtins.print")  # no-op patch
    with cm:
        call_command(*args, stdout=out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Refusal cases
# ---------------------------------------------------------------------------

def test_refuses_when_source_equals_target():
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "migrate_ch_vector_tables",
            "--source-database=default",
            "--target-database=default",
            "--tables=feedbacks",
            stdout=StringIO(),
        )
    assert "must differ" in str(excinfo.value)


def test_runs_on_single_node_ch():
    """Single-node CH is a valid target: create_table's own engine probe
    emits plain ``ReplacingMergeTree`` when there's only one replica, and
    the CREATE DATABASE bootstrap drops ``ON CLUSTER`` (which would
    require Keeper) on the same signal.
    """
    db_client, _ = _build_mock_db_client(clustered=False, expected_replicas=1)
    out = _run("feedbacks", db_client=db_client)
    assert "feedbacks:" in out
    db_client.create_table.assert_called_once()
    create_db_sql = [
        call.args[0]
        for call in db_client.client.execute.call_args_list
        if "create database" in call.args[0].lower()
    ]
    assert create_db_sql, "CREATE DATABASE should still run on single-node"
    assert all("on cluster" not in s.lower() for s in create_db_sql)


def test_refuses_unknown_table_name():
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "migrate_ch_vector_tables",
            "--source-database=default",
            "--target-database=futureagi",
            "--tables=feedbacks,not_a_real_table",
            stdout=StringIO(),
        )
    assert "not_a_real_table" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Dry-run vs real run
# ---------------------------------------------------------------------------

def test_dry_run_does_not_create_table_or_insert():
    db_client, _ = _build_mock_db_client(source_distinct=14)
    stdout = _run("feedbacks", db_client=db_client, dry_run=True)

    db_client.create_table.assert_not_called()
    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    assert not any(s.lower().lstrip().startswith("insert into") for s in executed_sqls), (
        "dry-run must not emit any INSERT statement"
    )
    assert "would_insert" in stdout.lower() or "dry run" in stdout.lower()


def test_creates_target_table_when_source_is_absent_so_future_writes_have_a_home():
    """``ground_truths`` doesn't exist in the legacy ``default`` DB yet, but
    new write paths point at ``futureagi.ground_truths``. The command must
    still create the target so the first runtime write doesn't error.
    """
    db_client, _ = _build_mock_db_client(table_exists=False)
    stdout = _run("ground_truths", db_client=db_client)

    db_client.create_table.assert_called_once_with(
        "ground_truths", cluster="cluster", database="futureagi"
    )
    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    assert not any(s.lower().lstrip().startswith("insert into") for s in executed_sqls)
    assert "nothing to copy" in stdout.lower()


def test_target_database_is_created_on_cluster_before_any_table_work():
    """``futureagi`` doesn't exist on prod yet. CREATE DATABASE has to fan
    out to every replica via ON CLUSTER, and it must run before any per-table
    SQL; otherwise the first CREATE TABLE hits UNKNOWN_DATABASE.
    """
    db_client, _ = _build_mock_db_client(source_distinct=3)
    _run("feedbacks", db_client=db_client)

    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    create_db_idx = next(
        (i for i, s in enumerate(executed_sqls)
         if "create database if not exists futureagi" in s.lower()
         and "on cluster 'cluster'" in s.lower()),
        None,
    )
    assert create_db_idx is not None, (
        "expected CREATE DATABASE IF NOT EXISTS futureagi ON CLUSTER 'cluster'"
    )
    # First per-table probe is the source_exists check against system.tables
    # (without clusterAllReplicas). Confirm the CREATE DATABASE ran first.
    first_per_table_idx = next(
        i for i, s in enumerate(executed_sqls)
        if "from system.tables" in s.lower() and "clusterallreplicas" not in s.lower()
    )
    assert create_db_idx < first_per_table_idx


def test_dry_run_does_not_create_target_database():
    """A dry-run must not mutate the cluster, including no CREATE DATABASE."""
    db_client, _ = _build_mock_db_client(source_distinct=3)
    _run("feedbacks", db_client=db_client, dry_run=True)

    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    assert not any(
        "create database" in s.lower() for s in executed_sqls
    ), "dry-run must not issue CREATE DATABASE"


def test_real_run_emits_insert_with_explicit_column_list_and_id_anti_join():
    """The core behaviour: reads from every replica via clusterAllReplicas
    with an EXPLICIT column list (never ``SELECT *``) so a drifted legacy
    source can't silently misalign by position; writes only the IDs that
    aren't already at the target, so re-runs are idempotent.
    """
    db_client, target_state = _build_mock_db_client(
        source_distinct=14, target_count_before=0
    )
    stdout = _run("feedbacks", db_client=db_client)

    db_client.create_table.assert_called_once_with(
        "feedbacks", cluster="cluster", database="futureagi"
    )
    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    insert_stmts = [s for s in executed_sqls if s.lower().lstrip().startswith("insert into")]
    assert len(insert_stmts) == 1
    insert_sql = insert_stmts[0]
    assert "INSERT INTO futureagi.feedbacks" in insert_sql
    assert "clusterAllReplicas('cluster', default.feedbacks)" in insert_sql
    assert "WHERE id NOT IN (SELECT id FROM futureagi.feedbacks)" in insert_sql
    # Explicit column list must be present; SELECT * would misalign a
    # drifted legacy source silently.
    assert "SELECT *" not in insert_sql, (
        "INSERT must use an explicit column list, not SELECT *"
    )
    for col in _VECTOR_COLUMNS:
        assert f"`{col}`" in insert_sql, (
            f"column `{col}` missing from explicit list"
        )
    assert target_state["value"] == 14
    assert "copied 14 rows" in stdout


def test_parity_check_reads_system_tables_total_rows_not_leader_only_count():
    """Post-INSERT parity is read through
    ``clusterAllReplicas(system.tables)`` on ``total_rows``, not
    ``count() ... GROUP BY hostName()`` on the target table.

    The older ``count() GROUP BY hostName()`` idiom drops any replica whose
    local copy is empty (no group emitted), so a follower still lagging
    disappears from the result entirely and the parity check reads as
    "converged" over the replicas that happen to be present.
    ``system.tables.total_rows`` returns one row per replica that holds
    the table, including the empty ones, which is the signal the gate
    actually needs.
    """
    db_client, _ = _build_mock_db_client(source_distinct=14, target_count_before=0)
    _run("feedbacks", db_client=db_client)

    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    system_tables_reads = [
        s for s in executed_sqls
        if "clusterAllReplicas" in s
        and "system.tables" in s
        and "total_rows" in s
    ]
    assert system_tables_reads, (
        "post-INSERT parity must read through system.tables.total_rows"
    )
    legacy_group_by_reads = [
        s for s in executed_sqls
        if "GROUP BY hostName()" in s
    ]
    assert not legacy_group_by_reads, (
        "must not fall back to the empty-replica-hiding "
        "'count() GROUP BY hostName()' idiom"
    )


def test_raises_command_error_when_a_replica_is_missing_from_parity():
    """Convergence requires the FULL replica set to be present in the
    per-replica-counts result. If ``system.clusters`` reports 3 replicas but
    only 2 respond, the table has not converged and the command must exit
    non-zero so an exit-code-gated cutover can't advance.
    """
    db_client, _ = _build_mock_db_client(
        source_distinct=14,
        expected_replicas=3,
        replicas_present=2,  # one follower still registering
    )
    # Shrink the polling window so the test doesn't spend 30s waiting.
    with patch(
        "model_hub.services.ch_migration.time.sleep",
        return_value=None,
    ), patch(
        "model_hub.services.ch_migration.time.monotonic",
        side_effect=[0.0, 0.0, 0.0, 999.0, 999.0],
    ):
        with pytest.raises(CommandError) as excinfo:
            _run("feedbacks", db_client=db_client)
    assert "did not fully converge" in str(excinfo.value).lower()
    assert "feedbacks" in str(excinfo.value)


def test_cluster_flag_is_threaded_into_clusterallreplicas_and_create_table():
    """A non-default --cluster value must propagate into the INSERT SELECT,
    the parity-check query, AND the per-table CREATE TABLE. The earlier
    revision of this test only checked the INSERT and parity SQL; CREATE
    TABLE goes through `db_client.create_table`, and a hardcoded cluster
    name there would break any deployment whose `remote_servers` config
    calls the cluster anything other than the hardcoded literal.
    """
    db_client, _ = _build_mock_db_client(source_distinct=3)
    _run("feedbacks", db_client=db_client, cluster="fi_cluster")

    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    assert any("clusterAllReplicas('fi_cluster'," in s for s in executed_sqls)
    assert not any("clusterAllReplicas('cluster'," in s for s in executed_sqls)
    # Plus the CREATE DATABASE fans out on the same cluster.
    assert any(
        "create database if not exists futureagi" in s.lower()
        and "on cluster 'fi_cluster'" in s.lower()
        for s in executed_sqls
    )
    # And per-table CREATE TABLE goes through db_client.create_table with the
    # same cluster name AND the target database; otherwise the hardcoded-
    # cluster bug ships silently OR the table lands in the source database.
    create_calls = db_client.create_table.call_args_list
    assert len(create_calls) == 1
    assert create_calls[0].kwargs.get("cluster") == "fi_cluster"
    assert create_calls[0].kwargs.get("database") == "futureagi"


def test_second_run_is_a_no_op_when_target_already_at_parity():
    """The id-anti-join filter means a re-run on already-migrated data
    inserts nothing. The command must report 0 rows copied without erroring.
    """
    db_client, _ = _build_mock_db_client(
        source_distinct=14, target_count_before=14
    )
    stdout = _run("feedbacks", db_client=db_client)
    assert "copied 0 rows" in stdout


def test_migrates_feedbacks_ground_truths_and_syn_in_one_invocation():
    """End-to-end multi-table case. syn is explicitly listed because the
    agent_evaluator search_knowledge_base tool reads from it; if a future
    PR drops syn from KNOWN_TABLES, this test fails loudly.
    """
    db_client, _ = _build_mock_db_client(source_distinct=7)
    _run("feedbacks", "ground_truths", "syn", db_client=db_client)

    create_calls = [c.args[0] for c in db_client.create_table.call_args_list]
    assert create_calls == ["feedbacks", "ground_truths", "syn"]

    insert_sqls = [
        c.args[0]
        for c in db_client.client.execute.call_args_list
        if c.args[0].lower().lstrip().startswith("insert into")
    ]
    assert len(insert_sqls) == 3
    for table in ("feedbacks", "ground_truths", "syn"):
        assert any(
            f"INSERT INTO futureagi.{table}" in sql
            and f"clusterAllReplicas('cluster', default.{table})" in sql
            for sql in insert_sqls
        ), f"missing INSERT for {table}"
