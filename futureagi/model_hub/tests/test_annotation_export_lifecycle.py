"""Storage retention and worker redelivery regressions for queue exports."""

from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from model_hub.models.annotation_queues import AnnotationExportJob, QueueItem
from model_hub.tasks.annotation_export import (
    EXPORT_RETENTION_DAYS,
    _iter_item_chunks,
    cleanup_expired_export_jobs,
    export_annotation_queue_async,
)
from model_hub.tests.test_annotation_export_async import _make_job
from model_hub.tests.test_annotation_export_batch_cap import _build_dataset_queue
from model_hub.views.annotation_queues import queue_export_items_queryset
from tfc.utils.audit import mute_audit_signals


@pytest.mark.django_db
def test_export_pages_bound_sql_and_preserve_tied_item_order(organization, workspace, user):
    seed = _build_dataset_queue(organization=organization, workspace=workspace, user=user, n_rows=7)
    QueueItem.objects.filter(queue=seed["queue"]).update(order=0, created_at=timezone.now())
    with CaptureQueriesContext(connection) as queries:
        chunks = list(_iter_item_chunks(queue_export_items_queryset(seed["queue"]), 2))
    ids = [item.id for chunk in chunks for item in chunk]
    assert len(ids) == len(set(ids)) == 7
    assert ids == sorted(ids)
    reads = [q["sql"] for q in queries.captured_queries
             if 'FROM "model_hub_queueitem"' in q["sql"]]
    assert len(reads) == 4
    assert all("LIMIT 2" in query for query in reads)
    assert all("OFFSET" not in query for query in reads)


@pytest.mark.django_db
def test_successful_export_redelivery_does_not_rewrite_artifact(organization, workspace, user):
    seed = _build_dataset_queue(organization=organization, workspace=workspace, user=user, n_rows=1)
    job = _make_job(seed["queue"], organization, workspace, user)
    with mock.patch("model_hub.tasks.annotation_export._store_export_file", return_value=("https://storage.invalid/export.json", "export.json")) as store:
        result = export_annotation_queue_async._original_func(str(job.id))
        job.refresh_from_db()
        finished = job.finished_at
        repeated = export_annotation_queue_async._original_func(str(job.id))
    assert repeated == result
    store.assert_called_once()
    job.refresh_from_db()
    assert job.status == "succeeded"
    assert job.finished_at == finished


@pytest.mark.django_db
def test_superseded_attempt_cannot_overwrite_success(organization, workspace, user):
    seed = _build_dataset_queue(organization=organization, workspace=workspace, user=user, n_rows=1)
    job = _make_job(seed["queue"], organization, workspace, user)
    from model_hub.tasks.annotation_export import render_export

    def newer_attempt_finishes(current):
        rendered = render_export(current)
        AnnotationExportJob.objects.filter(pk=current.pk).update(
            status="succeeded", item_count=1, file_url="https://storage.invalid/newer.json",
            started_at=timezone.now(), finished_at=timezone.now(),
        )
        return rendered

    with mock.patch("model_hub.tasks.annotation_export.render_export", side_effect=newer_attempt_finishes):
        with mock.patch("model_hub.tasks.annotation_export._store_export_file") as store:
            result = export_annotation_queue_async._original_func(str(job.id))
    store.assert_not_called()
    assert result["file_url"] == "https://storage.invalid/newer.json"


@pytest.mark.django_db
@pytest.mark.parametrize("parent", ["queue", "workspace", "organization"])
def test_parent_hard_delete_preserves_key_until_artifact_cleanup(organization, workspace, user, parent):
    seed = _build_dataset_queue(organization=organization, workspace=workspace, user=user, n_rows=1)
    job = _make_job(seed["queue"], organization, workspace, user)
    with mock.patch("model_hub.tasks.annotation_export._store_export_file", return_value=("https://storage.invalid/export.json", "export.json")):
        export_annotation_queue_async._original_func(str(job.id))
    job.refresh_from_db()
    key = job.storage_key
    assert key == f"annotation-exports/{seed['queue'].pk}/{job.pk}.json"
    job.finished_at = timezone.now() - timedelta(days=EXPORT_RETENTION_DAYS + 1)
    job.save()
    target = {"queue": seed["queue"], "workspace": workspace, "organization": organization}[parent]
    # Match AnnotationQueueViewSet.hard_delete, bypassing BaseModel's soft delete.
    # Membership audit rows cannot reference an organization being removed.
    # Keep its unrelated signal out of this artifact-retention regression.
    with mute_audit_signals():
        models.Model.delete(target)
    surviving = AnnotationExportJob.no_workspace_objects.get(pk=job.pk)
    assert surviving.storage_key == key
    assert surviving.queue_id is None
    with mock.patch("tfc.utils.storage_client.get_storage_client") as storage:
        cleanup_expired_export_jobs._original_func()
    assert storage.return_value.remove_object.call_args.args[1] == key
    assert not AnnotationExportJob.no_workspace_objects.filter(pk=job.pk).exists()


@pytest.mark.django_db
def test_queue_deleted_during_render_does_not_upload(organization, workspace, user):
    seed = _build_dataset_queue(organization=organization, workspace=workspace, user=user, n_rows=1)
    job = _make_job(seed["queue"], organization, workspace, user)
    from model_hub.tasks.annotation_export import render_export

    def delete_after_render(current):
        rendered = render_export(current)
        models.Model.delete(seed["queue"])
        return rendered

    with mock.patch("model_hub.tasks.annotation_export.render_export", side_effect=delete_after_render):
        with mock.patch("model_hub.tasks.annotation_export._store_export_file") as store:
            with pytest.raises(ValueError, match="queue was deleted"):
                export_annotation_queue_async._original_func(str(job.id))
    store.assert_not_called()
    job.refresh_from_db()
    assert job.status == "failed"
    assert job.storage_key


@pytest.mark.django_db
def test_retention_migration_preserves_existing_object_keys(organization, workspace, user):
    from importlib import import_module
    from types import SimpleNamespace
    from django.apps import apps

    seed = _build_dataset_queue(organization=organization, workspace=workspace, user=user, n_rows=1)
    jobs = [_make_job(seed["queue"], organization, workspace, user, fmt) for fmt in ("csv", "json")]
    migration = import_module("model_hub.migrations.0124_annotation_export_retention")
    editor = SimpleNamespace(connection=connection)
    migration.preserve_export_storage_keys(apps, editor)
    migration.preserve_export_storage_keys(apps, editor)
    for job in jobs:
        job.refresh_from_db()
        assert job.storage_key == f"annotation-exports/{job.queue_id}/{job.pk}.{job.export_format}"
