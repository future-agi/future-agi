"""Feed keeps its pinned cluster-access and completed-row contracts."""

import uuid
from unittest.mock import Mock

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
def test_inaccessible_cluster_still_denies_get_and_dispatch(project, other_project):
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

    # Either project owns an analysis, but only `project` owns this cluster.
    outside = [str(other_project.pk)]
    assert feed.get_deep_analysis(cluster.cluster_id, trace_id, outside) is None
    assert feed.dispatch_deep_analysis(cluster.cluster_id, trace_id, outside) is None


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["running", "failed"])
def test_stored_feed_result_wins_without_cache_reads(project, state, monkeypatch):
    trace_id = str(uuid.uuid4())
    cluster = make_feed_group(project, "background-state")
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)
    TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=project, overall_score=2.0
    )
    if state == "running":
        deep_analysis_state.set_running(trace_id)
    else:
        deep_analysis_state.set_failed(trace_id)
    cache_read = Mock(side_effect=ConnectionError("cache offline"))
    monkeypatch.setattr(deep_analysis_state, "cache", Mock(wraps=deep_analysis_state.cache, get=cache_read))
    try:
        result = feed.get_deep_analysis(cluster.cluster_id, trace_id, [str(project.id)])
        assert result.status == "done"
        cache_read.assert_not_called()
        assert not result.recommendations
    finally:
        deep_analysis_state.clear(trace_id)


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_refused_preupgrade_batch_preserves_running_marker_and_click_deduplication(project, monkeypatch):
    from django.core.cache import cache

    from tracer.tests._falcon_hold import assert_activity_refused, no_background_work

    tid = str(uuid.uuid4())
    group = make_feed_group(project, "preupgrade")
    ErrorClusterTraces.objects.create(cluster=group, trace_id=tid)
    args = (group.cluster_id, tid, [str(project.pk)])
    old_key = f"deep_analysis:running:{tid}"
    cache.set(old_key, "1", 3600)
    enqueue = Mock()
    monkeypatch.setattr("tracer.tasks.run_deep_analysis_on_demand.delay", enqueue)
    try:
        with no_background_work(tid):
            assert_activity_refused(str(project.pk), [tid])
        assert cache.get(old_key) == "1"
        # A refused queued job cannot establish who owns the unversioned key.
        # The dev click stays deduplicated; it may run after the marker is gone.
        assert feed.get_deep_analysis(*args).status == "running"
        assert feed.dispatch_deep_analysis(*args).status == "running"
        enqueue.assert_not_called()
        assert cache.get(old_key) == "1"
        cache.delete(old_key)  # simulate expiry, not worker cleanup
        assert feed.get_deep_analysis(*args).status == "idle"
        assert feed.dispatch_deep_analysis(*args).status == "running"
        with no_background_work(tid):
            assert_activity_refused(str(project.pk), [tid])
        assert feed.dispatch_deep_analysis(*args).status == "running"
        enqueue.assert_called_once_with(tid, False)
        assert cache.get(old_key) == "1" and deep_analysis_state.is_running(tid)
    finally:
        deep_analysis_state.clear(tid)
