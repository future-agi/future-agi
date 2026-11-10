"""An export's status and download must not observe a lagging replica."""

import pytest
from django.db import router
from django.db.models.query import QuerySet

from model_hub.models.annotation_queues import AnnotationExportJob, AnnotationQueue
from model_hub.services.annotation_export_storage import download_url
from model_hub.tests.test_annotation_export_async import JOB_URL
from model_hub.tests.test_annotation_export_download import (
    completed_export as completed_export,
    storage as storage,
)
from tfc.routers import ReadReplicaRouter


@pytest.mark.django_db
@pytest.mark.parametrize(
    "opt_in,lagged_model",
    [
        (["AnnotationExportJob"], AnnotationExportJob),
        (["ALL_MODELS_USE_READ_REPLICA"], AnnotationExportJob),
        (["ALL_MODELS_USE_READ_REPLICA"], AnnotationQueue),
    ],
    ids=["job-opt-in", "all-models-job-lag", "all-models-queue-lag"],
)
@pytest.mark.parametrize(
    "action,job_status",
    [("poll", "pending"), ("poll", "succeeded"), ("download", "succeeded")],
)
def test_export_actions_read_primary_during_replica_lag(
    auth_client, completed_export, storage, monkeypatch, opt_in, lagged_model,
    action, job_status,
):
    job = completed_export
    job.status = job_status
    job.save(update_fields=["status"])
    path = (
        JOB_URL.format(queue_id=job.queue_id, job_id=job.pk)
        if action == "poll"
        else download_url(job)
    )
    reads = []

    class LaggedReplicaRouter(ReadReplicaRouter):
        def db_for_read(self, model, **hints):
            alias = super().db_for_read(model, **hints)
            if model in (AnnotationQueue, AnnotationExportJob):
                reads.append((model, alias))
            # All other tables have caught up. Only the selected table is stale.
            return alias if model is lagged_model else "default"

    lagged_router = LaggedReplicaRouter(opt_in=opt_in)
    monkeypatch.setattr("tfc.routers._replica_configured", lambda: True)
    monkeypatch.setattr(router, "routers", [lagged_router])
    fetch_all = QuerySet._fetch_all

    def fetch_with_replica_lag(queryset):
        # Model an empty replica at the ORM evaluation boundary, while using
        # real primary rows and the production router/authorization/view code.
        if queryset.db == "replica":
            queryset._result_cache = []
        else:
            fetch_all(queryset)

    monkeypatch.setattr(QuerySet, "_fetch_all", fetch_with_replica_lag)
    lagged_pk = job.pk if lagged_model is AnnotationExportJob else job.queue_id
    with pytest.raises(lagged_model.DoesNotExist):
        lagged_model.objects.get(pk=lagged_pk)
    reads.clear()

    response = auth_client.get(path)
    assert response.status_code == 200
    if action == "poll":
        result = response.data["result"]
        assert result["job_id"] == str(job.pk)
        assert result["status"] == job_status
        if job_status == "pending":
            assert result["file_url"] is None
        else:
            assert "/download/?token=" in result["file_url"]
        storage.get_object.assert_not_called()
    else:
        try:
            assert b"".join(response.streaming_content) == (
                b'[{"private":"synthetic-export"}]'
            )
        finally:
            response.close()
        storage.get_object.assert_called_once()
    assert {model for model, _ in reads} == {AnnotationQueue, AnnotationExportJob}
    assert all(alias == "default" for _, alias in reads)
    # The per-action pin must not leak into later requests.
    assert lagged_router.db_for_read(lagged_model) == "replica"
