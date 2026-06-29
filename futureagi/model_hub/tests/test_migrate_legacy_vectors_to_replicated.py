"""Behavioural tests for ``migrate_legacy_vectors_to_replicated``.

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


def _build_mock_db_client(
    *,
    source_distinct: int = 14,
    target_count_before: int = 0,
    table_exists: bool = True,
    clustered: bool = True,
):
    """A ClickHouseVectorDB stand-in with scripted query returns.

    The command issues a small set of queries per table; we dispatch by
    substring match. ``clustered`` toggles what ``_is_clustered`` returns
    so we can exercise the "refuse on single-node CH" path.
    """
    db_client = MagicMock()
    target_count_state = {"value": target_count_before}

    def execute_side_effect(sql, params=None):
        sql_lc = sql.lower()
        if "from system.tables" in sql_lc:
            return [(1 if table_exists else 0,)]
        if "uniqexact(id)" in sql_lc and "clusterallreplicas" in sql_lc:
            return [(source_distinct,)]
        if "hostname()" in sql_lc and "clusterallreplicas" in sql_lc:
            return [
                ("ch-replica-1", target_count_state["value"]),
                ("ch-replica-2", target_count_state["value"]),
                ("ch-replica-3", target_count_state["value"]),
            ]
        if "select count() from" in sql_lc and "clusterallreplicas" not in sql_lc:
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
    # Explicit return so tests don't depend on MagicMock truthiness.
    db_client._is_clustered = MagicMock(return_value=clustered)
    return db_client, target_count_state


def _run(*tables: str, db_client=None, dry_run: bool = False) -> str:
    """Invoke the command with the standard source/target and return stdout."""
    out = StringIO()
    args = [
        "migrate_legacy_vectors_to_replicated",
        "--source-database=default",
        "--target-database=futureagi",
        f"--tables={','.join(tables)}",
    ]
    if dry_run:
        args.append("--dry-run")
    cm = patch(
        "model_hub.management.commands.migrate_legacy_vectors_to_replicated.ClickHouseVectorDB",
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
            "migrate_legacy_vectors_to_replicated",
            "--source-database=default",
            "--target-database=default",
            "--tables=feedbacks",
            stdout=StringIO(),
        )
    assert "must differ" in str(excinfo.value)


def test_refuses_when_ch_is_not_clustered():
    """Running on a single-node CH would create the target as plain
    ReplacingMergeTree and re-introduce the non-replicated bug.
    """
    db_client, _ = _build_mock_db_client(clustered=False)
    with patch(
        "model_hub.management.commands.migrate_legacy_vectors_to_replicated.ClickHouseVectorDB",
        return_value=db_client,
    ):
        with pytest.raises(CommandError) as excinfo:
            call_command(
                "migrate_legacy_vectors_to_replicated",
                "--source-database=default",
                "--target-database=futureagi",
                "--tables=feedbacks",
                stdout=StringIO(),
            )
    assert "replica" in str(excinfo.value).lower()


def test_refuses_unknown_table_name():
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "migrate_legacy_vectors_to_replicated",
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
    assert "would copy" in stdout.lower()


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
    first_per_table_idx = next(
        i for i, s in enumerate(executed_sqls) if "from system.tables" in s.lower()
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


def test_real_run_emits_insert_select_clusterallreplicas_with_id_not_in_target():
    """The core behaviour: reads from every replica via clusterAllReplicas,
    writes only the IDs that aren't already at the target. This is what
    makes the migration both correct (no data loss) and idempotent.
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
    assert target_state["value"] == 14
    assert "copied 14 rows" in stdout


def test_post_insert_parity_check_queries_every_replica_not_just_leader():
    """Querying ``SELECT count() FROM target`` would only hit the leader and
    hide a replica that hasn't pulled the new rows from Keeper yet. The
    post-INSERT parity check must go through ``clusterAllReplicas`` and
    ``GROUP BY hostName()`` so a lagging replica surfaces as a divergence.
    """
    db_client, _ = _build_mock_db_client(source_distinct=14, target_count_before=0)
    _run("feedbacks", db_client=db_client)

    executed_sqls = [c.args[0] for c in db_client.client.execute.call_args_list]
    parity_sqls = [
        s
        for s in executed_sqls
        if "hostName()" in s
        and "clusterAllReplicas" in s
        and "GROUP BY hostName()" in s
    ]
    assert len(parity_sqls) >= 1, (
        "post-INSERT parity check must use clusterAllReplicas + GROUP BY hostName()"
    )


def test_cluster_flag_is_threaded_into_clusterallreplicas_and_create_table():
    """A non-default --cluster value must propagate into the INSERT SELECT,
    the parity-check query, AND the per-table CREATE TABLE. The earlier
    revision of this test only checked the INSERT and parity SQL; CREATE
    TABLE goes through `db_client.create_table`, and a hardcoded cluster
    name there would break any deployment whose `remote_servers` config
    calls the cluster anything other than the hardcoded literal.
    """
    db_client, _ = _build_mock_db_client(source_distinct=3)
    out = StringIO()
    with patch(
        "model_hub.management.commands.migrate_legacy_vectors_to_replicated.ClickHouseVectorDB",
        return_value=db_client,
    ):
        call_command(
            "migrate_legacy_vectors_to_replicated",
            "--source-database=default",
            "--target-database=futureagi",
            "--tables=feedbacks",
            "--cluster=fi_cluster",
            stdout=out,
        )

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
