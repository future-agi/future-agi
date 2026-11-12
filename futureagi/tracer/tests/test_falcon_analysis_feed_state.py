"""The feed consumes background results within the same project boundary."""

import uuid

import pytest

from tracer.models.trace_error_analysis import (
    ErrorClusterTraces,
    TraceErrorAnalysis,
    TraceErrorDetail,
)
from tracer.queries import deep_analysis_state, feed
from tracer.tests.test_error_analysis import make_feed_group
from tracer.tests.test_falcon_tool_isolation import other_project as other_project


@pytest.mark.django_db
def test_feed_uses_only_cluster_project_analysis(project, other_project):
    trace_id = str(uuid.uuid4())
    cluster = make_feed_group(project, "shared-cluster")
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)
    own = TraceErrorAnalysis.objects.create(trace_id=trace_id, project=project)
    TraceErrorDetail.objects.create(
        analysis=own, error_id="E001", category="Test", recommendation="Own fix"
    )
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project
    )
    TraceErrorDetail.objects.create(
        analysis=foreign,
        error_id="E001",
        category="Test",
        recommendation="Private foreign fix",
    )

    result = feed.get_deep_analysis(cluster.cluster_id, trace_id, [str(project.id)])

    assert result.status == "done"
    assert [item.description for item in result.recommendations] == ["Own fix"]


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["running", "failed"])
def test_feed_does_not_report_partial_falcon_scores_as_done(project, state):
    trace_id = str(uuid.uuid4())
    cluster = make_feed_group(project, "background-state")
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)
    TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=project, overall_score=2.0
    )
    if state == "running":
        deep_analysis_state.set_running(trace_id, project_id=str(project.id))
    else:
        deep_analysis_state.set_failed(trace_id, project_id=str(project.id))
    try:
        result = feed.get_deep_analysis(cluster.cluster_id, trace_id, [str(project.id)])
        assert result.status == state
        assert not result.recommendations
    finally:
        deep_analysis_state.clear(trace_id, project_id=str(project.id))
