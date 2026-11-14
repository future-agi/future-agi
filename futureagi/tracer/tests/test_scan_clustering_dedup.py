"""Regression tests for scanner cluster de-duplication.

``create_cluster`` derives its ``cluster_id`` from md5(project, category,
brief[:100]), so the SAME failure arriving twice reproduces the SAME id by
construction. The pre-existing "handle collision" branch treated that as an md5
collision and minted a *different* id, which manufactured pairs of clusters
carrying byte-identical titles seconds apart (observed in production: 8 titles
across 16 clusters, 6 pairs sharing one trace_id).

The branch is reached whenever ``find_nearest_centroid`` misses a cluster it
should have matched — ``cluster_centroids`` is a ReplacingMergeTree read without
FINAL, so a just-written centroid is not reliably visible to the next lookup.

These pin the corrected behaviour: join on a repeat, fork only on a genuine
collision (same id, different failure).
"""

import hashlib
import uuid
from unittest.mock import patch

import pytest

from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup
from tracer.queries.scan_clustering import create_cluster
from tracer.types.scan_types import ClusterableIssue

CATEGORY = "Language-only"
BRIEF = "Stated portfolio values and calculations without calling data tools"


def _issue(project_id: str, brief: str = BRIEF, category: str = CATEGORY):
    return ClusterableIssue(
        issue_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        project_id=str(project_id),
        category=category,
        group="Language-only",
        fix_layer="tools",
        brief=brief,
        confidence="high",
    )


def _expected_id(project_id: str, category: str, brief: str) -> str:
    """Mirror the id derivation so the test pins the contract, not the impl."""
    base = f"{project_id}|scanner|{category}|{brief[:100]}"
    return f"S-{hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()[:8].upper()}"


def _seed_cluster(project, cluster_id: str, *, title: str, category: str):
    return TraceErrorGroup.objects.create(
        project_id=project.id,
        cluster_id=cluster_id,
        source=ClusterSource.SCANNER,
        issue_group="Language-only",
        issue_category=category,
        fix_layer="tools",
        title=title,
        error_type="Language-only",
        total_events=1,
        unique_traces=1,
        error_count=1,
    )


@pytest.mark.django_db
class TestCreateClusterDeduplication:
    def test_repeat_brief_joins_existing_cluster_instead_of_forking(self, project):
        """The production bug: identical (category, brief) must NOT create a
        second cluster. It must be routed into the one already holding it."""
        cluster_id = _expected_id(str(project.id), CATEGORY, BRIEF)
        _seed_cluster(project, cluster_id, title=BRIEF, category=CATEGORY)

        issue = _issue(project.id)
        # The create-path collaborators are patched too, so that if the join is
        # ever regressed this fails on the assertions below (a second cluster
        # exists) rather than blowing up inside the create path.
        with patch(
            "tracer.queries.scan_clustering.assign_to_cluster"
        ) as assign, patch(
            "tracer.queries.scan_clustering._seed_severity", return_value="high"
        ) as seed_sev, patch(
            "tracer.queries.scan_clustering.ensure_centroid_table"
        ), patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB"
        ), patch(
            "tracer.queries.scan_clustering.ErrorClusterTraces"
        ), patch(
            "tracer.queries.scan_clustering.TraceScanIssue"
        ):
            returned = create_cluster(str(project.id), issue, [0.1, 0.2, 0.3])

        assert returned == cluster_id
        assign.assert_called_once()
        assert assign.call_args.args[0] == cluster_id
        # No second cluster, and no wasted severity LLM call on the join path.
        assert TraceErrorGroup.objects.filter(project_id=project.id).count() == 1
        seed_sev.assert_not_called()

    def test_true_hash_collision_still_gets_a_distinct_id(self, project):
        """A row under the same id but describing a DIFFERENT failure is a real
        collision — it must still fork, or the two failures would be merged."""
        cluster_id = _expected_id(str(project.id), CATEGORY, BRIEF)
        _seed_cluster(
            project,
            cluster_id,
            title="An entirely unrelated failure",
            category=CATEGORY,
        )

        issue = _issue(project.id)
        with patch(
            "tracer.queries.scan_clustering.assign_to_cluster"
        ) as assign, patch(
            "tracer.queries.scan_clustering._seed_severity", return_value="high"
        ), patch(
            "tracer.queries.scan_clustering.ensure_centroid_table"
        ), patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB"
        ), patch(
            "tracer.queries.scan_clustering.ErrorClusterTraces"
        ), patch(
            "tracer.queries.scan_clustering.TraceScanIssue"
        ):
            returned = create_cluster(str(project.id), issue, [0.1, 0.2, 0.3])

        assign.assert_not_called()
        assert returned != cluster_id
        assert TraceErrorGroup.objects.filter(project_id=project.id).count() == 2
        assert TraceErrorGroup.objects.filter(
            project_id=project.id, cluster_id=returned, title=BRIEF
        ).exists()

    def test_differing_category_does_not_join(self, project):
        """Same brief under a different category is a different cluster family —
        the id differs, so this must take the normal create path."""
        other = _expected_id(str(project.id), CATEGORY, BRIEF)
        _seed_cluster(project, other, title=BRIEF, category=CATEGORY)

        issue = _issue(project.id, category="Goal Deviation")
        with patch(
            "tracer.queries.scan_clustering.assign_to_cluster"
        ) as assign, patch(
            "tracer.queries.scan_clustering._seed_severity", return_value="high"
        ), patch(
            "tracer.queries.scan_clustering.ensure_centroid_table"
        ), patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB"
        ), patch(
            "tracer.queries.scan_clustering.ErrorClusterTraces"
        ), patch(
            "tracer.queries.scan_clustering.TraceScanIssue"
        ):
            returned = create_cluster(str(project.id), issue, [0.1, 0.2, 0.3])

        assign.assert_not_called()
        assert returned == _expected_id(str(project.id), "Goal Deviation", BRIEF)
