"""
Phase 2A – Queue Items API Tests.

Tests cover:
- Add items to queue (dataset rows, duplicates, invalid sources)
- List items with filters
- Remove items (single + bulk)
- Model validation (source_type / FK consistency)
"""

import uuid

import pytest
from rest_framework import status

from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.develop_dataset import Dataset, Row
from tfc.middleware.workspace_context import set_workspace_context

QUEUE_URL = "/model-hub/annotation-queues/"
LABEL_URL = "/model-hub/annotations-labels/"


def items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/"


def add_items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/add-items/"


def bulk_remove_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/bulk-remove/"


def item_detail_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def queue(auth_client):
    """Create a queue and return its ID."""
    resp = auth_client.post(QUEUE_URL, {"name": "Item Test Queue"}, format="json")
    return resp.data["id"]


@pytest.fixture
def dataset_with_rows(organization, workspace):
    """Create a dataset with 3 rows."""
    set_workspace_context(workspace=workspace, organization=organization)
    ds = Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
    )
    rows = []
    for i in range(3):
        rows.append(Row.objects.create(dataset=ds, order=i))
    return ds, rows


# ---------------------------------------------------------------------------
# 2A.1 – Add Items
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItems:

    def test_add_dataset_rows(self, auth_client, queue, dataset_with_rows):
        """TC-1: Add dataset rows to queue."""
        _, rows = dataset_with_rows
        items = [{"source_type": "dataset_row", "source_id": str(r.id)} for r in rows]
        resp = auth_client.post(add_items_url(queue), {"items": items}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["added"] == 3

    def test_add_duplicate_items(self, auth_client, queue, dataset_with_rows):
        """TC-3: Adding duplicate items reports duplicates."""
        _, rows = dataset_with_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        # Add first time
        auth_client.post(add_items_url(queue), {"items": items}, format="json")
        # Add again
        resp = auth_client.post(add_items_url(queue), {"items": items}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["duplicates"] == 1
        assert result["added"] == 0

    def test_add_invalid_source_type(self, auth_client, queue):
        """TC-4: Invalid source_type returns 400."""
        resp = auth_client.post(
            add_items_url(queue),
            {"items": [{"source_type": "invalid", "source_id": str(uuid.uuid4())}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_nonexistent_source(self, auth_client, queue):
        """TC-5: Non-existent source_id reports error."""
        resp = auth_client.post(
            add_items_url(queue),
            {"items": [{"source_type": "dataset_row", "source_id": str(uuid.uuid4())}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert len(result["errors"]) > 0
        assert result["added"] == 0

    def test_add_to_nonexistent_queue(self, auth_client):
        """TC-6: Add to non-existent queue returns 404."""
        resp = auth_client.post(
            add_items_url(uuid.uuid4()),
            {"items": [{"source_type": "dataset_row", "source_id": str(uuid.uuid4())}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# 2A.2 – List Items
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListItems:

    def _add_rows(self, auth_client, queue, rows):
        items = [{"source_type": "dataset_row", "source_id": str(r.id)} for r in rows]
        auth_client.post(add_items_url(queue), {"items": items}, format="json")

    def test_list_all_items(self, auth_client, queue, dataset_with_rows):
        """TC-7: List all items in queue."""
        _, rows = dataset_with_rows
        self._add_rows(auth_client, queue, rows)
        resp = auth_client.get(items_url(queue))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 3

    def test_filter_by_status(self, auth_client, queue, dataset_with_rows):
        """TC-8: Filter by status=pending."""
        _, rows = dataset_with_rows
        self._add_rows(auth_client, queue, rows)
        resp = auth_client.get(items_url(queue), {"status": "pending"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 3  # All are pending by default

    def test_status_all_does_not_filter_items(
        self, auth_client, queue, dataset_with_rows
    ):
        """The UI sends status=all for All Statuses; treat it as no filter."""
        _, rows = dataset_with_rows
        self._add_rows(auth_client, queue, rows)
        resp = auth_client.get(items_url(queue), {"status": "all"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 3

    def test_filter_by_source_type(self, auth_client, queue, dataset_with_rows):
        """TC-9: Filter by source_type."""
        _, rows = dataset_with_rows
        self._add_rows(auth_client, queue, rows)
        resp = auth_client.get(items_url(queue), {"source_type": "dataset_row"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 3


# ---------------------------------------------------------------------------
# 2A.3 – Remove Items
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveItems:

    def _add_and_get_item_ids(self, auth_client, queue, rows):
        items = [{"source_type": "dataset_row", "source_id": str(r.id)} for r in rows]
        auth_client.post(add_items_url(queue), {"items": items}, format="json")
        resp = auth_client.get(items_url(queue))
        return [r["id"] for r in resp.data["results"]]

    def test_remove_single_item(self, auth_client, queue, dataset_with_rows):
        """TC-11: Remove single item via DELETE."""
        _, rows = dataset_with_rows
        item_ids = self._add_and_get_item_ids(auth_client, queue, rows)
        resp = auth_client.delete(item_detail_url(queue, item_ids[0]))
        assert resp.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT)

    def test_bulk_remove_items(self, auth_client, queue, dataset_with_rows):
        """TC-12: Bulk remove items."""
        _, rows = dataset_with_rows
        item_ids = self._add_and_get_item_ids(auth_client, queue, rows)
        resp = auth_client.post(
            bulk_remove_url(queue),
            {"item_ids": item_ids[:2]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        # Verify remaining
        list_resp = auth_client.get(items_url(queue))
        assert list_resp.data["count"] == 1


# ---------------------------------------------------------------------------
# 2A.4 – Model Validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueueItemModelValidation:

    def test_create_item_matching_fk(
        self, organization, workspace, queue, dataset_with_rows, auth_client
    ):
        """TC-19: source_type=dataset_row with dataset_row FK is valid."""
        _, rows = dataset_with_rows
        q = AnnotationQueue.objects.get(pk=queue)
        item = QueueItem(
            queue=q,
            source_type="dataset_row",
            dataset_row=rows[0],
            organization=organization,
        )
        item.full_clean()  # Should not raise
        item.save()
        assert QueueItem.objects.filter(pk=item.pk).exists()

    def test_create_item_mismatched_fk(
        self, organization, workspace, queue, auth_client
    ):
        """TC-20: source_type=dataset_row without dataset_row FK raises error."""
        from django.core.exceptions import ValidationError

        q = AnnotationQueue.objects.get(pk=queue)
        item = QueueItem(
            queue=q,
            source_type="dataset_row",
            organization=organization,
        )
        with pytest.raises(ValidationError):
            item.full_clean()
