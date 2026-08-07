"""The Feed's occurrence count must equal the members it claims to count.

``assign_to_cluster`` incremented ``error_count`` unconditionally while both
membership writes it counts are idempotent — the issue's cluster FK is an
``update``, the junction row a ``get_or_create``. Re-running the scan over a
trace therefore bumped the number the Feed renders as "occurrences" without
adding a member.

Measured on live data before the fix: a fifth of rendered scanner entries were
wrong, the worst showing **33 occurrences against 3 issues across 3 traces**, and
none of the gap was explained by soft-deleted issues. ``unique_traces`` sat
beside it and stayed correct for the one reason that matters here — it was always
recomputed from rows rather than incremented. Both counts are now derived the
same way.

The merge path had the same shape: it summed both sides' counters after
re-pointing every issue, so it carried the drift forward and compounded it on
each subsequent merge.

ClickHouse is mocked; the assertions are on PG counts, which is where the
rendered number lives.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.models.trace_scan import (
    TraceScanIssue,
    TraceScanResult,
    TraceScanStatus,
)
from tracer.queries.scan_clustering import assign_to_cluster
from tracer.types.scan_types import ClusterableIssue

_EMBED = [0.1] * 8


def _cluster(project, cluster_id="S-COUNT001", **kw):
    return TraceErrorGroup.objects.create(
        project_id=project.id,
        cluster_id=cluster_id,
        source=ClusterSource.SCANNER,
        issue_group="Tool Failures",
        issue_category="Tool-related",
        fix_layer="Tools",
        title="agent skipped the required tool call",
        error_count=kw.get("error_count", 0),
        total_events=kw.get("total_events", 0),
        unique_traces=0,
        first_seen=timezone.now(),
        last_seen=timezone.now(),
    )


def _issue(project, trace_id=None, brief="agent skipped the required tool call"):
    """A scanned issue not yet attached to any cluster."""
    trace_id = trace_id or str(uuid.uuid4())
    sr = TraceScanResult.objects.create(
        trace_id=trace_id,
        project_id=project.id,
        status=TraceScanStatus.COMPLETED,
        has_issues=True,
        key_moments=[],
        meta={},
    )
    row = TraceScanIssue.objects.create(
        scan_result=sr,
        category="Tool-related",
        group="Tool Failures",
        fix_layer="Tools",
        confidence="H",
        brief=brief,
    )
    return ClusterableIssue(
        issue_id=str(row.id),
        trace_id=trace_id,
        project_id=str(project.id),
        category="Tool-related",
        group="Tool Failures",
        fix_layer="Tools",
        confidence="H",
        brief=brief,
    )


def _mock_ch():
    """Centroid store that accepts writes and reports no prior version."""
    db = MagicMock()
    db.return_value.client.execute.return_value = []
    return db


@pytest.mark.django_db
class TestErrorCountIsDerived:
    def test_rescanning_the_same_trace_does_not_inflate_occurrences(self, project):
        """The bug's exact signature: same trace assigned repeatedly.

        Both membership writes are idempotent, so after N assignments of one
        issue there is still one issue and one junction row. The rendered count
        must say 1, not N.
        """
        cluster = _cluster(project)
        issue = _issue(project)

        with patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB", _mock_ch()
        ):
            for _ in range(5):
                assign_to_cluster(cluster.cluster_id, str(project.id), issue, _EMBED)

        cluster.refresh_from_db()
        assert TraceScanIssue.objects.filter(cluster=cluster).count() == 1
        assert ErrorClusterTraces.objects.filter(cluster=cluster).count() == 1
        assert cluster.error_count == 1, (
            f"occurrences inflated to {cluster.error_count} by re-scanning one trace"
        )

    def test_distinct_traces_each_count_once(self, project):
        """Guard against the fix collapsing genuinely distinct members."""
        cluster = _cluster(project, "S-COUNT002")
        issues = [_issue(project) for _ in range(3)]

        with patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB", _mock_ch()
        ):
            for issue in issues:
                assign_to_cluster(cluster.cluster_id, str(project.id), issue, _EMBED)

        cluster.refresh_from_db()
        assert cluster.error_count == 3
        assert cluster.unique_traces == 3

    def test_count_matches_members_after_repeated_and_new_assignments(self, project):
        """Mixed traffic — the realistic shape, since prod re-scans and grows."""
        cluster = _cluster(project, "S-COUNT003")
        first, second = _issue(project), _issue(project)

        with patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB", _mock_ch()
        ):
            assign_to_cluster(cluster.cluster_id, str(project.id), first, _EMBED)
            assign_to_cluster(cluster.cluster_id, str(project.id), first, _EMBED)
            assign_to_cluster(cluster.cluster_id, str(project.id), second, _EMBED)
            assign_to_cluster(cluster.cluster_id, str(project.id), first, _EMBED)

        cluster.refresh_from_db()
        live = TraceScanIssue.objects.filter(cluster=cluster, deleted=False).count()
        assert cluster.error_count == live == 2
        assert cluster.unique_traces == 2

    def test_a_stale_stored_count_is_corrected_on_next_assignment(self, project):
        """Rows already drifted in the DB heal as soon as they take a member.

        The backfill migration repairs history; this pins that the code path
        cannot re-open the gap on a row it touches.
        """
        cluster = _cluster(project, "S-COUNT004", error_count=99)
        issue = _issue(project)

        with patch(
            "tracer.queries.scan_clustering.ClickHouseVectorDB", _mock_ch()
        ):
            assign_to_cluster(cluster.cluster_id, str(project.id), issue, _EMBED)

        cluster.refresh_from_db()
        assert cluster.error_count == 1
