"""Background export of oversize annotation queues.

The synchronous download and the Temporal activity must agree byte for byte:
the whole point of the async path is that a large queue gets the same file the
small one gets, instead of a 413. These tests pin that equivalence, the
coverage of every item above the cap, and the failure being readable.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from unittest import mock

import pytest
from django.test.utils import override_settings
from rest_framework import status
from rest_framework.utils.encoders import JSONEncoder

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from model_hub.models.annotation_queues import AnnotationExportJob, QueueItem
from model_hub.models.choices import (
    AnnotationExportJobStatus,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.tasks.annotation_export import (
    EXPORT_RETENTION_DAYS,
    cleanup_expired_export_jobs,
    export_annotation_queue_async,
    render_export,
)
from model_hub.utils.annotation_queue_helpers import CollectorSourceCache

# The activity is invoked through ``_original_func`` the way the other activity
# tests in this repo do it: the decorator wraps the call in
# ``close_old_connections()``, which would drop the connection this test's
# transaction is running on.
from model_hub.tests.test_annotation_export_batch_cap import (
    EXPORT_URL,
    _build_call_execution_queue,
    _build_ch_source_queue,
    _build_dataset_queue,
    _build_prototype_run_queue,
    _unwrap,
)

JOB_URL = "/model-hub/annotation-queues/{queue_id}/export-jobs/{job_id}/"


def _render_text(job):
    """Render a job and return (text, item_count, size), closing the spool."""
    fileobj, item_count, size = render_export(job)
    try:
        return fileobj.read().decode("utf-8"), item_count, size
    finally:
        fileobj.close()


def _make_job(queue, organization, workspace, user, export_format="json"):
    return AnnotationExportJob.objects.create(
        queue=queue,
        organization=organization,
        workspace=workspace,
        created_by=user,
        export_format=export_format,
    )


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_async_json_payload_matches_sync_download(
    auth_client, organization, workspace, user
):
    """Same queue, both paths, identical rows."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=5
    )
    sync_resp = auth_client.get(
        EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=json"
    )
    assert sync_resp.status_code == status.HTTP_200_OK
    sync_rows = _unwrap(sync_resp.data)

    job = _make_job(seed["queue"], organization, workspace, user, "json")
    text, item_count, size = _render_text(job)
    async_rows = json.loads(text)
    # The reported size must match what was actually written, since it is what
    # the upload declares as content length.
    assert size == len(text.encode("utf-8"))

    assert item_count == 5
    assert len(async_rows) == len(sync_rows)
    # Encode the sync rows with the same DRF encoder the API response goes
    # through, so this compares what a client actually receives on each path
    # rather than Python object identity (str(datetime) is not the ISO form
    # DRF emits, and would fail here for a difference no user ever sees).
    assert async_rows == json.loads(json.dumps(sync_rows, cls=JSONEncoder))


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_async_csv_payload_matches_sync_download(
    auth_client, organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=5
    )
    sync_resp = auth_client.get(
        EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=csv"
    )
    assert sync_resp.status_code == status.HTTP_200_OK
    sync_csv = sync_resp.content.decode("utf-8")

    job = _make_job(seed["queue"], organization, workspace, user, "csv")
    text, item_count, size = _render_text(job)

    assert item_count == 5
    assert text == sync_csv
    assert size == len(text.encode("utf-8"))


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=2)
def test_async_export_covers_every_item_above_the_cap(organization, workspace, user):
    """The cap bounds the request, not the export: all rows must come out."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=7
    )
    job = _make_job(seed["queue"], organization, workspace, user, "csv")

    with mock.patch("model_hub.tasks.annotation_export.EXPORT_CHUNK_SIZE", 2):
        text, item_count, _size = _render_text(job)

    assert item_count == 7
    reader = list(csv.reader(io.StringIO(text)))
    header, rows = reader[0], reader[1:]
    assert header[0] == "item_id"
    # Chunking must not drop or duplicate an item, and must not re-emit the
    # header per chunk.
    assert len({row[0] for row in rows}) == 7


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_activity_stores_file_and_completes_job(organization, workspace, user):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    job = _make_job(seed["queue"], organization, workspace, user, "csv")

    # Read the payload inside the call: the activity closes the spool in a
    # finally, so it is not readable afterwards (which is the point — the temp
    # file must not be left open).
    captured = {}

    def _capture(_job, fileobj, size):
        fileobj.seek(0)
        captured["bytes"] = fileobj.read()
        captured["size"] = size
        return ("http://storage/queue.csv", "queue_x_annotations.csv")

    with mock.patch(
        "model_hub.tasks.annotation_export._store_export_file",
        side_effect=_capture,
    ):
        result = export_annotation_queue_async._original_func(str(job.id))

    job.refresh_from_db()
    assert job.status == AnnotationExportJobStatus.SUCCEEDED.value
    assert job.item_count == 3
    assert job.file_url == "http://storage/queue.csv"
    assert job.file_name == "queue_x_annotations.csv"
    assert job.error is None
    assert job.started_at is not None and job.finished_at is not None
    assert result["item_count"] == 3
    # What went to storage is the rendered export, not a placeholder, and the
    # declared length matches the bytes handed over.
    assert captured["bytes"].decode("utf-8").startswith("item_id,")
    assert captured["size"] == len(captured["bytes"])


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_activity_failure_records_real_error_and_reraises(
    organization, workspace, user
):
    """A failed export must say why, instead of a generic download failure."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    job = _make_job(seed["queue"], organization, workspace, user, "json")

    with mock.patch(
        "model_hub.tasks.annotation_export._store_export_file",
        side_effect=RuntimeError("bucket is gone"),
    ):
        with pytest.raises(RuntimeError):
            export_annotation_queue_async._original_func(str(job.id))

    job.refresh_from_db()
    assert job.status == AnnotationExportJobStatus.FAILED.value
    assert "bucket is gone" in job.error
    assert job.file_url is None
    assert job.finished_at is not None


@pytest.mark.django_db
def test_export_job_endpoint_reports_progress_then_link(
    auth_client, organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=2
    )
    job = _make_job(seed["queue"], organization, workspace, user, "csv")

    url = JOB_URL.format(queue_id=seed["queue"].id, job_id=job.id)
    pending = _unwrap(auth_client.get(url).data)
    assert pending["status"] == AnnotationExportJobStatus.PENDING.value
    assert pending["file_url"] is None

    job.status = AnnotationExportJobStatus.SUCCEEDED.value
    job.file_url = "http://storage/queue.csv"
    job.file_name = "queue_x_annotations.csv"
    job.item_count = 2
    job.save()

    ready = _unwrap(auth_client.get(url).data)
    assert ready["status"] == AnnotationExportJobStatus.SUCCEEDED.value
    assert ready["file_url"] == "http://storage/queue.csv"
    assert ready["item_count"] == 2


@pytest.mark.django_db
def test_export_job_endpoint_404s_for_another_queue(
    auth_client, organization, workspace, user
):
    """A job id is only readable through the queue that owns it."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    other = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _make_job(seed["queue"], organization, workspace, user)

    resp = auth_client.get(JOB_URL.format(queue_id=other["queue"].id, job_id=job.id))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_async_export_honours_the_status_filter(organization, workspace, user):
    """The filter the user asked for has to survive the trip to the worker."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=6
    )
    completed = list(QueueItem.objects.filter(queue=seed["queue"])[:2])
    for item in completed:
        item.status = QueueItemStatus.COMPLETED.value
        item.save(update_fields=["status"])

    job = _make_job(seed["queue"], organization, workspace, user, "json")
    job.status_filter = QueueItemStatus.COMPLETED.value
    job.save(update_fields=["status_filter"])

    text, item_count, _size = _render_text(job)
    rows = json.loads(text)

    assert item_count == 2
    assert {row["item_id"] for row in rows} == {str(i.id) for i in completed}
    assert all(row["status"] == QueueItemStatus.COMPLETED.value for row in rows)


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=2)
def test_status_all_is_recorded_as_no_filter(
    auth_client, organization, workspace, user
):
    """`status=all` must not be stored as a literal filter that matches nothing."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=5
    )
    with mock.patch(
        "tfc.temporal.drop_in.runner.start_activity_sync", return_value="wf-all"
    ):
        resp = auth_client.get(
            EXPORT_URL.format(queue_id=seed["queue"].id)
            + "?export_format=json&status=all"
        )

    assert resp.status_code == status.HTTP_202_ACCEPTED
    job = AnnotationExportJob.objects.get(pk=resp.data["job_id"])
    assert job.status_filter is None

    _text, item_count, _size = _render_text(job)
    assert item_count == 5


@pytest.mark.django_db
def test_export_job_endpoint_404s_on_a_malformed_job_id(
    auth_client, organization, workspace, user
):
    """A bad id is a 404, not a 500 from the UUID cast."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    resp = auth_client.get(
        JOB_URL.format(queue_id=seed["queue"].id, job_id="not-a-uuid")
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_export_rolls_over_to_disk_without_changing_the_bytes(
    organization, workspace, user
):
    """Past the spool limit the render goes to a temp file, byte-identical.

    This is the difference between moving the timeout and moving the problem:
    holding the whole document in memory would OOM the worker on exactly the
    queues this path exists for.
    """
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=4
    )
    job = _make_job(seed["queue"], organization, workspace, user, "csv")

    in_memory, _count, size_mem = _render_text(job)

    with mock.patch("model_hub.tasks.annotation_export.EXPORT_SPOOL_MAX_BYTES", 1):
        fileobj, _count2, size_disk = render_export(job)
        try:
            # `_rolled` is SpooledTemporaryFile's own flag for "no longer in
            # memory"; asserting it is what separates this from a test that
            # would pass either way.
            assert fileobj._rolled is True
            on_disk = fileobj.read().decode("utf-8")
        finally:
            fileobj.close()

    assert on_disk == in_memory
    assert size_disk == size_mem


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_items_without_annotations_still_get_a_csv_line(organization, workspace, user):
    """Every item is accounted for, annotated or not."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    job = _make_job(seed["queue"], organization, workspace, user, "csv")

    text, item_count, _size = _render_text(job)
    rows = list(csv.reader(io.StringIO(text)))[1:]

    assert item_count == 3
    assert len(rows) == 3
    expected = {str(i.id) for i in QueueItem.objects.filter(queue=seed["queue"])}
    assert {row[0] for row in rows} == expected
    # Annotation columns blank, item columns filled.
    assert all(row[11:] == ["", "", "", "", "", "", ""] for row in rows)


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
@pytest.mark.parametrize("export_format", ["json", "csv"])
def test_async_matches_sync_across_chunk_boundaries(
    auth_client, organization, workspace, user, export_format
):
    """Paridade with the chunk size forced below the item count.

    The single-chunk parity tests never exercise the seam between chunks, which
    is where the JSON branch's comma and the CSV branch's header placement can
    go wrong. Seven items at chunk size two crosses that seam four times.
    """
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=7
    )
    sync_resp = auth_client.get(
        EXPORT_URL.format(queue_id=seed["queue"].id) + f"?export_format={export_format}"
    )
    assert sync_resp.status_code == status.HTTP_200_OK

    job = _make_job(seed["queue"], organization, workspace, user, export_format)
    with mock.patch("model_hub.tasks.annotation_export.EXPORT_CHUNK_SIZE", 2):
        text, item_count, _size = _render_text(job)

    assert item_count == 7
    if export_format == "csv":
        assert text == sync_resp.content.decode("utf-8")
    else:
        assert json.loads(text) == json.loads(
            json.dumps(_unwrap(sync_resp.data), cls=JSONEncoder)
        )


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_failure_leaves_job_running_while_temporal_will_retry(
    organization, workspace, user
):
    """A retryable attempt must not look final to the polling client.

    Temporal retries this activity up to 3 times. Marking the row failed on the
    first attempt would make the client give up during the backoff window on an
    export that is still going to succeed.
    """
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=2
    )
    job = _make_job(seed["queue"], organization, workspace, user, "json")

    with mock.patch(
        "model_hub.tasks.annotation_export._store_export_file",
        side_effect=RuntimeError("minio hiccup"),
    ):
        with mock.patch(
            "model_hub.tasks.annotation_export._temporal_will_retry",
            return_value=True,
        ):
            with pytest.raises(RuntimeError):
                export_annotation_queue_async._original_func(str(job.id))

    job.refresh_from_db()
    assert job.status == AnnotationExportJobStatus.RUNNING.value
    assert job.error is None
    assert job.finished_at is None


def test_direct_invocation_is_treated_as_the_last_attempt():
    """Outside an activity context nothing retries, so failures are final."""
    from model_hub.tasks.annotation_export import _temporal_will_retry

    assert _temporal_will_retry() is False


# ---------------------------------------------------------------------------
# Source-type coverage above the cap.
#
# The parity tests above all seed dataset rows. The export has to hold for every
# source type issue #2311 lists, and each resolves differently: call executions
# are the widest rows (prefetched transcripts, per-item eval metrics), prototype
# runs are a plain PG read, and the tracer sources (trace / observation_span /
# trace_session) resolve CH-only. A regression that broke rendering for, say,
# call executions would pass every dataset-row test, so each type is pinned here
# on the same async render path.
# ---------------------------------------------------------------------------


def _transcript_queries(captured):
    """Captured SQL statements that read the call-transcript table."""
    return [
        q for q in captured.captured_queries if "simulate_call_transcript" in q["sql"]
    ]


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_async_call_execution_matches_sync_download(
    auth_client, organization, workspace, user
):
    """Call-execution rows come out of the worker identical to the sync download."""
    seed = _build_call_execution_queue(
        organization=organization, workspace=workspace, user=user, n_items=5
    )
    sync_resp = auth_client.get(
        EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=json"
    )
    assert sync_resp.status_code == status.HTTP_200_OK, sync_resp.data
    sync_rows = _unwrap(sync_resp.data)

    job = _make_job(seed["queue"], organization, workspace, user, "json")
    text, item_count, _size = _render_text(job)

    assert item_count == 5
    assert json.loads(text) == json.loads(json.dumps(sync_rows, cls=JSONEncoder))
    # Every item resolved as a call execution, not a "could not resolve" sentinel.
    assert all(row["source"]["type"] == "call_execution" for row in json.loads(text))


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_async_call_execution_prefetches_transcripts_no_n_plus_one(
    organization, workspace, user
):
    """The transcript read stays batched over the chunk, not one query per call.

    ``queue_export_items_queryset`` carries the ``call_execution__transcripts``
    prefetch; the render reads each call's transcripts off it. Drop the prefetch
    and this becomes one transcript query per item — the N+1 the async path exists
    to avoid on the widest source rows.
    """
    seed = _build_call_execution_queue(
        organization=organization, workspace=workspace, user=user, n_items=6
    )
    job = _make_job(seed["queue"], organization, workspace, user, "json")

    with CaptureQueriesContext(connection) as cap:
        _text, item_count, _size = _render_text(job)

    assert item_count == 6
    # One batched prefetch for the whole queue, never 6.
    assert len(_transcript_queries(cap)) <= 1, [
        q["sql"] for q in _transcript_queries(cap)
    ]


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
def test_async_prototype_run_matches_sync_download(
    auth_client, organization, workspace, user
):
    """Prototype-run rows come out of the worker identical to the sync download."""
    seed = _build_prototype_run_queue(
        organization=organization, workspace=workspace, user=user, n_items=4
    )
    sync_resp = auth_client.get(
        EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=json"
    )
    assert sync_resp.status_code == status.HTTP_200_OK, sync_resp.data
    sync_rows = _unwrap(sync_resp.data)

    job = _make_job(seed["queue"], organization, workspace, user, "json")
    text, item_count, _size = _render_text(job)

    assert item_count == 4
    assert json.loads(text) == json.loads(json.dumps(sync_rows, cls=JSONEncoder))
    assert all(row["source"]["type"] == "prototype_run" for row in json.loads(text))


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=50)
@pytest.mark.parametrize(
    "source_type",
    [
        QueueItemSourceType.TRACE.value,
        QueueItemSourceType.OBSERVATION_SPAN.value,
        QueueItemSourceType.TRACE_SESSION.value,
    ],
)
def test_async_ch_source_matches_sync_download(
    auth_client, organization, workspace, user, source_type
):
    """Each ClickHouse-native source renders on the worker the same as sync.

    Both paths resolve tracer sources through ``CollectorSourceCache.for_items``;
    patching it with a pre-populated cache lets the sync download and the async
    render be compared byte for byte without a live ClickHouse, and pins that the
    worker handles trace / span / session content, not just dataset rows.
    """
    seed = _build_ch_source_queue(
        organization=organization,
        workspace=workspace,
        user=user,
        source_type=source_type,
        n_items=3,
    )

    with mock.patch.object(
        CollectorSourceCache, "for_items", return_value=seed["cache"]
    ):
        sync_resp = auth_client.get(
            EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=json"
        )
        assert sync_resp.status_code == status.HTTP_200_OK, sync_resp.data
        sync_rows = _unwrap(sync_resp.data)

        job = _make_job(seed["queue"], organization, workspace, user, "json")
        text, item_count, _size = _render_text(job)

    assert item_count == 3
    assert json.loads(text) == json.loads(json.dumps(sync_rows, cls=JSONEncoder))


# ---------------------------------------------------------------------------
# Retention: a finished export leaves a DB row and a stored object, and nothing
# in the write path reclaims either. cleanup_expired_export_jobs sweeps the
# terminal jobs past the retention window.
# ---------------------------------------------------------------------------


def _finished_job(queue, organization, workspace, user, *, status_value, age_days):
    """An export job in a terminal state that finished ``age_days`` days ago."""
    job = _make_job(queue, organization, workspace, user, "json")
    job.status = status_value
    job.file_url = f"http://storage/{job.id}.json"
    job.finished_at = timezone.now() - timedelta(days=age_days)
    job.save(update_fields=["status", "file_url", "finished_at"])
    return job


@pytest.mark.django_db
def test_cleanup_soft_deletes_terminal_jobs_past_retention(
    organization, workspace, user
):
    """Only succeeded/failed jobs past the cutoff are reclaimed; in-flight and
    recent ones are left alone."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    queue = seed["queue"]
    old = EXPORT_RETENTION_DAYS + 1

    stale_ok = _finished_job(
        queue, organization, workspace, user,
        status_value=AnnotationExportJobStatus.SUCCEEDED.value, age_days=old,
    )
    stale_failed = _finished_job(
        queue, organization, workspace, user,
        status_value=AnnotationExportJobStatus.FAILED.value, age_days=old,
    )
    recent = _finished_job(
        queue, organization, workspace, user,
        status_value=AnnotationExportJobStatus.SUCCEEDED.value, age_days=1,
    )
    # A pending job carries no finished_at, so it can't be past any cutoff.
    pending = _make_job(queue, organization, workspace, user, "json")

    with mock.patch(
        "model_hub.tasks.annotation_export._delete_export_object"
    ) as delete_obj:
        result = cleanup_expired_export_jobs._original_func()

    assert result == {"deleted": 2}
    swept = {c.args[0].id for c in delete_obj.call_args_list}
    assert swept == {stale_ok.id, stale_failed.id}

    live = set(AnnotationExportJob.objects.values_list("id", flat=True))
    assert live == {recent.id, pending.id}
    # The reclaimed rows are soft-deleted, not gone.
    assert AnnotationExportJob.all_objects.get(pk=stale_ok.id).deleted is True
    assert AnnotationExportJob.all_objects.get(pk=stale_failed.id).deleted is True


@pytest.mark.django_db
def test_cleanup_continues_past_a_storage_failure(organization, workspace, user):
    """One object-store failure must not abort the whole sweep."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    queue = seed["queue"]
    old = EXPORT_RETENTION_DAYS + 5
    for _ in range(3):
        _finished_job(
            queue, organization, workspace, user,
            status_value=AnnotationExportJobStatus.SUCCEEDED.value, age_days=old,
        )

    calls = {"n": 0}

    def _flaky(job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("minio down")

    with mock.patch(
        "model_hub.tasks.annotation_export._delete_export_object", side_effect=_flaky
    ):
        result = cleanup_expired_export_jobs._original_func()

    # The failing job is left for the next sweep; the other two are reclaimed.
    assert calls["n"] == 3
    assert result == {"deleted": 2}
    assert AnnotationExportJob.objects.count() == 1


@pytest.mark.django_db
def test_cleanup_deletes_the_stored_object_for_the_reclaimed_job(
    organization, workspace, user
):
    """The stored file is removed with the deterministic key the export wrote."""
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    job = _finished_job(
        seed["queue"], organization, workspace, user,
        status_value=AnnotationExportJobStatus.SUCCEEDED.value,
        age_days=EXPORT_RETENTION_DAYS + 1,
    )

    client = mock.MagicMock()
    with mock.patch(
        "tfc.utils.storage_client.get_storage_client", return_value=client
    ):
        cleanup_expired_export_jobs._original_func()

    bucket, key = client.remove_object.call_args.args
    assert key == f"annotation-exports/{job.queue_id}/{job.id}.json"
