"""Regression tests for fix 4 — dropping the category partition.

Candidate centroids used to be filtered by ``family`` (the scanner's category)
before the distance sort, so two clusters in different categories could never
merge no matter how close their embeddings sat. The scanner picks the category
from free text, so one defect described two ways lands in two categories — and
on the post-fix-5 corpus that is visible directly:

    S-232AA3EB  Context Handling Failures     15 traces
    S-15263D7F  Poor Information Retrieval     5 traces

both "asked for a quarter, queried a different period". No embedding could
merge those while the family clause was in the WHERE.

These assert on the emitted SQL rather than standing up ClickHouse: what is
being pinned is which rows are *eligible* to be sorted, which is a property of
the statement. The toggle is exercised in both positions so reverting it stays
a one-line change rather than a code change.
"""

import re
from unittest.mock import patch

from tracer.queries import scan_clustering
from tracer.queries.scan_clustering import find_nearest_centroid


def _executed(mock_db):
    return mock_db.return_value.client.execute.call_args_list[-1]


def _norm(sql):
    return re.sub(r"\s+", " ", sql).strip()


def _run(embedding=(0.1, 0.2), category="Context Handling Failures"):
    with patch(
        "tracer.queries.scan_clustering.ClickHouseVectorDB"
    ) as db, patch("tracer.queries.scan_clustering.ensure_centroid_table"):
        db.return_value.client.execute.return_value = []
        find_nearest_centroid(list(embedding), "proj-1", category)
    return _executed(db)


class TestCategoryPartitionDisabled:
    def test_family_is_not_in_the_where_clause(self):
        """The fix itself: category must not gate which centroids compete."""
        assert scan_clustering.PARTITION_BY_CATEGORY is False, (
            "these tests describe the shipped default"
        )
        sql = _norm(_run().args[0])
        assert "family = " not in sql

    def test_project_scoping_survives(self):
        """Dropping the category partition must not drop tenant isolation —
        that would leak one customer's clusters into another's feed."""
        call = _run()
        sql = _norm(call.args[0])
        assert "project_id = %(project_id)s" in sql
        assert call.args[1]["project_id"] == "proj-1"

    def test_two_categories_produce_the_same_candidate_set(self):
        """The S-232AA3EB / S-15263D7F case, reduced to its mechanism.

        The same embedding arriving under two different scanner categories must
        query identically — only then can the distance sort decide, which is
        what lets the two halves of one defect find each other.
        """
        a = _norm(_run(category="Context Handling Failures").args[0])
        b = _norm(_run(category="Poor Information Retrieval").args[0])
        assert a == b


class TestCategoryPartitionEnabled:
    def test_toggle_restores_the_family_filter(self):
        """Kept as a switch: if cross-category merging proves too loose it can
        be reverted without touching the query builder."""
        with patch.object(scan_clustering, "PARTITION_BY_CATEGORY", True):
            call = _run(category="Poor Information Retrieval")

        sql = _norm(call.args[0])
        assert "family = %(family)s" in sql
        assert call.args[1]["family"] == "Poor Information Retrieval"

    def test_toggle_is_the_only_difference(self):
        """Guards against the two branches drifting apart — everything except
        the family predicate must be identical in both positions."""
        off = _norm(_run().args[0])
        with patch.object(scan_clustering, "PARTITION_BY_CATEGORY", True):
            on = _norm(_run().args[0])

        assert on.replace("AND family = %(family)s ", "") == off
