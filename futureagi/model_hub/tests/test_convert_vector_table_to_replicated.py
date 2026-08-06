"""Unit tests for ``convert_vector_table_to_replicated``.

The CH client is mocked, so these pin control flow and safety: no-op when
already Replicated / absent / single-node, dry-run performs no DDL, mixed
engines abort, and a non-converged copy aborts BEFORE any swap.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

CMD = "convert_vector_table_to_replicated"
MOD = "model_hub.management.commands.convert_vector_table_to_replicated"


def _run(*args):
    out = StringIO()
    call_command(CMD, *args, stdout=out, stderr=out)
    return out.getvalue()


def test_unknown_table_errors():
    with pytest.raises(CommandError, match="--table must be one of"):
        _run("--table", "nope")


def _db(clustered=True):
    """A patched ClickHouseVectorDB with a mock client."""
    db = MagicMock()
    db._is_clustered.return_value = clustered
    db.client = MagicMock()
    return db


def test_single_node_is_noop():
    db = _db(clustered=False)
    with patch(f"{MOD}.ClickHouseVectorDB", return_value=db):
        out = _run("--table", "feedbacks")
    assert "single-node" in out
    db.create_table.assert_not_called()
    db.client.execute.assert_not_called()


def test_already_replicated_is_noop():
    db = _db()
    with (
        patch(f"{MOD}.ClickHouseVectorDB", return_value=db),
        patch(f"{MOD}._distinct_engines", return_value={"ReplicatedReplacingMergeTree"}),
    ):
        out = _run("--table", "feedbacks")
    assert "already Replicated" in out
    db.create_table.assert_not_called()


def test_absent_is_noop():
    db = _db()
    with (
        patch(f"{MOD}.ClickHouseVectorDB", return_value=db),
        patch(f"{MOD}._distinct_engines", return_value=set()),
    ):
        out = _run("--table", "feedbacks")
    assert "absent" in out
    db.create_table.assert_not_called()


def test_mixed_engines_abort():
    db = _db()
    with (
        patch(f"{MOD}.ClickHouseVectorDB", return_value=db),
        patch(
            f"{MOD}._distinct_engines",
            return_value={"MergeTree", "ReplicatedReplacingMergeTree"},
        ),
    ):
        with pytest.raises(CommandError, match="engines differ across replicas"):
            _run("--table", "feedbacks", "--write-freeze-confirmed")
    db.create_table.assert_not_called()


def test_dry_run_plain_writes_nothing():
    db = _db()
    # union count + per-replica counts read via the client
    db.client.execute.return_value = [(5,)]
    with (
        patch(f"{MOD}.ClickHouseVectorDB", return_value=db),
        patch(f"{MOD}._distinct_engines", return_value={"MergeTree"}),
        patch(f"{MOD}.per_replica_counts", return_value={"r0": 2, "r1": 3, "r2": 0}),
        patch(f"{MOD}.expected_replica_count", return_value=3),
        patch(f"{MOD}._table_hosts", side_effect=[{"r0", "r1", "r2"}, set(), set()]),
        patch(f"{MOD}._conflicting_ids", return_value=0),
    ):
        out = _run("--table", "feedbacks", "--dry-run")
    assert "DRY-RUN" in out
    db.create_table.assert_not_called()  # no temp table, no swap


def test_execute_aborts_before_swap_when_not_converged():
    db = _db()
    db.client.execute.return_value = [(5,)]  # union_count = 5
    with (
        patch(f"{MOD}.ClickHouseVectorDB", return_value=db),
        patch(f"{MOD}._distinct_engines", return_value={"MergeTree"}),
        patch(f"{MOD}.per_replica_counts", return_value={"r0": 2, "r1": 3, "r2": 0}),
        patch(f"{MOD}.expected_replica_count", return_value=3),
        patch(f"{MOD}._table_hosts", side_effect=[{"r0", "r1", "r2"}, set(), set()]),
        patch(f"{MOD}._conflicting_ids", return_value=0),
        patch(f"{MOD}._shared_columns_same_db", return_value=["id", "eval_id", "vector"]),
        patch(f"{MOD}.poll_replica_parity", return_value=({"r0": 5}, False)),
    ):
        with pytest.raises(CommandError, match="did not converge"):
            _run("--table", "feedbacks", "--write-freeze-confirmed")
    # temp table created + insert attempted, but NO EXCHANGE was issued
    db.create_table.assert_called_once()
    issued = " ".join(str(c.args[0]) for c in db.client.execute.call_args_list)
    assert "EXCHANGE TABLES" not in issued
    # Lock the copy shape: explicit column list + LIMIT 1 BY id, never SELECT *.
    assert "`id`, `eval_id`, `vector`" in issued
    assert "LIMIT 1 BY id" in issued
    assert "SELECT *" not in issued


def test_execute_without_write_freeze_aborts():
    db = _db()
    db.client.execute.return_value = [(5,)]
    with (
        patch(f"{MOD}.ClickHouseVectorDB", return_value=db),
        patch(f"{MOD}._distinct_engines", return_value={"MergeTree"}),
        patch(f"{MOD}.per_replica_counts", return_value={"r0": 2, "r1": 3, "r2": 0}),
        patch(f"{MOD}.expected_replica_count", return_value=3),
        patch(f"{MOD}._table_hosts", side_effect=[{"r0", "r1", "r2"}, set(), set()]),
        patch(f"{MOD}._conflicting_ids", return_value=0),
    ):
        # no --dry-run and no --write-freeze-confirmed: must abort before any DDL
        with pytest.raises(CommandError, match="write-freeze-confirmed"):
            _run("--table", "feedbacks")
    db.create_table.assert_not_called()


def test_shared_columns_aborts_on_extra_live_column():
    from model_hub.management.commands.convert_vector_table_to_replicated import (
        _shared_columns_same_db,
    )

    client = MagicMock()
    client.execute.side_effect = [
        [("id",), ("eval_id",), ("legacy_col",)],  # live table columns
        [("id",), ("eval_id",)],  # target (new-schema) columns
    ]
    with pytest.raises(CommandError, match="legacy_col"):
        _shared_columns_same_db(client, "db", "syn", "syn__repl_tmp")
