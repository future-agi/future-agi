"""Regression tests for fix 2 — centroid-store correctness.

Three defects, all in how ``cluster_centroids`` is written and read:

1. ``assign_to_cluster`` INSERTs a new row per member instead of updating, and
   ``find_nearest_centroid`` read the table without collapsing versions, so the
   distance sort ran over stale centroids as well as current ones.
2. ``last_updated`` is second-granularity, so two updates to one cluster inside
   the same second could not be ordered.
3. Centroids outlive their TraceErrorGroup. Matching an orphan made
   ``assign_to_cluster`` raise DoesNotExist straight into the blanket handler in
   ``cluster_issues``, dropping the issue permanently.

The SQL tests assert on the emitted query rather than standing up ClickHouse:
what is being pinned is that versions are collapsed *before* the distance sort,
which is a property of the statement.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.queries.scan_clustering import find_nearest_centroid
from tracer.types.scan_types import ClusterableIssue


def _executed_sql(mock_db):
    return mock_db.return_value.client.execute.call_args_list[-1].args[0]


def _norm(sql):
    return re.sub(r"\s+", " ", sql).strip()


class TestFindNearestCentroidCollapsesVersions:
    def test_dedupes_to_one_row_per_cluster_before_distance_sort(self):
        """The distance sort must run over current centroids only.

        Without the inner LIMIT 1 BY, every historical version of a centroid is
        a separate candidate row and the nearest *stale* version can win.
        """
        with patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB"
        ) as db, patch("tracer.queries.scan_clustering.ensure_centroid_table"):
            db.return_value.client.execute.return_value = []
            find_nearest_centroid([0.1, 0.2], "proj-1", "Language-only")

        sql = _norm(_executed_sql(db))
        assert "LIMIT 1 BY cluster_id" in sql
        # The dedupe has to happen in a subquery, i.e. before the outer sort.
        inner = sql.split("LIMIT 1 BY cluster_id")[0]
        assert "ORDER BY distance" not in inner, (
            "distance sort must come after version collapse, not before"
        )

    def test_orders_by_recency_then_member_count(self):
        """member_count breaks ties last_updated cannot.

        last_updated is DateTime (second granularity); two updates to one
        cluster in the same second are otherwise unordered, and member_count is
        strictly increasing per cluster.
        """
        with patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB"
        ) as db, patch("tracer.queries.scan_clustering.ensure_centroid_table"):
            db.return_value.client.execute.return_value = []
            find_nearest_centroid([0.1, 0.2], "proj-1", "Language-only")

        sql = _norm(_executed_sql(db))
        assert "ORDER BY last_updated DESC, member_count DESC" in sql

    def test_still_applies_the_threshold(self):
        """Guard against the rewrite quietly dropping the distance cutoff."""
        with patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB"
        ) as db, patch("tracer.queries.scan_clustering.ensure_centroid_table"):
            db.return_value.client.execute.return_value = [("S-AAA", 0.9)]
            assert find_nearest_centroid([0.1], "proj-1", "cat") is None

            db.return_value.client.execute.return_value = [("S-AAA", 0.01)]
            assert find_nearest_centroid([0.1], "proj-1", "cat") == ("S-AAA", 0.01)


def _issue():
    return ClusterableIssue(
        issue_id="issue-1", trace_id="trace-1", project_id="proj-1",
        category="Language-only", group="Language-only", fix_layer="tools",
        brief="Stated portfolio values without calling data tools",
        confidence="H",
    )


class TestOrphanedCentroidHandling:
    def test_orphan_match_deletes_centroid_and_still_clusters_the_issue(self):
        """The production silent-drop path.

        A centroid whose cluster was deleted still matches. Before the fix,
        assign_to_cluster raised DoesNotExist into cluster_issues' blanket
        handler and the issue was never clustered and never retried.
        """
        from tracer.utils import trace_scanner as ts

        with patch.object(ts, "get_unclustered_issues", return_value=[_issue()]), \
             patch.object(ts, "embed_texts", return_value=[[0.1, 0.2]]), \
             patch.object(ts, "find_nearest_centroid", return_value=("S-GHOST", 0.01)), \
             patch.object(ts, "assign_to_cluster", side_effect=TraceErrorGroup.DoesNotExist), \
             patch.object(ts, "delete_centroid") as delete_centroid, \
             patch.object(ts, "create_cluster", return_value="S-NEW") as create:
            summary = ts.cluster_issues("proj-1")

        delete_centroid.assert_called_once_with("S-GHOST", "proj-1")
        create.assert_called_once()
        assert summary.new_clusters == 1
        assert summary.clustered == 1, "issue must not be silently dropped"

    def test_healthy_match_does_not_delete_anything(self):
        from tracer.utils import trace_scanner as ts

        with patch.object(ts, "get_unclustered_issues", return_value=[_issue()]), \
             patch.object(ts, "embed_texts", return_value=[[0.1, 0.2]]), \
             patch.object(ts, "find_nearest_centroid", return_value=("S-REAL", 0.01)), \
             patch.object(ts, "assign_to_cluster") as assign, \
             patch.object(ts, "delete_centroid") as delete_centroid:
            summary = ts.cluster_issues("proj-1")

        assign.assert_called_once()
        delete_centroid.assert_not_called()
        assert summary.assigned == 1
        assert summary.new_clusters == 0


class TestJoinIsCountedAsAssigned:
    def test_join_counts_as_assigned_not_new_cluster(self):
        """A join means the centroid lookup missed a cluster it should have
        matched. Counting it as a new cluster hides exactly that signal."""
        from tracer.utils import trace_scanner as ts

        def fake_create(project_id, issue, embedding, on_join=None):
            if on_join:
                on_join()
            return "S-EXISTING"

        with patch.object(ts, "get_unclustered_issues", return_value=[_issue()]), \
             patch.object(ts, "embed_texts", return_value=[[0.1, 0.2]]), \
             patch.object(ts, "find_nearest_centroid", return_value=None), \
             patch.object(ts, "create_cluster", side_effect=fake_create):
            summary = ts.cluster_issues("proj-1")

        assert summary.assigned == 1, "a join is an assignment, not a creation"
        assert summary.new_clusters == 0
        assert summary.clustered == 1

    def test_genuine_create_still_counts_as_new_cluster(self):
        from tracer.utils import trace_scanner as ts

        with patch.object(ts, "get_unclustered_issues", return_value=[_issue()]), \
             patch.object(ts, "embed_texts", return_value=[[0.1, 0.2]]), \
             patch.object(ts, "find_nearest_centroid", return_value=None), \
             patch.object(ts, "create_cluster", return_value="S-NEW"):
            summary = ts.cluster_issues("proj-1")

        assert summary.new_clusters == 1
        assert summary.assigned == 0
