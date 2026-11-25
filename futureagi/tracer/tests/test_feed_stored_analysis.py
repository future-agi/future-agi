"""The issue detail serves the analysis grouping stored for the issue.

Grouping stores ``{mechanism, fix_hypothesis, falsifier}`` on the issue's
state. ``description`` already carries the mechanism; the detail also serves
the suggested change and what would prove the mechanism wrong, so the Root
cause tab can show them without running the in-depth analysis. Eval and older
scan issues have no state and serve null.
"""

from unittest.mock import patch

import pytest

from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup
from tracer.models.trace_grouping import TraceGroupingIssueState, TraceGroupingScope
from tracer.queries import feed
from tracer.serializers.feed import FeedDetailCoreSerializer

pytestmark = pytest.mark.django_db

MECHANISM = {
    "title": "Agent refunds the wrong amount for two-item orders",
    "mechanism": "The agent refunds the first item only.",
    "fix_hypothesis": "Sum every line item before calling refund_order.",
    "falsifier": "Two-item refunds stay wrong after the agent reads both items.",
}


@pytest.fixture(autouse=True)
def _no_clickhouse():
    with (
        patch.object(feed, "is_voice_project", return_value=False),
        patch.object(feed, "_fetch_trends_batch", return_value={}),
        patch.object(feed, "_fetch_users_affected_batch", return_value={}),
        patch.object(feed, "_fetch_sessions_batch", return_value={}),
        patch.object(feed, "_fetch_latest_trace_id_batch", return_value={}),
    ):
        yield


def _issue(project, cluster_id, **fields):
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=cluster_id,
        issue_group="Investigation findings",
        error_count=1,
        total_events=1,
        unique_traces=1,
        **fields,
    )


def _detail(cluster):
    detail = feed.get_cluster_detail(cluster.cluster_id, [str(cluster.project_id)])
    return FeedDetailCoreSerializer(detail).data


def test_grouped_issue_serves_its_stored_suggested_change_and_falsifier(
    observe_project,
):
    cluster = _issue(
        observe_project,
        "S-STORED01",
        source=ClusterSource.SCANNER,
        error_type=MECHANISM["mechanism"],
        title=MECHANISM["title"],
        combined_description=MECHANISM["mechanism"],
    )
    TraceGroupingIssueState.objects.create(
        scope=TraceGroupingScope.objects.create(
            project=observe_project,
            organization_id=observe_project.organization_id,
            workspace_id=observe_project.workspace_id,
        ),
        cluster=cluster,
        mechanism=MECHANISM,
    )

    data = _detail(cluster)

    assert data["description"] == MECHANISM["mechanism"]
    assert data["fix_hypothesis"] == MECHANISM["fix_hypothesis"]
    assert data["falsifier"] == MECHANISM["falsifier"]


@pytest.mark.parametrize(
    "source, cluster_id",
    [(ClusterSource.EVAL, "E-NOSTATE1"), (ClusterSource.SCANNER, "FT-NOSTATE1")],
)
def test_issue_without_grouping_state_serves_null_analysis(
    observe_project, source, cluster_id
):
    cluster = _issue(
        observe_project,
        cluster_id,
        source=source,
        error_type="Refund amount",
        title="Refund amount is wrong",
        combined_description="Eval reason text.",
    )

    data = _detail(cluster)

    assert data["description"] == "Eval reason text."
    assert data["fix_hypothesis"] is None
    assert data["falsifier"] is None
