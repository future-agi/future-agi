"""Export batching + size-cap coverage for annotation queues.

Regression guard for the export endpoint that timed out at the gateway: the
dataset-row cell read must be batched (one ``row_id__in`` query, not a per-item
N+1), and an oversize queue must be rejected up front instead of materializing
and resolving the whole queue synchronously.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    FULL_ACCESS_QUEUE_ROLES,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    DataTypeChoices,
    DatasetSourceChoices,
    QueueItemSourceType,
    QueueItemStatus,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils.annotation_queue_helpers import (
    dataset_cells_by_row,
    resolve_source_content,
)
from tracer.models.project import Project

EXPORT_URL = "/model-hub/annotation-queues/{queue_id}/export/"


def _unwrap(data):
    return data.get("result", data) if isinstance(data, dict) else data


def _cell_queries(captured):
    """SQL statements in *captured* that touch the cell table."""
    return [q for q in captured.captured_queries if "model_hub_cell" in q["sql"]]


def _build_dataset_queue(*, organization, workspace, user, n_rows, cells_per_row=3):
    """Seed a queue whose items are ``n_rows`` distinct dataset rows, each with
    ``cells_per_row`` cells. Returns the queue plus its rows for assertions."""
    project = Project.objects.create(
        name=f"export project {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    dataset = Dataset.objects.create(
        name=f"export dataset {uuid.uuid4().hex[:8]}",
        source=DatasetSourceChoices.BUILD.value,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    columns = [
        Column.objects.create(
            name=f"col_{i}",
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        )
        for i in range(cells_per_row)
    ]
    label = AnnotationsLabels.objects.create(
        name=f"export label {uuid.uuid4().hex[:8]}",
        type="star",
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
    )
    queue = AnnotationQueue.objects.create(
        name=f"export queue {uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization,
        workspace=workspace,
        project=project,
        dataset=dataset,
        created_by=user,
    )
    AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
    AnnotationQueueAnnotator.objects.update_or_create(
        queue=queue,
        user=user,
        deleted=False,
        defaults={
            "role": AnnotatorRole.MANAGER.value,
            "roles": FULL_ACCESS_QUEUE_ROLES,
        },
    )
    rows = []
    for order in range(n_rows):
        row = Row.objects.create(dataset=dataset, order=order)
        for i, col in enumerate(columns):
            Cell.objects.create(
                dataset=dataset, row=row, column=col, value=f"r{order}c{i}"
            )
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=row,
            organization=organization,
            workspace=workspace,
            project=project,
            status=QueueItemStatus.PENDING.value,
            order=order,
        )
        rows.append(row)
    return {"queue": queue, "dataset": dataset, "columns": columns, "rows": rows}


@pytest.mark.django_db
def test_export_json_batches_dataset_cells_no_n_plus_one(
    auth_client, organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=5
    )
    with CaptureQueriesContext(connection) as cap:
        resp = auth_client.get(
            EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=json"
        )
    assert resp.status_code == status.HTTP_200_OK, resp.data

    # The whole export resolves every row's cells in a single batched read, not
    # one query per item — this is the regression the endpoint's hang came from.
    assert len(_cell_queries(cap)) == 1

    result = _unwrap(resp.data)
    assert len(result) == 5
    by_order = {row["order"]: row for row in result}
    assert by_order[0]["source"]["fields"] == {
        "col_0": "r0c0",
        "col_1": "r0c1",
        "col_2": "r0c2",
    }


@pytest.mark.django_db
def test_export_csv_batches_dataset_cells(auth_client, organization, workspace, user):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    with CaptureQueriesContext(connection) as cap:
        resp = auth_client.get(
            EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=csv"
        )
    assert resp.status_code == status.HTTP_200_OK
    assert resp["Content-Type"] == "text/csv"
    assert len(_cell_queries(cap)) == 1
    body = resp.content.decode()
    # One header row + one line per item (no labels scored -> one row each).
    assert body.count("dataset_row") == 3


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=2)
def test_export_rejects_oversize_queue_before_resolving(
    auth_client, organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    with CaptureQueriesContext(connection) as cap:
        resp = auth_client.get(
            EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=json"
        )
    assert resp.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    # Rejected up front: no cell resolution happened.
    assert _cell_queries(cap) == []

    # CSV hits the same guard (it is checked before the format branch).
    resp_csv = auth_client.get(
        EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=csv"
    )
    assert resp_csv.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


@pytest.mark.django_db
@override_settings(ANNOTATION_EXPORT_SYNC_MAX=3)
def test_export_at_cap_boundary_succeeds(
    auth_client, organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    resp = auth_client.get(
        EXPORT_URL.format(queue_id=seed["queue"].id) + "?export_format=json"
    )
    assert resp.status_code == status.HTTP_200_OK
    assert len(_unwrap(resp.data)) == 3


@pytest.mark.django_db
def test_dataset_cells_by_row_batches_and_preseeds(
    organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=3
    )
    items = list(QueueItem.objects.filter(queue=seed["queue"]))
    # A row with no cells must still appear in the map (pre-seeded) so it is a
    # cache hit rather than a spurious per-item fallback query.
    empty_row = Row.objects.create(dataset=seed["dataset"], order=99)
    items.append(
        QueueItem.objects.create(
            queue=seed["queue"],
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=empty_row,
            organization=organization,
            workspace=workspace,
        )
    )

    with CaptureQueriesContext(connection) as cap:
        cache = dataset_cells_by_row(items)
    assert len(_cell_queries(cap)) == 1

    assert set(cache) == {str(r.id) for r in seed["rows"]} | {str(empty_row.id)}
    assert cache[str(empty_row.id)] == []
    assert len(cache[str(seed["rows"][0].id)]) == 3


@pytest.mark.django_db
def test_resolve_source_content_uses_cell_cache_then_falls_back(
    organization, workspace, user
):
    seed = _build_dataset_queue(
        organization=organization, workspace=workspace, user=user, n_rows=1
    )
    item = QueueItem.objects.select_related("dataset_row__dataset").get(
        queue=seed["queue"]
    )
    cache = dataset_cells_by_row([item])

    # With the cache, no per-item cell query fires.
    with CaptureQueriesContext(connection) as cap:
        cached = resolve_source_content(item, cell_cache=cache)
    assert _cell_queries(cap) == []

    # Without it, the single-item caller keeps its own read (fallback unchanged).
    with CaptureQueriesContext(connection) as cap:
        fresh = resolve_source_content(item)
    assert len(_cell_queries(cap)) == 1

    assert cached["fields"] == fresh["fields"] == {
        "col_0": "r0c0",
        "col_1": "r0c1",
        "col_2": "r0c2",
    }
