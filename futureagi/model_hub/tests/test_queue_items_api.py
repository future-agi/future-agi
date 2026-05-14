"""
Phase 2A – Queue Items API Tests.

Tests cover:
- Add items to queue (dataset rows, duplicates, invalid sources)
- List items with filters
- Remove items (single + bulk)
- Model validation (source_type / FK consistency)
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
    QueueItemAssignment,
    QueueItemReviewThread,
)
from model_hub.models.choices import AnnotatorRole
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


def assign_items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/assign/"


def item_detail_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/"


def demote_queue_creator_to_annotator(queue_id, user):
    membership = AnnotationQueueAnnotator.objects.get(queue_id=queue_id, user=user)
    membership.role = AnnotatorRole.ANNOTATOR.value
    membership.roles = [AnnotatorRole.ANNOTATOR.value]
    membership.save(update_fields=["role", "roles", "updated_at"])


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

    def test_add_items_requires_queue_manager(
        self, queue, dataset_with_rows, organization, workspace
    ):
        """Annotators can work items, but only managers can add items."""
        from accounts.models.user import User
        from conftest import WorkspaceAwareAPIClient
        from tfc.constants.roles import OrganizationRoles

        _, rows = dataset_with_rows
        annotator_user = User.objects.create_user(
            email=f"queue-add-annotator-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Queue Add Annotator",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue,
            user=annotator_user,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=annotator_user)
        annotator_client.set_workspace(workspace)
        resp = annotator_client.post(
            add_items_url(queue),
            {"items": [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        annotator_client.stop_workspace_injection()

    def test_add_call_execution_with_agent_workspace_fallback(
        self, auth_client, queue, organization, workspace
    ):
        """Simulation calls can be added when only the agent carries workspace."""
        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.run_test import RunTest
        from simulate.models.scenarios import Scenarios
        from simulate.models.test_execution import CallExecution, TestExecution

        agent = AgentDefinition.objects.create(
            agent_name="Workspace Agent",
            agent_type="voice",
            inbound=False,
            description="Agent with workspace ownership",
            organization=organization,
            workspace=workspace,
        )
        run_test = RunTest.objects.create(
            name="Run without workspace",
            agent_definition=agent,
            organization=organization,
            workspace=None,
        )
        scenario = Scenarios.objects.create(
            name="Workspace Scenario",
            source="hello",
            agent_definition=agent,
            organization=organization,
            workspace=None,
        )
        execution = TestExecution.objects.create(
            run_test=run_test,
            agent_definition=agent,
        )
        call = CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
        )

        resp = auth_client.post(
            add_items_url(queue),
            {"items": [{"source_type": "call_execution", "source_id": str(call.id)}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["added"] == 1
        assert result["errors"] == []
        assert QueueItem.objects.filter(
            queue_id=queue,
            source_type="call_execution",
            call_execution=call,
            deleted=False,
        ).exists()


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

    def test_filter_by_in_review_workflow_status(
        self, auth_client, queue, dataset_with_rows
    ):
        """status=in_review maps to review_status=pending_review."""
        _, rows = dataset_with_rows
        self._add_rows(auth_client, queue, rows)
        item = QueueItem.objects.filter(queue_id=queue).order_by("order").first()
        item.status = "in_progress"
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])

        resp = auth_client.get(items_url(queue), {"status": "in_review"})

        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["workflow_status"] == "in_review"
        assert resp.data["results"][0]["workflow_status_label"] == "In Review"

    def test_filter_by_resubmitted_workflow_status(
        self, auth_client, queue, dataset_with_rows, user, organization
    ):
        _, rows = dataset_with_rows
        self._add_rows(auth_client, queue, rows)
        item = QueueItem.objects.filter(queue_id=queue).order_by("order").first()
        item.status = "in_progress"
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_ITEM,
            blocking=True,
            status=QueueItemReviewThread.STATUS_ADDRESSED,
            organization=organization,
        )

        resp = auth_client.get(items_url(queue), {"status": "resubmitted"})

        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["workflow_status"] == "resubmitted"
        assert resp.data["results"][0]["workflow_status_label"] == "Resubmitted"

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

    def test_order_items_by_added_at(self, auth_client, queue, dataset_with_rows):
        """Queue item list supports whole-queue sorting by Added."""
        _, rows = dataset_with_rows
        self._add_rows(auth_client, queue, rows)

        created_items = list(QueueItem.objects.filter(queue_id=queue).order_by("order"))
        base_time = timezone.now()
        for index, item in enumerate(created_items):
            QueueItem.objects.filter(id=item.id).update(
                created_at=base_time + timedelta(minutes=index)
            )

        desc_resp = auth_client.get(items_url(queue), {"ordering": "-created_at"})
        assert desc_resp.status_code == status.HTTP_200_OK
        assert [row["id"] for row in desc_resp.data["results"]] == [
            str(item.id) for item in reversed(created_items)
        ]

        default_resp = auth_client.get(items_url(queue))
        assert default_resp.status_code == status.HTTP_200_OK
        assert [row["id"] for row in default_resp.data["results"]] == [
            str(item.id) for item in reversed(created_items)
        ]

        asc_resp = auth_client.get(items_url(queue), {"ordering": "created_at"})
        assert asc_resp.status_code == status.HTTP_200_OK
        assert [row["id"] for row in asc_resp.data["results"]] == [
            str(item.id) for item in created_items
        ]


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

    def test_annotator_can_self_claim_unassigned_item_but_not_manage_items(
        self, auth_client, queue, dataset_with_rows, organization, workspace
    ):
        """Annotators can self-claim unassigned items, but cannot manage items."""
        from accounts.models.user import User
        from conftest import WorkspaceAwareAPIClient
        from tfc.constants.roles import OrganizationRoles

        _, rows = dataset_with_rows
        item_ids = self._add_and_get_item_ids(auth_client, queue, rows)
        annotator_user = User.objects.create_user(
            email=f"queue-self-claim-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Queue Self Claim Annotator",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue,
            user=annotator_user,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=annotator_user)
        annotator_client.set_workspace(workspace)

        delete_resp = annotator_client.delete(item_detail_url(queue, item_ids[0]))
        bulk_resp = annotator_client.post(
            bulk_remove_url(queue),
            {"item_ids": item_ids[:1]},
            format="json",
        )
        assign_other_resp = annotator_client.post(
            assign_items_url(queue),
            {
                "item_ids": item_ids[:1],
                "user_ids": [str(uuid.uuid4())],
                "action": "set",
            },
            format="json",
        )
        self_assign_resp = annotator_client.post(
            assign_items_url(queue),
            {
                "item_ids": item_ids[:1],
                "user_ids": [str(annotator_user.id)],
                "action": "set",
            },
            format="json",
        )

        assert delete_resp.status_code == status.HTTP_403_FORBIDDEN
        assert bulk_resp.status_code == status.HTTP_403_FORBIDDEN
        assert assign_other_resp.status_code == status.HTTP_403_FORBIDDEN
        assert self_assign_resp.status_code == status.HTTP_200_OK
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.assigned_to_id == annotator_user.id
        assert QueueItemAssignment.objects.filter(
            queue_item_id=item_ids[0],
            user=annotator_user,
            deleted=False,
        ).exists()
        annotator_client.stop_workspace_injection()

    def test_annotator_cannot_self_assign_item_owned_by_another_user(
        self, auth_client, queue, dataset_with_rows, organization, workspace
    ):
        """Self-claim is only for unassigned items; managers handle reassignment."""
        from accounts.models.user import User
        from conftest import WorkspaceAwareAPIClient
        from tfc.constants.roles import OrganizationRoles

        _, rows = dataset_with_rows
        item_ids = self._add_and_get_item_ids(auth_client, queue, rows)
        annotator_user = User.objects.create_user(
            email=f"queue-claim-denied-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Queue Claim Denied Annotator",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        other_user = User.objects.create_user(
            email=f"queue-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Queue Owner",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue,
            user=annotator_user,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue,
            user=other_user,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )
        QueueItemAssignment.objects.create(
            queue_item_id=item_ids[0],
            user=other_user,
        )
        QueueItem.objects.filter(pk=item_ids[0]).update(assigned_to=other_user)

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=annotator_user)
        annotator_client.set_workspace(workspace)
        resp = annotator_client.post(
            assign_items_url(queue),
            {
                "item_ids": item_ids[:1],
                "user_ids": [str(annotator_user.id)],
                "action": "add",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert not QueueItemAssignment.objects.filter(
            queue_item_id=item_ids[0],
            user=annotator_user,
            deleted=False,
        ).exists()
        annotator_client.stop_workspace_injection()


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
