"""Storage retention and worker redelivery regressions for queue exports."""

import json
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


@pytest.mark.django_db(transaction=True)
def test_export_pages_bound_sql_and_preserve_tied_item_order(
    organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=7
    )
    QueueItem.objects.filter(queue=seed["queue"]).update(
        order=0, created_at=timezone.now()
    )
    with CaptureQueriesContext(connection) as queries:
        chunks = list(_iter_item_chunks(queue_export_items_queryset(seed["queue"]), 2))
    ids = [item.id for chunk in chunks for item in chunk]
    assert len(ids) == len(set(ids)) == 7
    assert ids == sorted(ids)
    reads = [
        q["sql"]
        for q in queries.captured_queries
        if 'FROM "model_hub_queueitem"' in q["sql"]
    ]
    assert len(reads) == 4
    assert all("LIMIT 2" in query for query in reads)
    assert all("OFFSET" not in query for query in reads)


@pytest.mark.django_db(transaction=True)
def test_successful_export_redelivery_does_not_rewrite_artifact(
    organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _make_job(seed["queue"], organization, workspace, user)
    with mock.patch(
        "model_hub.tasks.annotation_export._store_export_file",
        return_value=("https://storage.invalid/export.json", "export.json"),
    ) as store:
        result = export_annotation_queue_async._original_func(str(job.id))
        job.refresh_from_db()
        finished = job.finished_at
        repeated = export_annotation_queue_async._original_func(str(job.id))
    assert repeated == result
    store.assert_called_once()
    job.refresh_from_db()
    assert job.status == "succeeded"
    assert job.finished_at == finished


@pytest.mark.django_db(transaction=True)
def test_superseded_attempt_cannot_overwrite_success(organization, workspace, user):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _make_job(seed["queue"], organization, workspace, user)
    from model_hub.tasks.annotation_export import render_export

    def newer_attempt_finishes(current):
        rendered = render_export(current)
        AnnotationExportJob.objects.filter(pk=current.pk).update(
            status="succeeded",
            item_count=1,
            file_url="https://storage.invalid/newer.json",
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        return rendered

    with mock.patch(
        "model_hub.tasks.annotation_export.render_export",
        side_effect=newer_attempt_finishes,
    ):
        with mock.patch(
            "model_hub.tasks.annotation_export._store_export_file"
        ) as store:
            result = export_annotation_queue_async._original_func(str(job.id))
    store.assert_not_called()
    assert result["file_url"] == "https://storage.invalid/newer.json"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("parent", ["queue", "workspace", "organization"])
def test_parent_hard_delete_preserves_key_until_artifact_cleanup(
    organization, workspace, user, parent
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _make_job(seed["queue"], organization, workspace, user)
    with mock.patch(
        "model_hub.tasks.annotation_export._store_export_file",
        return_value=("https://storage.invalid/export.json", "export.json"),
    ):
        export_annotation_queue_async._original_func(str(job.id))
    job.refresh_from_db()
    key = job.storage_key
    assert key == f"annotation-exports/{seed['queue'].pk}/{job.pk}.json"
    job.finished_at = timezone.now() - timedelta(days=EXPORT_RETENTION_DAYS + 1)
    job.save()
    target = {
        "queue": seed["queue"],
        "workspace": workspace,
        "organization": organization,
    }[parent]
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


@pytest.mark.django_db(transaction=True)
def test_queue_deleted_during_render_does_not_upload(organization, workspace, user):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _make_job(seed["queue"], organization, workspace, user)
    from model_hub.tasks.annotation_export import render_export

    def delete_after_render(current):
        rendered = render_export(current)
        models.Model.delete(seed["queue"])
        return rendered

    with mock.patch(
        "model_hub.tasks.annotation_export.render_export",
        side_effect=delete_after_render,
    ):
        with mock.patch(
            "model_hub.tasks.annotation_export._store_export_file"
        ) as store:
            with pytest.raises(ValueError, match="queue was deleted"):
                export_annotation_queue_async._original_func(str(job.id))
    store.assert_not_called()
    job.refresh_from_db()
    assert job.status == "failed"
    assert job.storage_key


@pytest.mark.django_db(transaction=True)
def test_concurrent_reorder_keeps_export_snapshot(organization, workspace, user):
    from model_hub.tasks.annotation_export import render_export
    from model_hub.views.annotation_queues import build_queue_export_rows

    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=5
    )
    queue = seed["queue"]
    expected = list(queue_export_items_queryset(queue).values_list("pk", flat=True))
    job = _make_job(queue, organization, workspace, user)
    other = connection.copy(alias="export_concurrent_edit")
    calls = 0

    def edit_during_render(current_queue, chunk):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Commit from another session, moving one read item forward and an
            # unread item behind the page boundary. Neither may be repeated/lost.
            with other.cursor() as cursor:
                cursor.execute(
                    'UPDATE model_hub_queueitem SET "order" = 1000 WHERE id = %s',
                    [expected[0]],
                )
                cursor.execute(
                    'UPDATE model_hub_queueitem SET "order" = -1 WHERE id = %s',
                    [expected[-1]],
                )
        return build_queue_export_rows(current_queue, chunk)

    try:
        with (
            mock.patch("model_hub.tasks.annotation_export.EXPORT_CHUNK_SIZE", 2),
            mock.patch(
                "model_hub.views.annotation_queues.build_queue_export_rows",
                side_effect=edit_during_render,
            ),
        ):
            spool, count, _ = render_export(job)
        try:
            exported = json.load(spool)
        finally:
            spool.close()
    finally:
        other.close()
    assert count == 5
    assert [row["item_id"] for row in exported] == [str(pk) for pk in expected]
    assert (
        list(queue_export_items_queryset(queue).values_list("pk", flat=True))
        != expected
    )


@pytest.mark.django_db(transaction=True)
def test_render_snapshot_pins_router_reads_to_primary(organization, workspace, user):
    from model_hub.tasks.annotation_export import render_export
    from model_hub.views.annotation_queues import build_queue_export_rows
    from tfc.routers import ReadReplicaRouter

    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    job = _make_job(seed["queue"], organization, workspace, user)
    router = ReadReplicaRouter(opt_in=["ALL_MODELS_USE_READ_REPLICA"])
    checked = []

    def check_primary(current_queue, chunk):
        assert router.db_for_read(QueueItem) == "default"
        checked.append(len(chunk))
        return build_queue_export_rows(current_queue, chunk)

    with (
        mock.patch("tfc.routers._replica_configured", return_value=True),
        mock.patch(
            "model_hub.views.annotation_queues.build_queue_export_rows",
            side_effect=check_primary,
        ),
    ):
        assert router.db_for_read(QueueItem) == "replica"
        spool, count, _ = render_export(job)
        spool.close()
        assert router.db_for_read(QueueItem) == "replica"
    assert count == sum(checked) == 3


@pytest.mark.django_db(transaction=True)
def test_expired_attempt_cannot_claim_after_newer_attempt(
    organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _make_job(seed["queue"], organization, workspace, user)
    job.status = "running"
    job.activity_execution = "workflow-run/activity"
    job.activity_attempt = 2
    job.started_at = timezone.now()
    job.save()
    started = job.started_at
    with (
        mock.patch(
            "model_hub.tasks.annotation_export._temporal_execution",
            return_value=(job.activity_execution, 1),
        ),
        mock.patch("model_hub.tasks.annotation_export.render_export") as render,
    ):
        with pytest.raises(RuntimeError, match="superseded"):
            export_annotation_queue_async._original_func(str(job.id))
    render.assert_not_called()
    job.refresh_from_db()
    assert (job.activity_attempt, job.started_at, job.status) == (2, started, "running")


@pytest.mark.django_db(transaction=True)
def test_superseded_unfinished_attempt_raises_instead_of_returning_success(
    organization, workspace, user
):
    from model_hub.tasks.annotation_export import render_export

    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _make_job(seed["queue"], organization, workspace, user)

    def newer_attempt_claims(current):
        rendered = render_export(current)
        AnnotationExportJob.objects.filter(pk=current.pk).update(
            activity_attempt=2, started_at=timezone.now()
        )
        return rendered

    with (
        mock.patch(
            "model_hub.tasks.annotation_export._temporal_execution",
            return_value=("run/activity", 1),
        ),
        mock.patch(
            "model_hub.tasks.annotation_export.render_export",
            side_effect=newer_attempt_claims,
        ),
        mock.patch("model_hub.tasks.annotation_export._store_export_file") as store,
    ):
        with pytest.raises(RuntimeError, match="superseded"):
            export_annotation_queue_async._original_func(str(job.id))
    store.assert_not_called()
    job.refresh_from_db()
    assert job.status == "running"
    assert job.activity_attempt == 2
    assert job.error is None
