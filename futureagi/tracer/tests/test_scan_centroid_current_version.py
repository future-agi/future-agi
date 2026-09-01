"""The incremental centroid update must read the CURRENT centroid version.

``cluster_centroids`` is a ReplacingMergeTree and ``assign_to_cluster`` INSERTs a
new row per member instead of updating one, so every historical version of a
centroid coexists until a background merge collapses them. The read that feeds the
running mean therefore has to name which version it wants. It used to be a bare
``SELECT ... LIMIT 1``, which returns an arbitrary one: the mean gets computed from
a stale vector and a stale ``member_count``, and since that count is the weight in
``_update_centroid`` the error compounds into every later update rather than washing
out.

DB-free: every Django and ClickHouse collaborator is mocked. These are
revert-catchers on the query shape, not proof the SQL is right — a mocked client
cannot execute ClickHouse. Two hazards specific to this fix are covered
because both are silent in production:

  - dropping ``GROUP BY`` while keeping ``argMax``. An aggregate with no GROUP BY
    always returns exactly one row, so a cluster with no stored centroid would come
    back as a phantom row of empty defaults and ``if rows:`` would take the wrong
    branch, seeding the running mean from an empty vector instead of the issue.
  - dropping the tuple recency key back to plain ``last_updated``, which is
    second-granularity: two members joining one cluster inside the same second are
    unordered, and the tie-break has to be ``member_count`` because it is the only
    monotonically growing column available.
"""

from unittest.mock import MagicMock, patch

import pytest

from tracer.queries.scan_clustering import assign_to_cluster
from tracer.types.scan_types import ClusterableIssue

_CLUSTER = "S-1234ABCD"
_PROJECT = "3fa85f64-5717-4562-b3fc-2c963f66afa6"


class _RecordingClient:
    """Captures every statement; replays canned rows for the SELECT."""

    def __init__(self, select_rows):
        self._select_rows = select_rows
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params or {}))
        return self._select_rows if "SELECT" in sql else []


def _issue() -> ClusterableIssue:
    return ClusterableIssue(
        issue_id="11111111-1111-1111-1111-111111111111",
        trace_id="22222222-2222-2222-2222-222222222222",
        project_id=_PROJECT,
        category="Tool-related",
        group="Tool Failures",
        fix_layer="Prompt",
        brief="answered without calling the tool",
        confidence="H",
    )


def _run(select_rows):
    """Drive assign_to_cluster with Django collaborators stubbed; return the client."""
    client = _RecordingClient(select_rows)
    db = MagicMock()
    db.client = client
    with patch("tracer.queries.scan_clustering.ClickHouseVectorDB", return_value=db), \
         patch("tracer.queries.scan_clustering.ensure_centroid_table"), \
         patch("tracer.queries.scan_clustering.TraceErrorGroup") as group, \
         patch("tracer.queries.scan_clustering.TraceScanIssue"), \
         patch("tracer.queries.scan_clustering.ErrorClusterTraces"):
        group.objects.get.return_value = MagicMock(error_count=0, total_events=0)
        assign_to_cluster(_CLUSTER, _PROJECT, _issue(), [1.0, 0.0, 0.0])
    return client


def _select(client):
    return next(sql for sql, _ in client.statements if "SELECT" in sql)


def _insert_params(client):
    return next(p for sql, p in client.statements if "INSERT" in sql)


def test_reads_the_current_version_not_an_arbitrary_one():
    client = _run([([0.0, 1.0, 0.0], 4)])
    sql = _select(client)
    assert "argMax" in sql, (
        "a bare LIMIT 1 returns an arbitrary ReplacingMergeTree version"
    )
    assert "GROUP BY" in sql, (
        "argMax without GROUP BY returns a phantom row when no centroid exists"
    )
    assert "last_updated" in sql and "member_count" in sql, (
        "recency key must break same-second ties on the monotonic member_count"
    )


def test_read_is_project_scoped():
    client = _run([([0.0, 1.0, 0.0], 4)])
    sql = _select(client)
    params = next(p for s, p in client.statements if "SELECT" in s)
    assert "project_id" in sql and params.get("project_id") == _PROJECT


def test_running_mean_uses_the_returned_count_as_the_weight():
    # 4 existing members at [0,1,0], new member [1,0,0] -> (4*old + new)/5
    client = _run([([0.0, 1.0, 0.0], 4)])
    params = _insert_params(client)
    assert params["member_count"] == 5
    assert params["centroid"] == pytest.approx([0.2, 0.8, 0.0])


def test_missing_centroid_seeds_from_the_issue_rather_than_an_empty_vector():
    """GROUP BY makes 'no stored centroid' return zero rows, keeping this reachable."""
    client = _run([])
    params = _insert_params(client)
    assert params["member_count"] == 1
    assert params["centroid"] == [1.0, 0.0, 0.0]
