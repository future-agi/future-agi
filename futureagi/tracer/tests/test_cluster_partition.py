"""Regression tests for the category partition on centroid matching.

Candidate centroids are filtered by ``family`` (the scanner's category) before
the distance sort, so two clusters in different categories can never merge no
matter how close their embeddings sit. That was dropped once, because the
scanner picks the category from free text and one defect described two ways
lands in two categories:

    S-232AA3EB  Context Handling Failures     15 traces
    S-15263D7F  Poor Information Retrieval     5 traces

both "asked for a quarter, queried a different period".

Distilling the brief before embedding fixes that at the source — the phrase is
now stable, and only the category choice wavers, on 1.2% of distinct phrases
and 4.5% of issues. Replaying one project's 1,277 real issues without the
partition, three of the four groups that appear only in that configuration are
one bug with mis-categorised strays, and the fourth is 312 members spanning
eight categories with none above 45%, filing "gives investment advice despite
policy prohibiting it" alongside "responds with irrelevant disclaimer". The
partition is back on: it costs the strays and prevents that entry.

These assert on the emitted SQL rather than standing up ClickHouse: what is
being pinned is which rows are *eligible* to be sorted, which is a property of
the statement. The toggle is exercised in both positions so flipping it stays a
one-line change rather than a code change.
"""

import re
from unittest.mock import patch

from tracer.queries import scan_clustering
from tracer.queries.scan_clustering import find_nearest_centroid


def _executed(mock_db):
    return mock_db.return_value.client.execute.call_args_list[-1]


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _run(embedding=(0.1, 0.2), category="Context Handling Failures"):
    with patch(
        "tracer.queries.scan_clustering.ClickHouseVectorDB"
    ) as db, patch("tracer.queries.scan_clustering.ensure_centroid_table"):
        db.return_value.client.execute.return_value = []
        find_nearest_centroid(list(embedding), "proj-1", category)
    return _executed(db)


class TestCategoryPartitionEnabled:
    def test_family_gates_the_candidate_set(self):
        """The shipped default: an issue only competes against its own category."""
        assert scan_clustering.PARTITION_BY_CATEGORY is True, (
            "these tests describe the shipped default"
        )
        call = _run(category="Poor Information Retrieval")
        sql = _norm(call.args[0])
        assert "family = %(family)s" in sql
        assert call.args[1]["family"] == "Poor Information Retrieval"

    def test_project_scoping_survives(self):
        """The category filter must not be the only thing scoping the read —
        losing the project predicate would leak one customer's clusters into
        another's feed."""
        call = _run()
        sql = _norm(call.args[0])
        assert "project_id = %(project_id)s" in sql
        assert call.args[1]["project_id"] == "proj-1"

    def test_two_categories_query_different_candidate_sets(self):
        """The cost this buys: the same embedding under two categories cannot
        reach the same centroids. That is the S-232AA3EB / S-15263D7F split, now
        priced at 4.5% of issues because distillation stabilised the phrase."""
        a = _norm(_run(category="Context Handling Failures").args[0])
        b = _run(category="Poor Information Retrieval")
        assert a == _norm(b.args[0])          # same SQL text...
        assert b.args[1]["family"] == "Poor Information Retrieval"   # ...different bind


class TestCategoryPartitionDisabled:
    def test_toggle_removes_the_family_filter(self):
        """Kept as a switch: if the stray fragments hurt more than the mixing,
        this reverts without touching the query builder."""
        with patch.object(scan_clustering, "PARTITION_BY_CATEGORY", False):
            sql = _norm(_run().args[0])
        assert "family = " not in sql

    def test_toggle_is_the_only_difference(self):
        """Guards against the two branches drifting apart — everything except
        the family predicate must be identical in both positions."""
        on = _norm(_run().args[0])
        with patch.object(scan_clustering, "PARTITION_BY_CATEGORY", False):
            off = _norm(_run().args[0])

        assert on.replace("AND family = %(family)s ", "") == off
