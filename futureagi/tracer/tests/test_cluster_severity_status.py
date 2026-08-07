"""Regression tests for fixes 6 and 7 — severity and status honesty.

Both fields were decided once, at cluster creation, from a single seed issue,
and never recomputed. In production that produced a feed where the grades
carried no information: 72% of clusters were high/critical, and 234 of 235 were
"escalating" — including 138 singletons seen exactly once.

Fix 6 re-derives severity from the cluster's *current* title (which after fix 3b
describes the group rather than one arbitrary member) and applies volume as a
floor. Fix 7 stops claiming a trend below a threshold where there is none.

Neither invents a model. They make the existing labels defensible.
"""

from unittest.mock import MagicMock, patch

import pytest

from tracer.models.trace_error_analysis import (
    ClusterSource,
    FeedIssueStatus,
    TraceErrorGroup,
)
from tracer.queries.scan_clustering import (
    _refresh_severity,
    _volume_floor,
)


def _cluster(project, *, traces=1, title="Queried the wrong period", **kwargs):
    return TraceErrorGroup.objects.create(
        project_id=project.id,
        cluster_id=kwargs.pop("cluster_id", "S-SEV0001"),
        source=ClusterSource.SCANNER,
        issue_group="Context Handling Failures",
        issue_category="Context Handling Failures",
        fix_layer="prompt",
        title=title,
        error_type="Context Handling Failures",
        total_events=traces,
        unique_traces=traces,
        error_count=traces,
        **kwargs,
    )


_EMBEDDING = [0.1] * 8


def _ch_stub():
    """ClickHouseVectorDB double whose reads return no rows.

    The centroid SELECT is unpacked when it returns anything, so a bare
    MagicMock fails on the unpack rather than on the behaviour under test.
    """
    db = MagicMock()
    db.client.execute.return_value = []
    return patch(
        "tracer.queries.scan_clustering.ClickHouseVectorDB", return_value=db
    )


def _issue(project, brief="Queried the wrong period"):
    """A real TraceScanIssue row plus the clustering view of it. Assignment
    writes to both, so a fabricated id would make the test pass without
    exercising the membership writes that sit beside the status field."""
    from tracer.models.trace_scan import TraceScanIssue, TraceScanResult, TraceScanStatus
    from tracer.types.scan_types import ClusterableIssue

    trace_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    result = TraceScanResult.objects.create(
        trace_id=trace_id,
        project_id=project.id,
        status=TraceScanStatus.COMPLETED,
        has_issues=True,
    )
    row = TraceScanIssue.objects.create(
        scan_result=result,
        category="Context Handling Failures",
        group="Context Handling Failures",
        fix_layer="prompt",
        confidence="H",
        brief=brief,
    )
    return ClusterableIssue(
        issue_id=str(row.id),
        trace_id=trace_id,
        project_id=str(project.id),
        category="Context Handling Failures",
        group="Context Handling Failures",
        fix_layer="prompt",
        brief=brief,
        confidence="H",
    )


def _graded(severity):
    """Pin the cheap-LLM grade so only the volume floor is under test."""
    return patch(
        "tracer.queries.scan_clustering._seed_severity", return_value=severity
    )


@pytest.mark.django_db
class TestSeverityVolumeFloor:
    def test_breadth_lifts_a_low_grade_to_medium(self, project):
        """A defect reproducing across many traces is at least a medium
        regardless of how its title reads — breadth is evidence the text alone
        cannot carry."""
        cluster = _cluster(project, traces=5)

        with _graded("low"):
            _refresh_severity(cluster)

        cluster.refresh_from_db()
        assert cluster.priority == "medium"
        assert cluster.combined_impact == "MEDIUM"

    def test_a_singleton_keeps_its_low_grade(self, project):
        """The floor must not apply to something seen once, or it re-inflates
        exactly the population fix 6 exists to deflate."""
        cluster = _cluster(project, traces=1)

        with _graded("low"):
            _refresh_severity(cluster)

        cluster.refresh_from_db()
        assert cluster.priority == "low"

    def test_high_volume_lifts_to_high(self, project):
        cluster = _cluster(project, traces=25)

        with _graded("low"):
            _refresh_severity(cluster)

        cluster.refresh_from_db()
        assert cluster.priority == "high"

    def test_the_floor_never_lowers_a_grade(self, project):
        """It is a floor, not an assignment. A one-off that genuinely lost a
        customer money stays critical."""
        cluster = _cluster(project, traces=1)

        with _graded("critical"):
            _refresh_severity(cluster)

        cluster.refresh_from_db()
        assert cluster.priority == "urgent"  # critical maps to urgent priority

    def test_missing_unique_traces_does_not_crash(self, project):
        cluster = _cluster(project, traces=1)
        cluster.unique_traces = None

        with _graded("medium"):
            _refresh_severity(cluster)

        cluster.refresh_from_db()
        assert cluster.priority == "medium"


@pytest.mark.django_db
class TestSeverityReadsTheCurrentTitle:
    def test_grades_the_cluster_title_not_the_seed_brief(self, project):
        """The point of running this after retitling: the classifier should see
        what the cluster is now called, not the first member that arrived."""
        cluster = _cluster(project, traces=3, title="Agent hallucinated holdings")

        with patch(
            "tracer.queries.scan_clustering._seed_severity", return_value="high"
        ) as seed:
            _refresh_severity(cluster)

        seed.assert_called_once_with(
            "Context Handling Failures", "Agent hallucinated holdings"
        )

    def test_no_grade_available_leaves_the_cluster_untouched(self, project):
        """OSS has no EE classifier and LLM calls fail; neither should rewrite a
        grade to a guess."""
        cluster = _cluster(project, traces=30, priority="low")
        before = cluster.updated_at

        with _graded(None):
            _refresh_severity(cluster)

        cluster.refresh_from_db()
        assert cluster.priority == "low"
        assert cluster.updated_at == before

    def test_no_write_when_the_grade_is_unchanged(self, project):
        cluster = _cluster(project, traces=1, priority="medium")
        before = cluster.updated_at

        with _graded("medium"):
            _refresh_severity(cluster)

        cluster.refresh_from_db()
        assert cluster.updated_at == before


class TestVolumeFloorBands:
    """The re-grade gate compares the floor before and after an assignment, so
    the band boundaries have to be exactly where the floor changes."""

    @pytest.mark.parametrize(
        "traces,expected",
        [(0, "low"), (4, "low"), (5, "medium"), (24, "medium"), (25, "high")],
    )
    def test_bands(self, traces, expected):
        assert _volume_floor(traces) == expected

    def test_boundaries_coincide_with_retitle_points(self):
        """Both crossings must land on a `_RETITLE_AT` growth point, or the
        gate never notices them and severity stays stale forever."""
        from tracer.queries.scan_clustering import _RETITLE_AT

        crossings = [
            n for n in range(1, 300) if _volume_floor(n) != _volume_floor(n - 1)
        ]
        assert crossings == [5, 25]
        assert set(crossings).issubset(set(_RETITLE_AT))


@pytest.mark.django_db
class TestStatusIsEscalatingAndOtherwiseHuman:
    """Status is escalating by default; every other value belongs to a person.

    Automation briefly downgraded low-volume clusters to for_review. That is a
    triage state a user sets through PATCH /tracer/feed/issues/{cluster_id}/, so
    writing it by machine both invented a review nobody did and — because the
    guard only covered acknowledged/resolved — flipped a user's own for_review
    back to escalating the moment the cluster gained a fifth trace.
    """

    def test_a_new_cluster_is_escalating(self, project):
        """Through create_cluster, not the model default — the downgrade this
        replaces lived in create_cluster, so asserting the default would pass
        with the downgrade restored."""
        from tracer.queries.scan_clustering import create_cluster

        with _ch_stub(), \
             patch("tracer.queries.scan_clustering.ensure_centroid_table"), \
             _graded("medium"):
            cluster_id = create_cluster(str(project.id), _issue(project), _EMBEDDING)

        created = TraceErrorGroup.objects.get(
            cluster_id=cluster_id, project_id=project.id
        )
        assert created.status == FeedIssueStatus.ESCALATING

    @pytest.mark.parametrize(
        "human",
        [
            FeedIssueStatus.FOR_REVIEW,
            FeedIssueStatus.ACKNOWLEDGED,
            FeedIssueStatus.RESOLVED,
        ],
    )
    def test_assignment_never_rewrites_a_human_status(self, project, human):
        """Growing past any volume must not reopen or relabel a triaged cluster.

        Drives the real assignment path rather than asserting a function is
        absent: a rename would defeat a name check, and the point is that no
        code path writes status, whatever it is called.
        """
        from tracer.queries.scan_clustering import assign_to_cluster

        cluster = _cluster(project, traces=50, status=human)
        issue = _issue(project)

        with _ch_stub(), \
             patch("tracer.queries.scan_clustering.ensure_centroid_table"):
            assign_to_cluster(
                cluster.cluster_id, str(project.id), issue, _EMBEDDING
            )

        cluster.refresh_from_db()
        assert cluster.status == human, (
            f"assignment rewrote a status a person set ({human} -> {cluster.status})"
        )
