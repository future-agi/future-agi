"""
Tests for distributed queue progress scoping (TH-3530).

Verifies that annotate-detail progress counts are scoped to the current
user's assigned items when the queue uses a non-manual assignment strategy.
"""

import pytest
from django.conf import settings as django_settings
from rest_framework import status

from accounts.models.user import User
from model_hub.models.annotation_queues import (
    AnnotationQueueAnnotator,
    QueueItem,
    QueueItemAssignment,
)
from model_hub.models.choices import AnnotatorRole, QueueItemStatus
from model_hub.models.develop_dataset import Dataset, Row

QUEUE_URL = "/model-hub/annotation-queues/"


def items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/"


def add_items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/add-items/"


def annotate_detail_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotate-detail/"


@pytest.fixture
def dataset_with_rows(organization, workspace):
    """Create a dataset with 10 rows."""
    django_settings.CURRENT_WORKSPACE = workspace
    django_settings.ORGANIZATION = organization
    ds = Dataset.objects.create(
        name="Progress Test Dataset",
        organization=organization,
        workspace=workspace,
    )
    rows = [Row.objects.create(dataset=ds, order=i) for i in range(10)]
    return ds, rows


@pytest.fixture
def second_user(organization, workspace):
    """Create a second user in the same organization."""
    return User.objects.create_user(
        email="annotator2@futureagi.com",
        password="testpassword123",
        name="Annotator Two",
        organization=organization,
    )


@pytest.fixture
def manual_queue(auth_client):
    """Create a queue with manual assignment strategy."""
    resp = auth_client.post(
        QUEUE_URL,
        {"name": "Manual Queue", "assignment_strategy": "manual"},
        format="json",
    )
    return resp.data["id"]


@pytest.fixture
def distributed_queue(auth_client):
    """Create a queue with round_robin assignment strategy."""
    resp = auth_client.post(
        QUEUE_URL,
        {"name": "Distributed Queue", "assignment_strategy": "round_robin"},
        format="json",
    )
    return resp.data["id"]


def _add_rows_to_queue(auth_client, queue_id, rows):
    """Add dataset rows to a queue and return created item IDs."""
    items = [{"source_type": "dataset_row", "source_id": str(r.id)} for r in rows]
    auth_client.post(add_items_url(queue_id), {"items": items}, format="json")
    resp = auth_client.get(items_url(queue_id))
    return [r["id"] for r in resp.data["results"]]


@pytest.mark.django_db
class TestDistributedQueueProgress:
    """TH-3530: Progress should be scoped to current user in distributed queues."""

    def test_manual_queue_shows_total_count(
        self, auth_client, manual_queue, dataset_with_rows
    ):
        """Manual queue progress includes all items regardless of assignment."""
        _, rows = dataset_with_rows
        item_ids = _add_rows_to_queue(auth_client, manual_queue, rows)

        resp = auth_client.get(annotate_detail_url(manual_queue, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK

        result = resp.data.get("result", resp.data)
        progress = result["progress"]
        assert progress["total"] == 10

    def test_distributed_queue_scopes_to_user(
        self, auth_client, user, second_user, distributed_queue, dataset_with_rows
    ):
        """Distributed queue progress only counts items assigned to current user."""
        _, rows = dataset_with_rows
        _add_rows_to_queue(auth_client, distributed_queue, rows)

        # Manually assign items: 4 to current user, 6 to second user
        queue_items = list(
            QueueItem.objects.filter(
                queue_id=distributed_queue, deleted=False
            ).order_by("order")
        )
        for i, qi in enumerate(queue_items):
            qi.assigned_to = user if i < 4 else second_user
            qi.save(update_fields=["assigned_to"])

        # Get annotate detail for one of current user's items
        my_item = queue_items[0]
        resp = auth_client.get(annotate_detail_url(distributed_queue, str(my_item.id)))
        assert resp.status_code == status.HTTP_200_OK

        result = resp.data.get("result", resp.data)
        progress = result["progress"]
        # Should show 4 (user's items), not 10 (total)
        assert progress["total"] == 4

    def test_distributed_queue_completed_count_scoped(
        self, auth_client, user, second_user, distributed_queue, dataset_with_rows
    ):
        """Completed count in distributed queue only counts current user's completed items."""
        _, rows = dataset_with_rows
        _add_rows_to_queue(auth_client, distributed_queue, rows)

        queue_items = list(
            QueueItem.objects.filter(
                queue_id=distributed_queue, deleted=False
            ).order_by("order")
        )
        # Assign: 4 to current user, 6 to second user
        for i, qi in enumerate(queue_items):
            qi.assigned_to = user if i < 4 else second_user
            qi.save(update_fields=["assigned_to"])

        # Complete 2 of current user's items and 3 of second user's items
        for qi in queue_items[:2]:
            qi.status = "completed"
            qi.save(update_fields=["status"])
        for qi in queue_items[4:7]:
            qi.status = "completed"
            qi.save(update_fields=["status"])

        # Check progress for a pending item of current user
        my_pending_item = queue_items[2]
        resp = auth_client.get(
            annotate_detail_url(distributed_queue, str(my_pending_item.id))
        )
        assert resp.status_code == status.HTTP_200_OK

        result = resp.data.get("result", resp.data)
        progress = result["progress"]
        assert progress["total"] == 4
        # Only current user's 2 completed items, not all 5
        assert progress["completed"] == 2

    def test_manager_progress_falls_back_but_selected_annotator_stays_scoped(
        self,
        auth_client,
        user,
        second_user,
        distributed_queue,
        dataset_with_rows,
        organization,
    ):
        """
        Power-user review flow: managers can open items assigned to other
        annotators. Without a selected annotator they should see full queue
        progress, but selecting an annotator should scope progress to that
        annotator's workload.
        """
        _, rows = dataset_with_rows
        _add_rows_to_queue(auth_client, distributed_queue, rows)

        third_user = User.objects.create_user(
            email="annotator3@futureagi.com",
            password="testpassword123",
            name="Annotator Three",
            organization=organization,
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=distributed_queue,
            user=user,
            defaults={"role": AnnotatorRole.MANAGER.value},
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=distributed_queue,
            user=second_user,
            role=AnnotatorRole.ANNOTATOR.value,
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=distributed_queue,
            user=third_user,
            role=AnnotatorRole.ANNOTATOR.value,
        )

        queue_items = list(
            QueueItem.objects.filter(
                queue_id=distributed_queue, deleted=False
            ).order_by("order")
        )
        # Exercise both ownership paths: newer multi-assignment rows for one
        # annotator and the deprecated assigned_to column for another.
        for qi in queue_items[:4]:
            qi.assigned_to = None
            qi.status = (
                QueueItemStatus.COMPLETED.value
                if qi == queue_items[0]
                else QueueItemStatus.PENDING.value
            )
            qi.save(update_fields=["assigned_to", "status"])
            QueueItemAssignment.objects.create(queue_item=qi, user=second_user)
        for qi in queue_items[4:]:
            qi.assigned_to = third_user
            qi.status = (
                QueueItemStatus.COMPLETED.value
                if qi == queue_items[4]
                else QueueItemStatus.PENDING.value
            )
            qi.save(update_fields=["assigned_to", "status"])

        manager_resp = auth_client.get(
            annotate_detail_url(distributed_queue, str(queue_items[0].id))
        )
        assert manager_resp.status_code == status.HTTP_200_OK
        manager_progress = manager_resp.data.get("result", manager_resp.data)[
            "progress"
        ]
        assert manager_progress["total"] == 10
        assert manager_progress["completed"] == 2
        assert manager_progress["user_progress"]["total"] == 0
        assert manager_progress["user_progress"]["completed"] == 0

        selected_resp = auth_client.get(
            annotate_detail_url(distributed_queue, str(queue_items[0].id)),
            {"annotator_id": str(second_user.id)},
        )
        assert selected_resp.status_code == status.HTTP_200_OK
        selected_progress = selected_resp.data.get("result", selected_resp.data)[
            "progress"
        ]
        assert selected_progress["total"] == 4
        assert selected_progress["completed"] == 1
        assert selected_progress["user_progress"]["total"] == 4
        assert selected_progress["user_progress"]["completed"] == 1
