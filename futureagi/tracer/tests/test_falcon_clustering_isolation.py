"""A refused batch never enters the existing clustering/centroid pipeline."""

import pytest

from tracer.models.trace_error_analysis import (
    TraceErrorAnalysis,
    TraceErrorDetail,
    TraceErrorGroup,
)
from tracer.tests._falcon_hold import assert_activity_refused, no_background_work
from tracer.tests.test_falcon_tool_isolation import other_project as other_project

pytestmark = pytest.mark.requires_ee


@pytest.mark.django_db(transaction=True)
def test_refused_batch_does_not_touch_existing_cluster(project, trace):
    analysis = TraceErrorAnalysis.objects.create(project=project, trace=trace)
    TraceErrorDetail.objects.create(analysis=analysis, error_id="E001", category="Test")
    cluster = TraceErrorGroup.objects.create(
        project=project, cluster_id="shared-cluster", error_type="Test", error_ids=[]
    )
    rows = TraceErrorGroup.objects.filter(pk=cluster.pk)
    before = rows.values().get()
    with no_background_work(str(trace.pk)):
        assert_activity_refused(str(project.pk), [str(trace.pk)])
    assert rows.values().get() == before


@pytest.mark.django_db(transaction=True)
def test_refused_batch_creates_no_cluster_from_foreign_detail(
    project, other_project, trace
):
    foreign = TraceErrorAnalysis.objects.create(
        project=other_project, trace_id=trace.id
    )
    TraceErrorDetail.objects.create(
        analysis=foreign,
        error_id="E001",
        category="Private",
        description="Other project",
    )
    with no_background_work(str(trace.pk)):
        assert_activity_refused(str(project.pk), [str(trace.pk)])
    assert not TraceErrorGroup.objects.filter(project=project).exists()
