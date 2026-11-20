"""The background analysis queue must keep clustering inside its project."""

from unittest.mock import MagicMock

import pytest

from tracer.models.trace_error_analysis import (
    TraceErrorAnalysis,
    TraceErrorDetail,
    TraceErrorGroup,
)
from tracer.queries.error_clustering import ErrorClusteringDB
from tracer.tests.test_falcon_tool_isolation import other_project as other_project

pytestmark = pytest.mark.requires_ee


@pytest.mark.django_db
def test_cluster_centroid_collision_uses_requesting_project(
    project, trace, monkeypatch
):
    analysis = TraceErrorAnalysis.objects.create(project=project, trace=trace)
    detail = TraceErrorDetail.objects.create(
        analysis=analysis, error_id="E001", category="Test"
    )
    cluster = TraceErrorGroup.objects.create(
        project=project, cluster_id="shared-cluster", error_type="Test", error_ids=[]
    )
    vector_db = MagicMock()
    vector_db.execute_read.return_value = [([0.1, 0.2], 1)]
    monkeypatch.setattr(
        "tracer.queries.error_clustering.ClickHouseVectorDB", lambda: vector_db
    )
    clusterer = MagicMock()
    clusterer._normalize_category_family.return_value = "Test"
    clusterer._update_centroid_incremental.return_value = [0.2, 0.3]

    ErrorClusteringDB().add_to_existing_cluster(
        project_id=str(project.id),
        cluster_id=cluster.cluster_id,
        error_detail_pk=str(detail.id),
        vector=[0.3, 0.4],
        clusterer=clusterer,
    )

    query, params = vector_db.execute_read.call_args.args
    assert "project_id = %(project_id)s" in query
    assert params["project_id"] == str(project.id)
    assert vector_db.client.execute.call_args.args[1]["project_id"] == str(project.id)


@pytest.mark.django_db
def test_cluster_rejects_foreign_detail_metadata(
    project, other_project, trace, monkeypatch
):
    foreign = TraceErrorAnalysis.objects.create(
        project=other_project, trace_id=trace.id
    )
    detail = TraceErrorDetail.objects.create(
        analysis=foreign,
        error_id="E001",
        category="Private",
        description="Private foreign description",
    )
    vector_db = MagicMock()
    monkeypatch.setattr(
        "tracer.queries.error_clustering.ClickHouseVectorDB", lambda: vector_db
    )
    clusterer = MagicMock()
    clusterer._compute_centroid.return_value = [0.1, 0.2]

    result = ErrorClusteringDB().create_new_cluster(
        project_id=str(project.id),
        error_detail_pks=[str(detail.id)],
        vectors=[[0.1, 0.2]],
        family="Test",
        clusterer=clusterer,
    )

    assert result is None
    assert not TraceErrorGroup.objects.filter(project=project).exists()
    vector_db.client.execute.assert_not_called()
