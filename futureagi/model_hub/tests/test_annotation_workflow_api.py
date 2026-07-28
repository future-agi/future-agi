"""
Phases 2B, 3A, 3B, 3C -- Annotation Workflow API Tests.

Tests cover:
- Add items to queue (including duplicate handling)
- Bulk remove items
- Submit annotations for an item
- Complete an item (with next-item navigation)
- Skip an item
- Annotate detail (source content, labels, progress, reservation)
- Next-item endpoint
- Assign items to annotators
- Filter items by assigned_to=me
- Next-item prefers assigned items
- Unassign items
- Progress endpoint (status counts, per-annotator stats)
- Auto-complete queue when all items done
- Multi-annotator complete logic (annotations_required)
"""

import importlib.util
import uuid
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    QueueItem,
    QueueItemAssignment,
    QueueItemNote,
    QueueItemReviewComment,
    QueueItemReviewThread,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import Score
from model_hub.utils.annotation_queue_helpers import calculate_agreement
from tfc.middleware.workspace_context import set_workspace_context

QUEUE_URL = "/model-hub/annotation-queues/"
LABEL_URL = "/model-hub/annotations-labels/"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/"


def add_items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/add-items/"


def bulk_remove_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/bulk-remove/"


def bulk_review_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/bulk-review/"


def submit_annotations_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotations/submit/"


def complete_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/complete/"


def skip_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/skip/"


def annotate_detail_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotate-detail/"


def next_item_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/next-item/"


def assign_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/assign/"


def progress_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/progress/"


def analytics_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/analytics/"


def release_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/release/"


def import_annotations_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotations/import/"


def annotations_list_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotations/"


def review_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/review/"


def discussion_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/discussion/"


def discussion_thread_url(queue_id, item_id, thread_id, action):
    return f"{discussion_url(queue_id, item_id)}{thread_id}/{action}/"


def discussion_reaction_url(queue_id, item_id, comment_id):
    return f"{discussion_url(queue_id, item_id)}comments/{comment_id}/reaction/"


def discussion_comment_url(queue_id, item_id, comment_id):
    return f"{discussion_url(queue_id, item_id)}comments/{comment_id}/"


def queue_status_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/update-status/"


def add_label_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/add-label/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(resp):
    """Extract result from GeneralMethods success_response."""
    return resp.data.get("result", resp.data)


def _create_score_for_item(item, label, annotator, organization, value="positive"):
    """Create a submitted queue annotation for review-flow tests."""
    return Score.objects.create(
        source_type=item.source_type,
        dataset_row=item.dataset_row,
        label=label,
        annotator=annotator,
        value=value,
        score_source="human",
        queue_item=item,
        organization=organization,
        workspace=item.workspace,
    )


def _create_workspace_viewer_user(organization, workspace, *, email_prefix):
    """Create a read-only workspace user with real org/workspace memberships."""
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    viewer = User.objects.create_user(
        email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Workspace Viewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    org_membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=viewer,
        organization=organization,
        defaults={
            "role": OrganizationRoles.MEMBER_VIEW_ONLY,
            "level": Level.VIEWER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=viewer,
        workspace=workspace,
        defaults={
            "role": OrganizationRoles.WORKSPACE_VIEWER,
            "level": Level.WORKSPACE_VIEWER,
            "organization_membership": org_membership,
            "is_active": True,
        },
    )
    return viewer


def _create_workspace_admin_user(organization, workspace, *, email_prefix):
    """Create a workspace admin who is NOT an org admin (org role = Member).

    This isolates the ``WorkspaceMembership.level_or_legacy >= WORKSPACE_ADMIN``
    branch of ``user_has_annotation_queue_admin_access`` from the org-admin path.
    """
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    admin = User.objects.create_user(
        email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Workspace Admin",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    org_membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=admin,
        organization=organization,
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=admin,
        workspace=workspace,
        defaults={
            "role": OrganizationRoles.WORKSPACE_ADMIN,
            "level": Level.WORKSPACE_ADMIN,
            "organization_membership": org_membership,
            "is_active": True,
        },
    )
    return admin


def _jwt_workspace_client(user, organization, workspace):
    """Authenticate through the real auth class so workspace write checks run."""
    from rest_framework.test import APIClient

    client = APIClient()
    login_resp = client.post(
        "/accounts/token/",
        {"email": user.email, "password": "testpassword123"},
        format="json",
    )
    assert login_resp.status_code == status.HTTP_200_OK, login_resp.data
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login_resp.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def allow_queue_creation_entitlement():
    """Keep workflow tests focused on queue behavior, not billing cache I/O."""
    with ExitStack() as stack:
        check_can_create = stack.enter_context(
            patch("tfc.ee_gating.check_ee_can_create")
        )
        try:
            entitlements_available = (
                importlib.util.find_spec("ee.usage.services.entitlements") is not None
            )
        except ModuleNotFoundError:
            entitlements_available = False

        if entitlements_available:
            stack.enter_context(
                patch(
                    "ee.usage.services.entitlements.Entitlements.check_feature",
                    return_value=SimpleNamespace(allowed=True, reason=None),
                )
            )
        yield check_can_create


@pytest.fixture
def queue_id(auth_client, label):
    """Create a queue and return its UUID.

    A queue must have at least one label (serializer-enforced). Use the shared
    `label` fixture so the queue carries exactly the label the workflow tests
    annotate — otherwise an extra (required) label would block item completion.
    """
    resp = auth_client.post(
        QUEUE_URL,
        {"name": "Workflow Test Queue", "label_ids": [str(label.id)]},
        format="json",
    )
    return resp.data["id"]


@pytest.fixture
def active_queue_id(auth_client, queue_id):
    """Create a queue and activate it."""
    auth_client.post(queue_status_url(queue_id), {"status": "active"}, format="json")
    return queue_id


@pytest.fixture
def dataset_rows(organization, workspace):
    """Create a dataset with 5 rows."""
    set_workspace_context(workspace=workspace, organization=organization)
    ds = Dataset.objects.create(
        name="Workflow DS",
        organization=organization,
        workspace=workspace,
    )
    rows = [Row.objects.create(dataset=ds, order=i) for i in range(5)]
    return ds, rows


@pytest.fixture
def label(organization, workspace):
    """Create a categorical label directly."""
    return AnnotationsLabels.objects.create(
        name="Sentiment",
        type="categorical",
        settings={
            "options": [{"label": "positive"}, {"label": "negative"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def label_b(organization, workspace):
    """Create a second label."""
    return AnnotationsLabels.objects.create(
        name="Quality",
        type="star",
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def queue_with_items(auth_client, queue_id, dataset_rows, label, organization):
    """Queue with 3 items and 1 label attached.

    The queue is activated so submit/complete/skip endpoints (which require
    ``status == ACTIVE``) work without each test having to opt in.
    """
    ds, rows = dataset_rows
    # The queue already has `label` attached (via the queue_id fixture), so
    # don't re-attach it here (would violate unique_active_queue_label).
    # Add 3 items
    items_payload = [
        {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:3]
    ]
    auth_client.post(add_items_url(queue_id), {"items": items_payload}, format="json")
    # Activate queue so /annotations/submit works (requires ACTIVE).
    auth_client.post(queue_status_url(queue_id), {"status": "active"}, format="json")
    item_ids = list(
        QueueItem.objects.filter(queue_id=queue_id, deleted=False)
        .order_by("order")
        .values_list("id", flat=True)
    )
    return queue_id, item_ids, label


# ===========================================================================
# Phase 2B -- Add-to-queue & bulk-remove
# ===========================================================================


@pytest.mark.django_db
class TestAddItems:
    def test_add_dataset_rows(self, auth_client, queue_id, dataset_rows):
        _, rows = dataset_rows
        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:2]
        ]
        resp = auth_client.post(
            add_items_url(queue_id), {"items": items}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["added"] == 2

    def test_duplicate_items_skipped(self, auth_client, queue_id, dataset_rows):
        _, rows = dataset_rows
        item = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": item}, format="json")
        resp = auth_client.post(add_items_url(queue_id), {"items": item}, format="json")
        result = _result(resp)
        assert result["duplicates"] == 1
        assert result["added"] == 0


@pytest.mark.django_db
class TestBulkRemove:
    def _seed_child_rows(self, item, label, user, organization, workspace):
        QueueItem.objects.filter(pk=item.pk).update(
            assigned_to=user,
            reserved_by=user,
            reserved_at=timezone.now(),
            reservation_expires_at=timezone.now() + timedelta(minutes=10),
        )
        QueueItemAssignment.objects.create(queue_item=item, user=user)
        Score.objects.create(
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=item.dataset_row,
            label=label,
            value="positive",
            notes="delete cleanup score",
            annotator=user,
            queue_item=item,
            organization=organization,
            workspace=workspace,
        )
        QueueItemNote.objects.create(
            queue_item=item,
            annotator=user,
            notes="delete cleanup note",
            organization=organization,
            workspace=workspace,
        )
        thread = QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            organization=organization,
            workspace=workspace,
        )
        QueueItemReviewComment.objects.create(
            queue_item=item,
            thread=thread,
            reviewer=user,
            comment="delete cleanup review comment",
            organization=organization,
            workspace=workspace,
        )

    def _assert_child_rows_deleted(self, item_id):
        item = QueueItem.all_objects.get(pk=item_id)
        assert item.deleted is True
        assert item.assigned_to_id is None
        assert item.reserved_by_id is None
        assert item.reserved_at is None
        assert item.reservation_expires_at is None
        assert not QueueItemAssignment.objects.filter(
            queue_item_id=item_id,
            deleted=False,
        ).exists()
        assert not Score.objects.filter(queue_item_id=item_id, deleted=False).exists()
        assert not QueueItemNote.objects.filter(
            queue_item_id=item_id,
            deleted=False,
        ).exists()
        assert not QueueItemReviewThread.objects.filter(
            queue_item_id=item_id,
            deleted=False,
        ).exists()
        assert not QueueItemReviewComment.objects.filter(
            queue_item_id=item_id,
            deleted=False,
        ).exists()

    def test_bulk_remove(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.post(
            bulk_remove_url(queue_id),
            {"item_ids": [str(item_ids[0]), str(item_ids[1])]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["removed"] == 2
        # Only 1 item remains
        remaining = QueueItem.objects.filter(queue_id=queue_id, deleted=False).count()
        assert remaining == 1

    def test_single_delete_soft_deletes_child_rows(
        self, auth_client, queue_with_items, user, organization, workspace
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        self._seed_child_rows(item, label, user, organization, workspace)

        resp = auth_client.delete(f"{QUEUE_URL}{queue_id}/items/{item.id}/")

        assert resp.status_code == status.HTTP_204_NO_CONTENT
        self._assert_child_rows_deleted(item.id)

    def test_bulk_remove_soft_deletes_child_rows(
        self, auth_client, queue_with_items, user, organization, workspace
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        self._seed_child_rows(item, label, user, organization, workspace)

        resp = auth_client.post(
            bulk_remove_url(queue_id),
            {"item_ids": [str(item.id)]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["removed"] == 1
        self._assert_child_rows_deleted(item.id)


# ===========================================================================
# Phase 3A -- Submit Annotations, Complete, Skip, Annotate Detail
# ===========================================================================


@pytest.mark.django_db
class TestSubmitAnnotations:
    def test_submit_annotations(self, auth_client, queue_with_items):
        """Submit annotation for an item."""
        queue_id, item_ids, label = queue_with_items
        payload = {
            "annotations": [{"label_id": str(label.id), "value": "positive"}],
        }
        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            payload,
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 1

    def test_workspace_viewer_queue_annotator_can_submit_and_complete_assigned_item(
        self,
        queue_with_items,
        organization,
        workspace,
    ):
        """Queue role permissions own annotation writes for workspace viewers."""
        queue_id, item_ids, label = queue_with_items
        viewer = _create_workspace_viewer_user(
            organization,
            workspace,
            email_prefix="viewer-queue-annotator",
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue_id,
            user=viewer,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )
        QueueItemAssignment.objects.create(
            queue_item_id=item_ids[0],
            user=viewer,
        )
        viewer_client = _jwt_workspace_client(viewer, organization, workspace)

        submit_resp = viewer_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        assert submit_resp.status_code == status.HTTP_200_OK
        assert _result(submit_resp)["submitted"] == 1

        complete_resp = viewer_client.post(
            complete_url(queue_id, item_ids[0]),
            {},
            format="json",
        )
        assert complete_resp.status_code == status.HTTP_200_OK
        assert _result(complete_resp)["completed_item_id"] == str(item_ids[0])

    def test_workspace_viewer_queue_manager_can_assign_items_but_not_create_queues(
        self,
        queue_with_items,
        organization,
        workspace,
    ):
        """Queue-manager writes reach the view; broad workspace writes still fail."""
        queue_id, item_ids, _ = queue_with_items
        viewer = _create_workspace_viewer_user(
            organization,
            workspace,
            email_prefix="viewer-queue-manager",
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue_id,
            user=viewer,
            role=AnnotatorRole.MANAGER.value,
            roles=[AnnotatorRole.MANAGER.value],
        )
        viewer_client = _jwt_workspace_client(viewer, organization, workspace)

        assign_resp = viewer_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0])],
                "user_ids": [str(viewer.id)],
                "action": "set",
            },
            format="json",
        )
        assert assign_resp.status_code == status.HTTP_200_OK
        assert QueueItemAssignment.objects.filter(
            queue_item_id=item_ids[0],
            user=viewer,
            deleted=False,
        ).exists()

        create_resp = viewer_client.post(
            QUEUE_URL,
            {"name": "Viewer should not create queues"},
            format="json",
        )
        assert create_resp.status_code == status.HTTP_403_FORBIDDEN
        assert "Write access denied to this workspace" in str(create_resp.data)

    def test_submit_rejects_items_waiting_for_review(
        self, auth_client, queue_with_items
    ):
        """Pending-review items are locked until a reviewer requests changes."""
        queue_id, item_ids, label = queue_with_items
        AnnotationQueue.objects.filter(pk=queue_id).update(requires_review=True)
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "waiting for review" in _result(resp)
        assert not Score.objects.filter(queue_item_id=item_ids[0]).exists()

    def test_submit_rejects_user_without_queue_role(
        self,
        queue_with_items,
        second_user,
        workspace,
    ):
        """Workspace access is not enough to annotate a custom queue."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, label = queue_with_items
        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp = second_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "Only queue annotators or managers" in _result(resp)
        second_client.stop_workspace_injection()

    def test_submit_rejects_unassigned_manual_item_for_annotator(
        self,
        queue_with_items,
        organization,
        workspace,
        label,
    ):
        """Manual queues require manager assignment before annotators can submit."""
        from accounts.models.user import User
        from conftest import WorkspaceAwareAPIClient
        from tfc.constants.roles import OrganizationRoles

        queue_id, item_ids, _ = queue_with_items
        annotator_user = User.objects.create_user(
            email=f"queue-unassigned-submit-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Queue Unassigned Submitter",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue_id,
            user=annotator_user,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=annotator_user)
        annotator_client.set_workspace(workspace)
        resp = annotator_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "must be assigned" in _result(resp)
        assert not Score.objects.filter(
            queue_item_id=item_ids[0],
            annotator=annotator_user,
            deleted=False,
        ).exists()
        annotator_client.stop_workspace_injection()

    def test_submit_allows_org_admin_with_reviewer_role_to_annotate(
        self, auth_client, dataset_rows, organization, workspace, user, label
    ):
        """Org/workspace admins can annotate even with a non-annotator queue role.

        ``auth_client`` is the org owner. On a queue created by someone else
        where the owner is only a reviewer, admin access grants
        manager-equivalent rights so they may submit annotations on an
        unassigned manual item.
        """
        from accounts.models.user import User
        from tfc.constants.roles import OrganizationRoles

        dataset, rows = dataset_rows
        creator = User.objects.create_user(
            email=f"admin-annotate-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Admin Annotate Owner",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        queue = AnnotationQueue.objects.create(
            name="Admin Annotate Queue",
            dataset=dataset,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            created_by=creator,
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label,
            order=0,
            required=True,
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue=queue,
            user=creator,
            deleted=False,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [AnnotatorRole.MANAGER.value],
            },
        )
        # auth_client's org-owner user is only a reviewer on this queue.
        admin_user = user
        AnnotationQueueAnnotator.objects.update_or_create(
            queue=queue,
            user=admin_user,
            deleted=False,
            defaults={
                "role": AnnotatorRole.REVIEWER.value,
                "roles": [AnnotatorRole.REVIEWER.value],
            },
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=rows[0],
            organization=organization,
            workspace=workspace,
        )

        resp = auth_client.post(
            submit_annotations_url(queue.id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 1
        assert Score.objects.filter(
            queue_item_id=item.id,
            annotator=admin_user,
            deleted=False,
        ).exists()

    def test_submit_allows_manager_unassigned_item_when_queue_has_assignments(
        self, auth_client, queue_with_items, user, second_user, organization
    ):
        """Managers can annotate an unassigned manual item even once other items
        in the queue are assigned to someone else."""
        queue_id, item_ids, label = queue_with_items
        AnnotationQueue.objects.filter(pk=queue_id).update(auto_assign=False)
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        # Assign a different item so the queue has at least one assignment.
        QueueItemAssignment.objects.create(
            queue_item=QueueItem.objects.get(pk=item_ids[1]),
            user=second_user,
        )

        # The first item stays unassigned; the manager (queue creator) submits.
        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 1
        assert Score.objects.filter(
            queue_item_id=item_ids[0],
            annotator=user,
            deleted=False,
        ).exists()

    def test_reviewer_assigned_item_can_add_missing_annotation_during_review(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.requires_review = True
        queue.auto_assign = False
        queue.save(update_fields=["requires_review", "auto_assign", "updated_at"])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value, AnnotatorRole.REVIEWER.value],
            },
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemAssignment.objects.create(
            queue_item=item,
            user=user,
        )
        _create_score_for_item(item, label, second_user, organization, value="negative")

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 1
        assert Score.objects.filter(
            queue_item=item,
            annotator=user,
            label=label,
            deleted=False,
        ).exists()

    def test_submit_rejects_rework_targeted_to_another_annotator(
        self,
        api_client,
        queue_with_items,
        user,
        second_user,
        label,
        organization,
        workspace,
    ):
        """A per-score review rejection can only be reworked by its target."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "rejected"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemReviewComment.objects.create(
            queue_item=item,
            reviewer=user,
            label=label,
            target_annotator=user,
            action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            comment="Only the target annotator should fix this score.",
            organization=organization,
        )

        api_client.force_authenticate(user=second_user)
        api_client.set_workspace(workspace)
        resp = api_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "different annotator" in _result(resp)

    def test_submit_allows_manager_to_override_targeted_rework(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        label,
        organization,
    ):
        """Managers/reviewers can intentionally repair an item targeted to someone else."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "rejected"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=user,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [
                    AnnotatorRole.MANAGER.value,
                    AnnotatorRole.REVIEWER.value,
                    AnnotatorRole.ANNOTATOR.value,
                ],
            },
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemReviewComment.objects.create(
            queue_item=item,
            reviewer=user,
            label=label,
            target_annotator=second_user,
            action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            comment="Second annotator missed this.",
            organization=organization,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 1

    def test_submit_allows_global_feedback_when_targeted_rework_coexists(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
        label,
        organization,
    ):
        """Global reviewer feedback applies to every annotator even with targeted feedback."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "rejected"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_ITEM,
            status=QueueItemReviewThread.STATUS_OPEN,
            blocking=True,
            organization=organization,
            workspace=workspace,
        )
        QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            label=label,
            target_annotator=second_user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_SCORE,
            status=QueueItemReviewThread.STATUS_OPEN,
            blocking=True,
            organization=organization,
            workspace=workspace,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 1

    def test_submit_updates_existing_annotation(self, auth_client, queue_with_items):
        """Upsert: submitting again updates the value."""
        queue_id, item_ids, label = queue_with_items
        url = submit_annotations_url(queue_id, item_ids[0])
        auth_client.post(
            url,
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(
            url,
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )
        ann = Score.objects.get(queue_item_id=item_ids[0], label=label, deleted=False)
        assert ann.value == "negative"

    def test_resubmitting_changed_value_preserves_score_value_history(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, label = queue_with_items
        url = submit_annotations_url(queue_id, item_ids[0])

        for value in ("positive", "negative", "positive"):
            resp = auth_client.post(
                url,
                {"annotations": [{"label_id": str(label.id), "value": value}]},
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.data

        ann = Score.objects.get(queue_item_id=item_ids[0], label=label, deleted=False)
        assert ann.value == "positive"
        assert [entry["value"] for entry in ann.value_history] == [
            "positive",
            "negative",
        ]
        assert all(entry.get("at") for entry in ann.value_history)

        history_resp = auth_client.get(annotations_list_url(queue_id, item_ids[0]))
        assert history_resp.status_code == status.HTTP_200_OK, history_resp.data
        history = _result(history_resp)
        assert history[0]["value"] == "positive"
        assert [entry["value"] for entry in history[0]["value_history"]] == [
            "positive",
            "negative",
        ]

    def test_submit_rejects_text_value_longer_than_label_limit(
        self,
        auth_client,
        queue_with_items,
        organization,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        text_label = AnnotationsLabels.objects.create(
            name="Short Text",
            type="text",
            settings={"placeholder": "", "min_length": 0, "max_length": 5},
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=text_label,
            order=1,
            required=False,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {"label_id": str(text_label.id), "value": {"text": "too long"}}
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "at most 5 characters" in _result(resp)
        assert not Score.objects.filter(
            queue_item_id=item_ids[0],
            label=text_label,
            deleted=False,
        ).exists()

    def test_submit_rejects_numeric_value_outside_label_bounds(
        self,
        auth_client,
        queue_with_items,
        organization,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        numeric_label = AnnotationsLabels.objects.create(
            name="Bounded Score",
            type="numeric",
            settings={
                "min": 0,
                "max": 5,
                "step_size": 1,
                "display_type": "button",
            },
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=numeric_label,
            order=1,
            required=False,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {"label_id": str(numeric_label.id), "value": {"value": 6}}
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "between 0 and 5" in _result(resp)
        assert not Score.objects.filter(
            queue_item_id=item_ids[0],
            label=numeric_label,
            deleted=False,
        ).exists()

    def test_submit_stores_notes_per_label(
        self, auth_client, queue_with_items, label_b
    ):
        """Labels with allow_notes keep their own notes instead of sharing one field."""
        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])
        label_b.allow_notes = True
        label_b.save(update_fields=["allow_notes", "updated_at"])
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label_b,
            order=1,
            required=False,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "positive",
                        "notes": "sentiment note",
                    },
                    {
                        "label_id": str(label_b.id),
                        "value": {"rating": 4},
                        "notes": "quality note",
                    },
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        notes_by_label = dict(
            Score.objects.filter(queue_item_id=item_ids[0], deleted=False).values_list(
                "label_id", "notes"
            )
        )
        assert notes_by_label[label.id] == "sentiment note"
        assert notes_by_label[label_b.id] == "quality note"

    @pytest.mark.api
    def test_submit_label_notes_reload_through_annotate_detail(
        self, auth_client, queue_with_items, label_b
    ):
        """Saved label notes come back when the annotation workspace reopens."""
        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])
        label_b.allow_notes = True
        label_b.save(update_fields=["allow_notes", "updated_at"])
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label_b,
            order=1,
            required=False,
        )

        submit_resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "positive",
                        "notes": "sentiment reload note",
                    },
                    {
                        "label_id": str(label_b.id),
                        "value": {"rating": 4},
                        "notes": "quality reload note",
                    },
                ]
            },
            format="json",
        )
        assert submit_resp.status_code == status.HTTP_200_OK

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert detail_resp.status_code == status.HTTP_200_OK
        annotations = _result(detail_resp)["annotations"]
        notes_by_label = {
            str(annotation["label_id"]): annotation["notes"]
            for annotation in annotations
        }
        assert notes_by_label[str(label.id)] == "sentiment reload note"
        assert notes_by_label[str(label_b.id)] == "quality reload note"

    @pytest.mark.api
    def test_submit_item_notes_reload_for_dataset_rows(
        self, auth_client, queue_with_items, user
    ):
        """Dataset-backed queue items persist whole-item notes for review."""
        queue_id, item_ids, label = queue_with_items

        submit_resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {"label_id": str(label.id), "value": "positive"},
                ],
                "item_notes": "whole dataset-row item note",
            },
            format="json",
        )
        assert submit_resp.status_code == status.HTTP_200_OK

        assert (
            QueueItemNote.objects.get(
                queue_item_id=item_ids[0],
                annotator=user,
            ).notes
            == "whole dataset-row item note"
        )

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert detail_resp.status_code == status.HTTP_200_OK
        detail = _result(detail_resp)
        assert detail["existing_notes"] == "whole dataset-row item note"
        assert detail["span_notes"][0]["notes"] == "whole dataset-row item note"
        assert detail["span_notes"][0]["annotator_id"] == str(user.id)

    def test_submit_only_stores_notes_for_note_enabled_labels(
        self, auth_client, queue_with_items, label_b
    ):
        """Legacy top-level notes are ignored for labels without allow_notes."""
        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])
        label_b.allow_notes = False
        label_b.save(update_fields=["allow_notes", "updated_at"])
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label_b,
            order=1,
            required=False,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "notes": "legacy shared note",
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "positive",
                    },
                    {
                        "label_id": str(label_b.id),
                        "value": {"rating": 4},
                        "notes": "should be ignored",
                    },
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        notes_by_label = dict(
            Score.objects.filter(queue_item_id=item_ids[0], deleted=False).values_list(
                "label_id", "notes"
            )
        )
        assert notes_by_label[label.id] == "legacy shared note"
        assert notes_by_label[label_b.id] == ""

    def test_submit_sets_in_progress(self, auth_client, queue_with_items):
        """Item status changes from pending to in_progress on first submit."""
        queue_id, item_ids, label = queue_with_items
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.IN_PROGRESS.value

    def test_submit_invalid_label_id(self, auth_client, queue_with_items):
        """Invalid label_id is silently skipped (submitted=0)."""
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(uuid.uuid4()), "value": "x"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 0

    def test_submit_for_nonexistent_item(self, auth_client, queue_with_items):
        queue_id, _, label = queue_with_items
        resp = auth_client.post(
            submit_annotations_url(queue_id, uuid.uuid4()),
            {"annotations": [{"label_id": str(label.id), "value": "x"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestCompleteItem:
    def test_complete_returns_next(self, auth_client, queue_with_items):
        """Complete item returns next pending item.

        ``complete`` requires the user to have submitted at least one Score
        for the item (it short-circuits with 400 otherwise — see
        ``_complete_item`` view). Pre-fix this test skipped that step and
        relied on the legacy code path silently accepting empty completes.
        """
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=2)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[2]).update(created_at=base_time)
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        resp = auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["completed_item_id"] == str(item_ids[0])
        assert result["next_item"]["id"] == str(item_ids[1])

    def test_complete_uses_submitted_item_as_cursor_with_stale_history(
        self, auth_client, queue_with_items
    ):
        """Submitting after navigating back should not skip future history items."""
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=2)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[2]).update(created_at=base_time)

        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )

        # Simulates browser history after visiting item 1 -> item 2, then
        # navigating back to item 1 before submitting. The next item should be
        # item 2, not item 3.
        resp = auth_client.post(
            complete_url(queue_id, item_ids[0]),
            {"exclude": [str(item_ids[0]), str(item_ids[1])]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["next_item"]["id"] == str(item_ids[1])

    def test_complete_last_item_returns_null_next(self, auth_client, queue_with_items):
        """Completing the last item returns next_item=null."""
        queue_id, item_ids, label = queue_with_items
        for iid in item_ids:
            # Submit annotation first so completion actually completes
            auth_client.post(
                submit_annotations_url(queue_id, iid),
                {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
                format="json",
            )
            resp = auth_client.post(complete_url(queue_id, iid), format="json")
        result = _result(resp)
        assert result["next_item"] is None

    def test_complete_sets_completed_status(self, auth_client, queue_with_items):
        """Item status set to completed (when annotations_required=1)."""
        queue_id, item_ids, label = queue_with_items
        # Submit an annotation first so the count is >= annotations_required (1)
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.COMPLETED.value

    def test_submitted_completed_item_is_visible_in_list_and_history(
        self, auth_client, queue_with_items
    ):
        """Submitted work is visible after returning to the queue list/history."""
        queue_id, item_ids, label = queue_with_items
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])

        submit_resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "positive",
                        "notes": "visible immediately",
                    }
                ],
                "item_notes": "whole item history note",
            },
            format="json",
        )
        assert submit_resp.status_code == status.HTTP_200_OK, submit_resp.data

        complete_resp = auth_client.post(
            complete_url(queue_id, item_ids[0]), format="json"
        )
        assert complete_resp.status_code == status.HTTP_200_OK, complete_resp.data

        completed_items_resp = auth_client.get(
            items_url(queue_id), {"status": "completed"}
        )
        assert completed_items_resp.status_code == status.HTTP_200_OK
        completed_items = completed_items_resp.data["results"]
        assert [row["id"] for row in completed_items] == [str(item_ids[0])]
        assert completed_items[0]["status"] == QueueItemStatus.COMPLETED.value

        history_resp = auth_client.get(annotations_list_url(queue_id, item_ids[0]))
        assert history_resp.status_code == status.HTTP_200_OK
        history = _result(history_resp)
        assert len(history) == 1
        assert history[0]["label_id"] == str(label.id)
        assert history[0]["value"] == "positive"
        assert history[0]["notes"] == "visible immediately"

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert detail_resp.status_code == status.HTTP_200_OK
        detail = _result(detail_resp)
        assert detail["annotations"][0]["value"] == "positive"
        assert detail["existing_notes"] == "whole item history note"

    def test_history_is_isolated_per_queue_item(
        self, auth_client, queue_with_items, user
    ):
        """Each queue keeps its own Score row for the same (source, label,
        annotator) tuple, so the same source scored in two queues yields
        two independent annotation histories.

        Pre-revamp the Score row was source-scoped and re-submitting the
        same label from a second queue silently updated the first queue's
        annotation. Now uniqueness includes queue_item, so each queue has
        an independent review context.
        """
        queue_id, item_ids, label = queue_with_items
        first_item = QueueItem.objects.get(pk=item_ids[0])

        auth_client.post(
            submit_annotations_url(queue_id, first_item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        complete_resp = auth_client.post(
            complete_url(queue_id, first_item.id), format="json"
        )
        assert complete_resp.status_code == status.HTTP_200_OK, complete_resp.data

        queue_2_resp = auth_client.post(
            QUEUE_URL,
            {
                "name": "Second queue with same source",
                "label_ids": [str(label.id)],
            },
            format="json",
        )
        assert queue_2_resp.status_code == status.HTTP_201_CREATED, queue_2_resp.data
        queue_2_id = queue_2_resp.data["id"]
        add_resp = auth_client.post(
            add_items_url(queue_2_id),
            {
                "items": [
                    {
                        "source_type": "dataset_row",
                        "source_id": str(first_item.dataset_row_id),
                    }
                ]
            },
            format="json",
        )
        assert add_resp.status_code == status.HTTP_200_OK, add_resp.data
        auth_client.post(
            queue_status_url(queue_2_id), {"status": "active"}, format="json"
        )
        second_item = QueueItem.objects.get(queue_id=queue_2_id, deleted=False)

        submit_second_resp = auth_client.post(
            submit_annotations_url(queue_2_id, second_item.id),
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )
        assert submit_second_resp.status_code == status.HTTP_200_OK

        # Two independent Score rows now exist — one per queue context.
        scores = list(
            Score.objects.filter(
                dataset_row_id=first_item.dataset_row_id,
                label=label,
                annotator=user,
                deleted=False,
            ).order_by("created_at")
        )
        assert len(scores) == 2
        scores_by_item = {s.queue_item_id: s for s in scores}
        assert scores_by_item[first_item.id].value == "positive"
        assert scores_by_item[second_item.id].value == "negative"

        # The first queue's history still shows the original positive value;
        # queue 2's negative submission does not leak into it.
        history_resp = auth_client.get(annotations_list_url(queue_id, first_item.id))
        assert history_resp.status_code == status.HTTP_200_OK
        history = _result(history_resp)
        assert len(history) == 1
        assert history[0]["value"] == "positive"
        assert str(history[0]["queue_item"]) == str(first_item.id)

        detail_resp = auth_client.get(annotate_detail_url(queue_id, first_item.id))
        assert detail_resp.status_code == status.HTTP_200_OK
        assert _result(detail_resp)["annotations"][0]["value"] == "positive"

    def test_completed_queue_item_can_be_edited(self, auth_client, queue_with_items):
        """Completed queues still allow revising an already completed item."""
        queue_id, item_ids, label = queue_with_items
        for iid in item_ids:
            auth_client.post(
                submit_annotations_url(queue_id, iid),
                {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
                format="json",
            )
            auth_client.post(complete_url(queue_id, iid), format="json")

        queue = AnnotationQueue.objects.get(pk=queue_id)
        assert queue.status == AnnotationQueueStatusChoices.COMPLETED.value

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        score = Score.objects.get(queue_item_id=item_ids[0], label=label, deleted=False)
        assert score.value == "negative"

    def test_add_required_label_reopens_completed_items(
        self, auth_client, queue_with_items, label_b, monkeypatch
    ):
        """A new required label makes completed items editable again."""
        monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *_, **__: None)
        queue_id, item_ids, label = queue_with_items
        for iid in item_ids:
            auth_client.post(
                submit_annotations_url(queue_id, iid),
                {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
                format="json",
            )
            auth_client.post(complete_url(queue_id, iid), format="json")

        queue = AnnotationQueue.objects.get(pk=queue_id)
        assert queue.status == AnnotationQueueStatusChoices.COMPLETED.value

        add_resp = auth_client.post(
            add_label_url(queue_id),
            {"label_id": str(label_b.id), "required": True},
            format="json",
        )

        assert add_resp.status_code == status.HTTP_200_OK, add_resp.data
        add_result = _result(add_resp)
        assert add_result["reopened_items"] == len(item_ids)
        assert add_result["queue_status"] == AnnotationQueueStatusChoices.ACTIVE.value
        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.ACTIVE.value
        assert set(
            QueueItem.objects.filter(id__in=item_ids).values_list("status", flat=True)
        ) == {QueueItemStatus.IN_PROGRESS.value}

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert detail_resp.status_code == status.HTTP_200_OK, detail_resp.data
        detail = _result(detail_resp)
        labels = {row["label_id"]: row for row in detail["labels"]}
        assert str(label.id) in labels
        assert str(label_b.id) in labels
        assert labels[str(label_b.id)]["required"] is True
        annotation_label_ids = {
            row["label_id"] for row in detail["annotations"] if row.get("label_id")
        }
        assert str(label.id) in annotation_label_ids
        assert str(label_b.id) not in annotation_label_ids

        submit_new_label = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label_b.id), "value": {"rating": 4}}]},
            format="json",
        )
        assert submit_new_label.status_code == status.HTTP_200_OK, submit_new_label.data

        complete_resp = auth_client.post(
            complete_url(queue_id, item_ids[0]), format="json"
        )
        assert complete_resp.status_code == status.HTTP_200_OK, complete_resp.data
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.COMPLETED.value

    @pytest.mark.xfail(
        reason="Pre-existing backend bug: complete_item view doesn't clear "
        "reservation fields. Tracked in Team B review (E14). Needs fix in "
        "model_hub/views/annotation_queues.py:complete_item."
    )
    def test_complete_clears_reservation(self, auth_client, queue_with_items, user):
        """Reservation is cleared on complete."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.reserved_by = user
        item.reserved_at = timezone.now()
        item.reservation_expires_at = timezone.now() + timedelta(minutes=30)
        item.save()

        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        item.refresh_from_db()
        assert item.reserved_by is None

    def test_complete_nonexistent_item(self, auth_client, queue_with_items):
        queue_id, _, _ = queue_with_items
        resp = auth_client.post(complete_url(queue_id, uuid.uuid4()), format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestReviewItem:
    def test_approve_rejects_items_not_waiting_for_review(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="rejected",
        )

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve"},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "pending review" in _result(resp)

    def test_request_changes_requires_feedback(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "request_changes"},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Feedback is required" in _result(resp)

    def test_request_changes_stores_overall_and_label_feedback(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.review_status = "pending_review"
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.save(update_fields=["review_status", "status", "updated_at"])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        _create_score_for_item(item, label, second_user, organization)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {
                "action": "request_changes",
                "notes": "Please re-check the item.",
                "label_comments": [
                    {
                        "label_id": str(label.id),
                        "target_annotator_id": str(second_user.id),
                        "comment": "Sentiment should be negative.",
                    }
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["action"] == "request_changes"
        item.refresh_from_db()
        assert item.status == QueueItemStatus.IN_PROGRESS.value
        assert item.review_status == "pending_review"
        assert item.review_notes == "Please re-check the item."
        assert item.reviewed_by == user
        comments = list(
            QueueItemReviewComment.objects.filter(queue_item=item).order_by(
                "created_at"
            )
        )
        assert len(comments) == 2
        assert comments[0].label_id is None
        assert comments[0].comment == "Please re-check the item."
        assert comments[1].label_id == label.id
        assert comments[1].target_annotator_id == second_user.id
        assert comments[1].comment == "Sentiment should be negative."
        threads = list(
            QueueItemReviewThread.objects.filter(queue_item=item).order_by("created_at")
        )
        assert len(threads) == 2
        assert threads[0].scope == QueueItemReviewThread.SCOPE_ITEM
        assert threads[0].blocking is False
        assert threads[1].scope == QueueItemReviewThread.SCOPE_SCORE
        assert threads[1].blocking is True
        assert threads[1].status == QueueItemReviewThread.STATUS_OPEN
        assert comments[1].thread_id == threads[1].id

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        detail = _result(detail_resp)
        assert len(detail["review_comments"]) == 2
        assert len(detail["review_threads"]) == 2
        label_comment = next(
            c for c in detail["review_comments"] if c["label_id"] == str(label.id)
        )
        assert label_comment["label_name"] == label.name
        assert label_comment["target_annotator_id"] == str(second_user.id)
        assert label_comment["reviewer_name"] == user.name
        assert label_comment["thread_status"] == QueueItemReviewThread.STATUS_OPEN
        assert label_comment["blocking"] is True

    def test_review_rejects_legacy_label_comment_aliases(
        self,
        auth_client,
        queue_with_items,
        user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.review_status = "pending_review"
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.save(update_fields=["review_status", "status", "updated_at"])
        _create_score_for_item(item, label, user, organization)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {
                "action": "request_changes",
                "label_comments": [
                    {
                        "label": str(label.id),
                        "annotator_id": str(user.id),
                        "notes": "Sentiment should be negative.",
                    }
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "label" in str(resp.data)
        assert "annotator_id" in str(resp.data)
        assert "notes" in str(resp.data)

    def test_label_feedback_requires_target_annotator(
        self,
        auth_client,
        queue_with_items,
        label,
    ):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {
                "action": "request_changes",
                "label_comments": [
                    {
                        "label_id": str(label.id),
                        "comment": "This needs a target annotator.",
                    }
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "target_annotator_id is required" in _result(resp)

    def test_review_rejects_item_without_submitted_annotations(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "request_changes", "notes": "Nothing to review yet."},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "at least one submitted annotation" in _result(resp)

    def test_review_comment_rejects_item_without_submitted_annotations(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "comment", "notes": "Nothing to review yet."},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "at least one submitted annotation" in _result(resp)

    def test_label_feedback_requires_target_annotator_submission(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        third_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        _create_score_for_item(item, label, third_user, organization)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {
                "action": "request_changes",
                "label_comments": [
                    {
                        "label_id": str(label.id),
                        "target_annotator_id": str(second_user.id),
                        "comment": "Second annotator has no answer yet.",
                    }
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "has not submitted this label" in _result(resp)

    def test_whole_item_request_changes_marks_item_rejected(
        self, auth_client, queue_with_items, second_user, organization
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        _create_score_for_item(item, label, second_user, organization)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "request_changes", "notes": "The whole item is wrong."},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.status == QueueItemStatus.IN_PROGRESS.value
        assert item.review_status == "rejected"

    def test_label_only_feedback_keeps_review_notes_private(
        self,
        auth_client,
        queue_with_items,
        second_user,
        label,
        organization,
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        _create_score_for_item(item, label, second_user, organization, value="negative")

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {
                "action": "request_changes",
                "label_comments": [
                    {
                        "label_id": str(label.id),
                        "target_annotator_id": str(second_user.id),
                        "comment": "This value should be negative.",
                    }
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.review_notes == "Reviewer requested changes on 1 annotation score."
        assert second_user.email not in item.review_notes
        assert "negative" not in item.review_notes

    def test_approve_can_store_optional_review_comment(
        self,
        auth_client,
        queue_with_items,
        second_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )
        _create_score_for_item(item, label, second_user, organization)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve", "notes": "Looks good."},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.COMPLETED.value
        assert item.review_status == "approved"
        comment = QueueItemReviewComment.objects.get(queue_item=item)
        assert comment.action == QueueItemReviewComment.ACTION_APPROVE
        assert comment.comment == "Looks good."

    def test_reviewer_cannot_approve_own_annotation(
        self,
        auth_client,
        queue_with_items,
        user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        _create_score_for_item(item, label, user, organization)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve"},
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "cannot review your own annotation" in _result(resp)

    def test_bulk_review_approves_selected_items(
        self,
        auth_client,
        queue_with_items,
        second_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        selected_ids = item_ids[:2]
        QueueItem.objects.filter(pk__in=selected_ids).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )
        for item in QueueItem.objects.filter(pk__in=selected_ids):
            _create_score_for_item(item, label, second_user, organization)

        resp = auth_client.post(
            bulk_review_url(queue_id),
            {
                "item_ids": [str(item_id) for item_id in selected_ids],
                "action": "approve",
                "notes": "Bulk approved.",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["reviewed"] == 2
        assert result["errors"] == []
        assert set(result["reviewed_item_ids"]) == {
            str(item_id) for item_id in selected_ids
        }
        approved = QueueItem.objects.filter(pk__in=selected_ids)
        assert {item.review_status for item in approved} == {"approved"}
        assert {item.status for item in approved} == {QueueItemStatus.COMPLETED.value}
        assert (
            QueueItemReviewComment.objects.filter(
                queue_item_id__in=selected_ids,
                action=QueueItemReviewComment.ACTION_APPROVE,
                comment="Bulk approved.",
            ).count()
            == 2
        )

    def test_bulk_review_requests_changes_selected_items(
        self,
        auth_client,
        queue_with_items,
        second_user,
        organization,
        workspace,
    ):
        queue_id, item_ids, label = queue_with_items
        selected_ids = item_ids[:2]
        QueueItem.objects.filter(pk__in=selected_ids).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )
        for item in QueueItem.objects.filter(pk__in=selected_ids):
            _create_score_for_item(item, label, second_user, organization)

        resp = auth_client.post(
            bulk_review_url(queue_id),
            {
                "item_ids": [str(item_id) for item_id in selected_ids],
                "action": "request_changes",
                "notes": "Bulk needs revision.",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["reviewed"] == 2
        assert result["errors"] == []
        assert set(result["reviewed_item_ids"]) == {
            str(item_id) for item_id in selected_ids
        }
        reviewed_items = QueueItem.objects.filter(pk__in=selected_ids)
        assert {item.review_status for item in reviewed_items} == {"rejected"}
        assert {item.status for item in reviewed_items} == {
            QueueItemStatus.IN_PROGRESS.value
        }
        assert {item.review_notes for item in reviewed_items} == {
            "Bulk needs revision."
        }
        comments = QueueItemReviewComment.objects.filter(
            queue_item_id__in=selected_ids,
            action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            comment="Bulk needs revision.",
            deleted=False,
        )
        assert comments.count() == 2
        assert set(comments.values_list("workspace_id", flat=True)) == {workspace.id}
        threads = QueueItemReviewThread.objects.filter(
            queue_item_id__in=selected_ids,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            blocking=True,
            status=QueueItemReviewThread.STATUS_OPEN,
            deleted=False,
        )
        assert threads.count() == 2
        assert set(threads.values_list("workspace_id", flat=True)) == {workspace.id}

    def test_bulk_review_respects_review_workflow_entitlement_denial(
        self,
        auth_client,
        queue_with_items,
        second_user,
        organization,
    ):
        if importlib.util.find_spec("ee.usage.services.entitlements") is None:
            pytest.skip("Enterprise entitlement module is not available.")

        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        _create_score_for_item(item, label, second_user, organization)

        with patch(
            "ee.usage.services.entitlements.Entitlements.check_feature",
            return_value=SimpleNamespace(
                allowed=False,
                reason="review workflow disabled",
            ),
        ):
            resp = auth_client.post(
                bulk_review_url(queue_id),
                {
                    "item_ids": [str(item.id)],
                    "action": "approve",
                    "notes": "Should not be saved.",
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "review workflow disabled" in str(_result(resp))
        item.refresh_from_db()
        assert item.review_status == "pending_review"
        assert item.status == QueueItemStatus.IN_PROGRESS.value
        assert item.review_notes in (None, "")
        assert not QueueItemReviewComment.objects.filter(
            queue_item=item,
            action=QueueItemReviewComment.ACTION_APPROVE,
        ).exists()

    def test_bulk_review_reports_own_annotations_without_approving_them(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        selected_ids = item_ids[:2]
        QueueItem.objects.filter(pk__in=selected_ids).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )
        own_item = QueueItem.objects.get(pk=selected_ids[0])
        other_item = QueueItem.objects.get(pk=selected_ids[1])
        _create_score_for_item(own_item, label, user, organization)
        _create_score_for_item(other_item, label, second_user, organization)

        resp = auth_client.post(
            bulk_review_url(queue_id),
            {
                "item_ids": [str(item_id) for item_id in selected_ids],
                "action": "approve",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["reviewed_item_ids"] == [str(other_item.id)]
        assert result["errors"] == [
            {
                "item_id": str(own_item.id),
                "error": "You cannot review your own annotation.",
            }
        ]
        own_item.refresh_from_db()
        other_item.refresh_from_db()
        assert own_item.review_status == "pending_review"
        assert other_item.review_status == "approved"

    def test_review_returns_next_pending_review_item(
        self,
        auth_client,
        queue_with_items,
        second_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        AnnotationQueue.objects.filter(pk=queue_id).update(requires_review=True)
        QueueItem.objects.filter(pk__in=item_ids[:2]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )
        first_item = QueueItem.objects.get(pk=item_ids[0])
        _create_score_for_item(first_item, label, second_user, organization)
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(created_at=base_time)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["next_item"]["id"] == str(item_ids[1])

    def test_approve_without_notes_clears_stale_request_change_summary(
        self,
        auth_client,
        queue_with_items,
        second_user,
        organization,
    ):
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.review_notes = "Old request-change feedback."
        item.save(update_fields=["status", "review_status", "review_notes"])
        _create_score_for_item(item, label, second_user, organization)

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.review_status == "approved"
        assert item.review_notes == ""

    def test_approve_resolves_non_blocking_request_change_feedback(
        self, auth_client, queue_with_items, user, second_user, label, organization
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        _create_score_for_item(item, label, second_user, organization)
        thread = QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_ITEM,
            blocking=False,
            status=QueueItemReviewThread.STATUS_OPEN,
            organization=organization,
        )

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        thread.refresh_from_db()
        assert thread.status == QueueItemReviewThread.STATUS_RESOLVED

    def test_approve_rejects_open_blocking_feedback(
        self, auth_client, queue_with_items, user, second_user, label, organization
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        _create_score_for_item(item, label, second_user, organization)
        QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_ITEM,
            blocking=True,
            status=QueueItemReviewThread.STATUS_OPEN,
            organization=organization,
        )

        resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve"},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "addressed before approval" in _result(resp)

    def test_resubmit_addresses_feedback_and_approval_resolves_it(
        self, auth_client, queue_with_items, second_user, organization, workspace
    ):
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, label = queue_with_items
        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        AnnotationQueue.objects.filter(pk=queue_id).update(requires_review=True)
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        _create_score_for_item(item, label, second_user, organization)

        review_resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {
                "action": "request_changes",
                "label_comments": [
                    {
                        "label_id": str(label.id),
                        "target_annotator_id": str(second_user.id),
                        "comment": "Fix this score.",
                    }
                ],
            },
            format="json",
        )
        assert review_resp.status_code == status.HTTP_200_OK
        thread = QueueItemReviewThread.objects.get(
            queue_item=item,
            blocking=True,
        )
        assert thread.status == QueueItemReviewThread.STATUS_OPEN

        submit_resp = second_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )
        assert submit_resp.status_code == status.HTTP_200_OK
        complete_resp = second_client.post(complete_url(queue_id, item_ids[0]))
        assert complete_resp.status_code == status.HTTP_200_OK

        item.refresh_from_db()
        thread.refresh_from_db()
        assert item.review_status == "pending_review"
        assert thread.status == QueueItemReviewThread.STATUS_ADDRESSED
        assert QueueItemReviewComment.objects.filter(
            thread=thread,
            action=QueueItemReviewComment.ACTION_ADDRESSED,
        ).exists()

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        detail = _result(detail_resp)
        assert detail["item"]["workflow_status"] == "resubmitted"
        assert detail["review_comments"][0]["thread_status"] == "addressed"

        approve_resp = auth_client.post(
            review_url(queue_id, item_ids[0]),
            {"action": "approve"},
            format="json",
        )
        assert approve_resp.status_code == status.HTTP_200_OK
        thread.refresh_from_db()
        assert thread.status == QueueItemReviewThread.STATUS_RESOLVED
        second_client.stop_workspace_injection()


@pytest.mark.django_db
class TestQueueItemDiscussion:
    def test_annotator_cannot_comment_on_item_assigned_to_another_user(
        self,
        queue_with_items,
        user,
        second_user,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        QueueItemAssignment.objects.create(queue_item_id=item_ids[0], user=user)

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp = second_client.post(
            discussion_url(queue_id, item_ids[0]),
            {"comment": "This item is not assigned to me."},
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        second_client.stop_workspace_injection()

    def test_discussion_comment_stores_mentions_on_non_blocking_thread(
        self,
        auth_client,
        queue_with_items,
        second_user,
        user,
        label,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "label_id": str(label.id),
                "comment": (
                    "Please double-check this label "
                    f"@[Annotator Two](user:{second_user.id})."
                ),
                "mentioned_user_ids": [str(second_user.id)],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert created["action"] == QueueItemReviewComment.ACTION_COMMENT
        assert created["label_id"] == str(label.id)
        assert created["blocking"] is False
        assert created["thread_status"] == QueueItemReviewThread.STATUS_OPEN
        assert created["reviewer_id"] == str(user.id)
        assert created["mentioned_users"] == [
            {
                "id": str(second_user.id),
                "name": second_user.name,
                "email": second_user.email,
            }
        ]

        comment = QueueItemReviewComment.objects.get(id=created["id"])
        assert comment.thread.blocking is False
        assert list(comment.mentioned_users.values_list("id", flat=True)) == [
            second_user.id
        ]

    def test_discussion_rejects_legacy_comment_aliases(
        self,
        auth_client,
        queue_with_items,
        second_user,
        label,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "content": "Please double-check this label.",
                "label": str(label.id),
                "mentions": [str(second_user.id)],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "content" in str(resp.data)
        assert "label" in str(resp.data)
        assert "mentions" in str(resp.data)

    def test_discussion_visibility_respects_target_annotator(
        self,
        auth_client,
        queue_with_items,
        second_user,
        third_user,
        workspace,
        user,
    ):
        queue_id, item_ids, _ = queue_with_items
        for member in (second_user, third_user):
            AnnotationQueueAnnotator.objects.update_or_create(
                queue_id=queue_id,
                user=member,
                defaults={
                    "role": AnnotatorRole.ANNOTATOR.value,
                    "roles": [AnnotatorRole.ANNOTATOR.value],
                },
            )

        resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "comment": "Only annotator two should see this.",
                "target_annotator_id": str(second_user.id),
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        second_resp = second_client.get(discussion_url(queue_id, item_ids[0]))
        assert second_resp.status_code == status.HTTP_200_OK
        second_comments = _result(second_resp)["review_comments"]
        assert [comment["comment"] for comment in second_comments] == [
            "Only annotator two should see this."
        ]
        second_client.stop_workspace_injection()

        third_client = WorkspaceAwareAPIClient()
        third_client.force_authenticate(user=third_user)
        third_client.set_workspace(workspace)
        third_resp = third_client.get(discussion_url(queue_id, item_ids[0]))
        assert third_resp.status_code == status.HTTP_200_OK
        assert _result(third_resp)["review_comments"] == []
        third_client.stop_workspace_injection()

    def test_discussion_comment_emails_mentioned_queue_member(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch(
                "model_hub.views.annotation_queues.send_message_to_channel"
            ) as send_message_to_channel,
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": f"Please check this @[Narda](user:{second_user.id}).",
                    "mentioned_user_ids": [str(second_user.id)],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        assert on_commit.call_count == 2
        send_message_to_channel.assert_not_called()
        email_helper.assert_called_once()
        subject, template, context, recipients = email_helper.call_args.args
        assert "added a comment" in subject
        assert template == "annotation_discussion_notification.html"
        assert recipients == [second_user.email]
        assert context["item_url"].endswith(
            f"/dashboard/annotations/queues/{queue_id}/annotate?itemId={item_ids[0]}"
        )

    def test_discussion_comment_does_not_run_websocket_broadcast_inline(
        self,
        auth_client,
        queue_with_items,
        settings,
    ):
        queue_id, item_ids, _ = queue_with_items
        settings.ANNOTATION_DISCUSSION_NOTIFICATIONS_ASYNC = True

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper"),
            patch("model_hub.views.annotation_queues.threading.Thread") as thread_cls,
            patch(
                "model_hub.views.annotation_queues.send_message_to_channel"
            ) as send_message_to_channel,
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {"comment": "Realtime delivery should not block the POST."},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        assert QueueItemReviewComment.objects.filter(
            queue_item_id=item_ids[0],
            comment="Realtime delivery should not block the POST.",
        ).exists()
        send_message_to_channel.assert_not_called()
        assert thread_cls.return_value.start.call_count == 1

    def test_discussion_comment_emails_typed_email_mention_without_payload_ids(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch(
                "model_hub.views.annotation_queues.send_message_to_channel"
            ) as send_message_to_channel,
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {"comment": f"Please check this @{second_user.email}."},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert created["mentioned_users"] == [
            {
                "id": str(second_user.id),
                "name": second_user.name,
                "email": second_user.email,
            }
        ]
        comment = QueueItemReviewComment.objects.get(id=created["id"])
        assert list(comment.mentioned_users.values_list("id", flat=True)) == [
            second_user.id
        ]
        assert on_commit.call_count == 2
        send_message_to_channel.assert_not_called()
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]

    def test_discussion_comment_emails_payload_email_mention(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": "Please check this from the payload mention.",
                    "mentioned_user_ids": [f"@{second_user.email.upper()}"],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert [user["email"] for user in created["mentioned_users"]] == [
            second_user.email
        ]
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]

    def test_discussion_comment_extracts_mention_from_comment_text(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": f"Please check this @{second_user.email}.",
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert [user["email"] for user in created["mentioned_users"]] == [
            second_user.email
        ]
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]

    def test_discussion_comment_emails_rich_mention_without_payload_ids(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": (
                        f"Please check this @[Annotator Two](user:{second_user.id})."
                    ),
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert [user["email"] for user in created["mentioned_users"]] == [
            second_user.email
        ]
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]

    def test_discussion_comment_email_resolves_case_insensitive_typed_email(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": (
                        f"Please check this @{second_user.email.upper()}, thanks."
                    ),
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert [user["email"] for user in created["mentioned_users"]] == [
            second_user.email
        ]
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]

    def test_discussion_comment_dedupes_mentions_and_suppresses_actor_email(
        self,
        auth_client,
        queue_with_items,
        second_user,
        third_user,
        user,
    ):
        queue_id, item_ids, _ = queue_with_items
        for member in (second_user, third_user):
            AnnotationQueueAnnotator.objects.update_or_create(
                queue_id=queue_id,
                user=member,
                defaults={
                    "role": AnnotatorRole.ANNOTATOR.value,
                    "roles": [AnnotatorRole.ANNOTATOR.value],
                },
            )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": (
                        f"Looping @{second_user.email} @{second_user.email} "
                        f"@{third_user.email} @{user.email}."
                    ),
                    "mentioned_user_ids": [str(second_user.id), str(third_user.id)],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert {member["email"] for member in created["mentioned_users"]} == {
            second_user.email,
            third_user.email,
            user.email,
        }
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert set(recipients) == {second_user.email, third_user.email}
        assert user.email not in recipients

    def test_discussion_comment_self_mention_does_not_email_actor(
        self,
        auth_client,
        queue_with_items,
        user,
    ):
        queue_id, item_ids, _ = queue_with_items

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {"comment": f"Reminder for myself @{user.email}."},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert [member["email"] for member in created["mentioned_users"]] == [
            user.email
        ]
        email_helper.assert_not_called()

    def test_discussion_comment_ignores_unknown_typed_email_mention(
        self,
        auth_client,
        queue_with_items,
    ):
        queue_id, item_ids, _ = queue_with_items

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {"comment": "Can @missing.person@example.com check this?"},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert created["mentioned_users"] == []
        email_helper.assert_not_called()

    def test_discussion_comment_rejects_invalid_mention_payload(
        self,
        auth_client,
        queue_with_items,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "comment": "Invalid mention payload.",
                "mentioned_user_ids": ["not-a-user-or-email"],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid mentioned user" in str(resp.data)

    def test_discussion_comment_rejects_non_list_mention_payload(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "comment": "Invalid mention payload shape.",
                "mentioned_user_ids": {"id": str(second_user.id)},
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["attr"] == "mentioned_user_ids"
        assert resp.data["code"] == "not_a_list"
        assert "mentioned_user_ids" in str(resp.data)

    def test_discussion_comment_rejects_non_member_uuid_mention(
        self,
        auth_client,
        queue_with_items,
        third_user,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "comment": "This user exists but is not a queue member.",
                "mentioned_user_ids": [str(third_user.id)],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Mentioned users must be members of this queue" in str(resp.data)

    def test_discussion_comment_rejects_too_many_mentions(
        self,
        auth_client,
        queue_with_items,
        second_user,
        third_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        for member in (second_user, third_user):
            AnnotationQueueAnnotator.objects.update_or_create(
                queue_id=queue_id,
                user=member,
                defaults={
                    "role": AnnotatorRole.ANNOTATOR.value,
                    "roles": [AnnotatorRole.ANNOTATOR.value],
                },
            )

        with patch(
            "model_hub.views.annotation_queues.MAX_MENTIONED_USERS_PER_COMMENT",
            1,
        ):
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": "Too many people mentioned.",
                    "mentioned_user_ids": [str(second_user.id), str(third_user.id)],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "At most 1 users can be mentioned" in str(resp.data)

    def test_discussion_comment_emails_target_annotator_without_inline_mention(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": "This feedback is for this annotator.",
                    "target_annotator_id": str(second_user.id),
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        created = _result(resp)["comment"]
        assert created["target_annotator_id"] == str(second_user.id)
        assert [user["email"] for user in created["mentioned_users"]] == [
            second_user.email
        ]
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]

    def test_discussion_comment_sends_rendered_email_with_locmem_backend(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        mail.outbox = []

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {"comment": f"Rendered email check @{second_user.email}."},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == [second_user.email]
        assert "added a comment" in message.subject
        assert "Rendered email check" in message.body
        html_body = (
            message.alternatives[0].content
            if hasattr(message.alternatives[0], "content")
            else message.alternatives[0][0]
        )
        assert (
            f"/dashboard/annotations/queues/{queue_id}/annotate?itemId={item_ids[0]}"
            in html_body
        )

    def test_discussion_email_delivery_does_not_block_real_email_backends(
        self,
        auth_client,
        queue_with_items,
        second_user,
        settings,
    ):
        queue_id, item_ids, _ = queue_with_items
        settings.EMAIL_BACKEND = "anymail.backends.mailgun.EmailBackend"
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.threading.Thread") as thread_cls,
            patch(
                "model_hub.views.annotation_queues.send_message_to_channel"
            ) as send_message_to_channel,
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "comment": f"Please check this @[Narda](user:{second_user.id}).",
                    "mentioned_user_ids": [str(second_user.id)],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        email_helper.assert_not_called()
        send_message_to_channel.assert_not_called()
        assert thread_cls.call_count == 2
        thread_names = {call.kwargs["name"] for call in thread_cls.call_args_list}
        assert any(
            name.startswith("annotation-discussion-broadcast-") for name in thread_names
        )
        assert any(
            name.startswith("annotation-discussion-email-") for name in thread_names
        )
        for call in thread_cls.call_args_list:
            assert call.kwargs["daemon"] is True
        assert thread_cls.return_value.start.call_count == 2

    def test_item_level_comment_emails_reviewers_and_managers_not_all_annotators(
        self,
        queue_with_items,
        second_user,
        third_user,
        user,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=user,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [
                    AnnotatorRole.MANAGER.value,
                    AnnotatorRole.REVIEWER.value,
                    AnnotatorRole.ANNOTATOR.value,
                ],
            },
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={
                "role": AnnotatorRole.REVIEWER.value,
                "roles": [AnnotatorRole.REVIEWER.value],
            },
        )

        from conftest import WorkspaceAwareAPIClient

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=second_user)
        annotator_client.set_workspace(workspace)
        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = annotator_client.post(
                discussion_url(queue_id, item_ids[0]),
                {"comment": "I need reviewer help on this item."},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert set(recipients) == {user.email, third_user.email}
        assert second_user.email not in recipients
        annotator_client.stop_workspace_injection()

    def test_discussion_reply_emails_thread_participants_only(
        self,
        auth_client,
        queue_with_items,
        second_user,
        third_user,
        user,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        for member, role in (
            (user, AnnotatorRole.MANAGER.value),
            (second_user, AnnotatorRole.ANNOTATOR.value),
            (third_user, AnnotatorRole.REVIEWER.value),
        ):
            AnnotationQueueAnnotator.objects.update_or_create(
                queue_id=queue_id,
                user=member,
                defaults={"role": role, "roles": [role]},
            )

        from conftest import WorkspaceAwareAPIClient

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=second_user)
        annotator_client.set_workspace(workspace)
        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            root_resp = annotator_client.post(
                discussion_url(queue_id, item_ids[0]),
                {"comment": "Can someone review this?"},
                format="json",
            )
            assert root_resp.status_code == status.HTTP_200_OK
            thread_id = _result(root_resp)["comment"]["thread_id"]
            email_helper.reset_mock()

            reply_resp = auth_client.post(
                discussion_url(queue_id, item_ids[0]),
                {
                    "thread_id": thread_id,
                    "comment": "Yes, I checked it.",
                },
                format="json",
            )

        assert reply_resp.status_code == status.HTTP_200_OK
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]
        annotator_client.stop_workspace_injection()

    def test_review_label_feedback_emails_target_annotator_only(
        self,
        auth_client,
        queue_with_items,
        organization,
        second_user,
        third_user,
        label,
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        for member, role in (
            (second_user, AnnotatorRole.ANNOTATOR.value),
            (third_user, AnnotatorRole.REVIEWER.value),
        ):
            AnnotationQueueAnnotator.objects.update_or_create(
                queue_id=queue_id,
                user=member,
                defaults={"role": role, "roles": [role]},
            )
        _create_score_for_item(item, label, second_user, organization, value="negative")

        with (
            patch(
                "model_hub.views.annotation_queues.transaction.on_commit"
            ) as on_commit,
            patch("model_hub.views.annotation_queues.email_helper") as email_helper,
            patch("model_hub.views.annotation_queues.send_message_to_channel"),
        ):
            on_commit.side_effect = lambda callback: callback()
            resp = auth_client.post(
                review_url(queue_id, item_ids[0]),
                {
                    "action": "request_changes",
                    "label_comments": [
                        {
                            "label_id": str(label.id),
                            "target_annotator_id": str(second_user.id),
                            "comment": "This score needs another look.",
                        }
                    ],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        email_helper.assert_called_once()
        *_, recipients = email_helper.call_args.args
        assert recipients == [second_user.email]
        assert third_user.email not in recipients

    def test_annotator_cannot_target_discussion_to_another_annotator(
        self,
        queue_with_items,
        second_user,
        third_user,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        for member in (second_user, third_user):
            AnnotationQueueAnnotator.objects.update_or_create(
                queue_id=queue_id,
                user=member,
                defaults={
                    "role": AnnotatorRole.ANNOTATOR.value,
                    "roles": [AnnotatorRole.ANNOTATOR.value],
                },
            )

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        resp = second_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "comment": "Trying to hide this from others.",
                "target_annotator_id": str(third_user.id),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        second_client.stop_workspace_injection()

    def test_non_author_annotator_cannot_resolve_or_reopen_discussion_thread(
        self,
        auth_client,
        queue_with_items,
        second_user,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        root_resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {"comment": "Creator-owned thread."},
            format="json",
        )
        assert root_resp.status_code == status.HTTP_200_OK
        thread_id = _result(root_resp)["comment"]["thread_id"]

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        resolve_resp = second_client.post(
            discussion_thread_url(queue_id, item_ids[0], thread_id, "resolve"),
            format="json",
        )
        reopen_resp = second_client.post(
            discussion_thread_url(queue_id, item_ids[0], thread_id, "reopen"),
            format="json",
        )

        assert resolve_resp.status_code == status.HTTP_403_FORBIDDEN
        assert reopen_resp.status_code == status.HTTP_403_FORBIDDEN
        second_client.stop_workspace_injection()

    def test_discussion_replies_reuse_thread_and_can_resolve_reopen(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )

        root_resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "comment": "Can someone verify this?",
                "mentioned_user_ids": [str(second_user.id)],
            },
            format="json",
        )
        assert root_resp.status_code == status.HTTP_200_OK
        root = _result(root_resp)["comment"]
        thread_id = root["thread_id"]

        reply_resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {
                "thread_id": thread_id,
                "comment": "Verified and fixed.",
                "mentioned_user_ids": [str(second_user.id)],
            },
            format="json",
        )
        assert reply_resp.status_code == status.HTTP_200_OK
        reply = _result(reply_resp)["comment"]
        assert reply["thread_id"] == thread_id
        assert QueueItemReviewComment.objects.filter(thread_id=thread_id).count() == 2

        resolve_resp = auth_client.post(
            discussion_thread_url(queue_id, item_ids[0], thread_id, "resolve"),
            format="json",
        )
        assert resolve_resp.status_code == status.HTTP_200_OK
        thread = QueueItemReviewThread.objects.get(id=thread_id)
        assert thread.status == QueueItemReviewThread.STATUS_RESOLVED
        assert _result(resolve_resp)["thread"]["status"] == "resolved"

        reopen_resp = auth_client.post(
            discussion_thread_url(queue_id, item_ids[0], thread_id, "reopen"),
            {"comment": "One more pass needed."},
            format="json",
        )
        assert reopen_resp.status_code == status.HTTP_200_OK
        thread.refresh_from_db()
        assert thread.status == QueueItemReviewThread.STATUS_REOPENED
        assert _result(reopen_resp)["comment"]["action"] == "reopen"

    def test_replying_to_resolved_discussion_thread_records_reopen_audit(
        self,
        auth_client,
        queue_with_items,
    ):
        queue_id, item_ids, _ = queue_with_items
        item_id = item_ids[0]
        root_resp = auth_client.post(
            discussion_url(queue_id, item_id),
            {"comment": "Original thread."},
            format="json",
        )
        assert root_resp.status_code == status.HTTP_200_OK
        thread_id = _result(root_resp)["comment"]["thread_id"]

        resolve_resp = auth_client.post(
            discussion_thread_url(queue_id, item_id, thread_id, "resolve"),
            format="json",
        )
        assert resolve_resp.status_code == status.HTTP_200_OK

        reply_resp = auth_client.post(
            discussion_url(queue_id, item_id),
            {
                "thread_id": thread_id,
                "comment": "Reply after resolved.",
            },
            format="json",
        )

        assert reply_resp.status_code == status.HTTP_200_OK
        thread = QueueItemReviewThread.objects.get(id=thread_id)
        assert thread.status == QueueItemReviewThread.STATUS_REOPENED
        actions = list(
            QueueItemReviewComment.objects.filter(thread_id=thread_id)
            .order_by("created_at")
            .values_list("action", flat=True)
        )
        assert actions == [
            QueueItemReviewComment.ACTION_COMMENT,
            QueueItemReviewComment.ACTION_RESOLVE,
            QueueItemReviewComment.ACTION_REOPEN,
            QueueItemReviewComment.ACTION_COMMENT,
        ]

    def test_non_author_reply_cannot_reopen_resolved_discussion_thread(
        self,
        auth_client,
        queue_with_items,
        second_user,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        item_id = item_ids[0]
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        root_resp = auth_client.post(
            discussion_url(queue_id, item_id),
            {"comment": "Creator-owned thread."},
            format="json",
        )
        assert root_resp.status_code == status.HTTP_200_OK
        thread_id = _result(root_resp)["comment"]["thread_id"]
        resolve_resp = auth_client.post(
            discussion_thread_url(queue_id, item_id, thread_id, "resolve"),
            format="json",
        )
        assert resolve_resp.status_code == status.HTTP_200_OK

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        reply_resp = second_client.post(
            discussion_url(queue_id, item_id),
            {
                "thread_id": thread_id,
                "comment": "Trying to reopen someone else's resolved thread.",
            },
            format="json",
        )

        assert reply_resp.status_code == status.HTTP_403_FORBIDDEN
        thread = QueueItemReviewThread.objects.get(id=thread_id)
        assert thread.status == QueueItemReviewThread.STATUS_RESOLVED
        assert QueueItemReviewComment.objects.filter(thread_id=thread_id).count() == 2
        second_client.stop_workspace_injection()

    def test_discussion_lifecycle_round_trips_through_annotate_detail(
        self,
        auth_client,
        queue_with_items,
        user,
    ):
        queue_id, item_ids, _ = queue_with_items
        item_id = item_ids[0]

        root_resp = auth_client.post(
            discussion_url(queue_id, item_id),
            {"comment": "Lifecycle check."},
            format="json",
        )
        assert root_resp.status_code == status.HTTP_200_OK
        root_comment = _result(root_resp)["comment"]
        thread_id = root_comment["thread_id"]

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_id))
        assert detail_resp.status_code == status.HTTP_200_OK
        detail = _result(detail_resp)
        thread = next(
            thread for thread in detail["review_threads"] if thread["id"] == thread_id
        )
        assert thread["status"] == QueueItemReviewThread.STATUS_OPEN
        assert thread["comments"][0]["comment"] == "Lifecycle check."

        resolve_resp = auth_client.post(
            discussion_thread_url(queue_id, item_id, thread_id, "resolve"),
            format="json",
        )
        assert resolve_resp.status_code == status.HTTP_200_OK

        resolved_detail_resp = auth_client.get(annotate_detail_url(queue_id, item_id))
        assert resolved_detail_resp.status_code == status.HTTP_200_OK
        resolved_detail = _result(resolved_detail_resp)
        resolved_thread = next(
            thread
            for thread in resolved_detail["review_threads"]
            if thread["id"] == thread_id
        )
        assert resolved_thread["status"] == QueueItemReviewThread.STATUS_RESOLVED
        assert any(
            comment["action"] == QueueItemReviewComment.ACTION_RESOLVE
            for comment in resolved_thread["comments"]
        )

        reopen_resp = auth_client.post(
            discussion_thread_url(queue_id, item_id, thread_id, "reopen"),
            {"comment": "Needs one more pass."},
            format="json",
        )
        assert reopen_resp.status_code == status.HTTP_200_OK

        reaction_resp = auth_client.post(
            discussion_reaction_url(queue_id, item_id, root_comment["id"]),
            {"emoji": "🚀"},
            format="json",
        )
        assert reaction_resp.status_code == status.HTTP_200_OK

        reopened_detail_resp = auth_client.get(annotate_detail_url(queue_id, item_id))
        assert reopened_detail_resp.status_code == status.HTTP_200_OK
        reopened_detail = _result(reopened_detail_resp)
        reopened_thread = next(
            thread
            for thread in reopened_detail["review_threads"]
            if thread["id"] == thread_id
        )
        assert reopened_thread["status"] == QueueItemReviewThread.STATUS_REOPENED
        assert any(
            comment["action"] == QueueItemReviewComment.ACTION_REOPEN
            and comment["comment"] == "Needs one more pass."
            for comment in reopened_thread["comments"]
        )
        root_comment_after_reaction = next(
            comment
            for comment in reopened_thread["comments"]
            if comment["id"] == root_comment["id"]
        )
        assert root_comment_after_reaction["reactions"] == [
            {
                "emoji": "🚀",
                "count": 1,
                "user_ids": [str(user.id)],
                "reacted_by_current_user": True,
            }
        ]

    def test_discussion_comment_author_can_edit_and_delete(
        self,
        auth_client,
        queue_with_items,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        create_resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {"comment": "Original note."},
            format="json",
        )
        assert create_resp.status_code == status.HTTP_200_OK
        comment_id = _result(create_resp)["comment"]["id"]

        edit_resp = auth_client.patch(
            discussion_comment_url(queue_id, item_ids[0], comment_id),
            {
                "comment": (
                    f"Edited note for @[Annotator Two](user:{second_user.id})."
                ),
                "mentioned_user_ids": [str(second_user.id)],
            },
            format="json",
        )

        assert edit_resp.status_code == status.HTTP_200_OK
        edited = _result(edit_resp)["comment"]
        assert edited["comment"].startswith("Edited note")
        assert edited["can_edit"] is True
        assert edited["can_delete"] is True
        assert edited["mentioned_users"] == [
            {
                "id": str(second_user.id),
                "name": second_user.name,
                "email": second_user.email,
            }
        ]

        delete_resp = auth_client.delete(
            discussion_comment_url(queue_id, item_ids[0], comment_id),
            format="json",
        )

        assert delete_resp.status_code == status.HTTP_200_OK
        assert _result(delete_resp)["review_comments"] == []
        comment = QueueItemReviewComment.all_objects.get(id=comment_id)
        assert comment.deleted is True
        assert comment.thread.deleted is True

    def test_discussion_comment_reactions_toggle_per_user(
        self,
        auth_client,
        queue_with_items,
        user,
    ):
        queue_id, item_ids, _ = queue_with_items
        root_resp = auth_client.post(
            discussion_url(queue_id, item_ids[0]),
            {"comment": "React to this."},
            format="json",
        )
        assert root_resp.status_code == status.HTTP_200_OK
        comment_id = _result(root_resp)["comment"]["id"]

        add_resp = auth_client.post(
            discussion_reaction_url(queue_id, item_ids[0], comment_id),
            {"emoji": "🧠"},
            format="json",
        )
        assert add_resp.status_code == status.HTTP_200_OK
        reactions = _result(add_resp)["comment"]["reactions"]
        assert reactions == [
            {
                "emoji": "🧠",
                "count": 1,
                "user_ids": [str(user.id)],
                "reacted_by_current_user": True,
            }
        ]

        remove_resp = auth_client.post(
            discussion_reaction_url(queue_id, item_ids[0], comment_id),
            {"emoji": "🧠"},
            format="json",
        )
        assert remove_resp.status_code == status.HTTP_200_OK
        assert _result(remove_resp)["comment"]["reactions"] == []

        invalid_resp = auth_client.post(
            discussion_reaction_url(queue_id, item_ids[0], comment_id),
            {"emoji": "done"},
            format="json",
        )
        assert invalid_resp.status_code == status.HTTP_400_BAD_REQUEST

        ascii_symbol_resp = auth_client.post(
            discussion_reaction_url(queue_id, item_ids[0], comment_id),
            {"emoji": "$"},
            format="json",
        )
        assert ascii_symbol_resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestSkipItem:
    def test_skip_returns_next(self, auth_client, queue_with_items):
        """Skip item returns next pending item."""
        queue_id, item_ids, _ = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=2)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[2]).update(created_at=base_time)

        resp = auth_client.post(skip_url(queue_id, item_ids[0]), format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["skipped_item_id"] == str(item_ids[0])
        assert result["next_item"]["id"] == str(item_ids[1])

    def test_skip_sets_skipped_status(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        auth_client.post(skip_url(queue_id, item_ids[0]), format="json")
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.SKIPPED.value

    def test_skip_rejects_completed_item(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.COMPLETED.value,
        )

        resp = auth_client.post(skip_url(queue_id, item_ids[0]), format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Completed items cannot be skipped" in _result(resp)

    def test_skip_rejects_non_member_even_if_workspace_admin(
        self, auth_client, dataset_rows, organization, workspace
    ):
        """Custom queues require explicit membership before skipping items."""
        from accounts.models.user import User
        from tfc.constants.roles import OrganizationRoles

        dataset, rows = dataset_rows
        manager = User.objects.create_user(
            email=f"skip-manager-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Skip Queue Manager",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        queue = AnnotationQueue.objects.create(
            name="Explicit Member Skip Queue",
            dataset=dataset,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            created_by=manager,
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue=queue,
            user=manager,
            deleted=False,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [AnnotatorRole.MANAGER.value],
            },
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=rows[0],
            organization=organization,
            workspace=workspace,
        )

        resp = auth_client.post(skip_url(queue.id, item.id), format="json")

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "Only queue members can skip items" in str(resp.data)
        item.refresh_from_db()
        assert item.status == QueueItemStatus.PENDING.value

    def test_skip_rejects_items_waiting_for_review(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        AnnotationQueue.objects.filter(pk=queue_id).update(requires_review=True)
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.post(skip_url(queue_id, item_ids[0]), format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot be skipped" in _result(resp)
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.IN_PROGRESS.value

    def test_skip_last_item(self, auth_client, queue_with_items):
        """Skip all items -- last one returns null next."""
        queue_id, item_ids, _ = queue_with_items
        base_time = timezone.now()
        for index, item_id in enumerate(item_ids):
            QueueItem.objects.filter(pk=item_id).update(
                created_at=base_time + timedelta(minutes=len(item_ids) - index)
            )
        for iid in item_ids:
            resp = auth_client.post(skip_url(queue_id, iid), format="json")
        assert _result(resp)["next_item"] is None

    def test_skip_clears_reservation(self, auth_client, queue_with_items, user):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.reserved_by = user
        item.reserved_at = timezone.now()
        item.reservation_expires_at = timezone.now() + timedelta(minutes=30)
        item.save()

        auth_client.post(skip_url(queue_id, item_ids[0]), format="json")
        item.refresh_from_db()
        assert item.reserved_by is None


@pytest.mark.django_db
class TestNextItem:
    def test_next_returns_latest_pending_by_default(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, _ = queue_with_items
        base_time = timezone.now()
        for index, item_id in enumerate(item_ids):
            QueueItem.objects.filter(pk=item_id).update(
                created_at=base_time + timedelta(minutes=index)
            )

        resp = auth_client.get(next_item_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["item"] is not None
        assert result["item"]["id"] == str(item_ids[-1])

    def test_next_returns_null_when_empty(self, auth_client, queue_id):
        """No items in queue, returns null."""
        resp = auth_client.get(next_item_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"] is None

    def test_next_skips_completed(self, auth_client, queue_with_items):
        """Next item skips completed items."""
        queue_id, item_ids, label = queue_with_items
        # Submit annotation + complete the first item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        resp = auth_client.get(next_item_url(queue_id))
        result = _result(resp)
        # Should not return the completed item
        assert str(result["item"]["id"]) != str(item_ids[0])

    def test_next_can_include_completed_items(self, auth_client, queue_with_items):
        """Annotators can opt into browsing completed items while navigating."""
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=2)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[2]).update(created_at=base_time)

        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        default_resp = auth_client.get(next_item_url(queue_id))
        assert default_resp.status_code == status.HTTP_200_OK
        assert _result(default_resp)["item"]["id"] == str(item_ids[1])

        include_resp = auth_client.get(
            next_item_url(queue_id), {"include_completed": "true"}
        )
        assert include_resp.status_code == status.HTTP_200_OK
        assert _result(include_resp)["item"]["id"] == str(item_ids[0])

    def test_completed_queue_navigation_defaults_to_completed_items(
        self, auth_client, queue_with_items
    ):
        """Completed queues browse completed rows without a frontend toggle."""
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=2)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[2]).update(created_at=base_time)

        for item_id in item_ids:
            auth_client.post(
                submit_annotations_url(queue_id, item_id),
                {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
                format="json",
            )
            auth_client.post(complete_url(queue_id, item_id), format="json")

        queue = AnnotationQueue.objects.get(pk=queue_id)
        assert queue.status == AnnotationQueueStatusChoices.COMPLETED.value

        next_resp = auth_client.get(next_item_url(queue_id))
        assert next_resp.status_code == status.HTTP_200_OK
        assert _result(next_resp)["item"]["id"] == str(item_ids[0])

        prev_resp = auth_client.get(
            next_item_url(queue_id), {"before": str(item_ids[1])}
        )
        assert prev_resp.status_code == status.HTTP_200_OK
        assert _result(prev_resp)["item"]["id"] == str(item_ids[0])

    def test_next_rejects_legacy_query_aliases(self, auth_client, queue_with_items):
        queue_id, _item_ids, _ = queue_with_items

        resp = auth_client.get(next_item_url(queue_id), {"includeCompleted": "true"})

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "includeCompleted" in str(resp.data)

    def test_previous_navigation_includes_completed_only_when_requested(
        self, auth_client, queue_with_items
    ):
        """Previous navigation follows the same completed-item toggle."""
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(created_at=base_time)
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        default_resp = auth_client.get(
            next_item_url(queue_id), {"before": str(item_ids[1])}
        )
        assert default_resp.status_code == status.HTTP_200_OK
        assert _result(default_resp)["item"] is None

        include_resp = auth_client.get(
            next_item_url(queue_id),
            {"before": str(item_ids[1]), "include_completed": "true"},
        )
        assert include_resp.status_code == status.HTTP_200_OK
        assert _result(include_resp)["item"]["id"] == str(item_ids[0])

    def test_previous_navigation_with_completed_toggle_respects_assignment_scope(
        self, auth_client, queue_with_items, user, second_user
    ):
        """Show completed must not let annotators browse another user's items."""
        queue_id, item_ids, _ = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.auto_assign = False
        queue.save(update_fields=["auto_assign", "updated_at"])
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=1),
            status=QueueItemStatus.COMPLETED.value,
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(created_at=base_time)
        QueueItemAssignment.objects.create(queue_item_id=item_ids[0], user=second_user)
        QueueItemAssignment.objects.create(queue_item_id=item_ids[1], user=user)

        resp = auth_client.get(
            next_item_url(queue_id),
            {"before": str(item_ids[1]), "include_completed": "true"},
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"] is None

    def test_next_can_filter_pending_review_items(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[1]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.get(
            next_item_url(queue_id), {"review_status": "pending_review"}
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"]["id"] == str(item_ids[1])

    def test_next_can_exclude_pending_review_items(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[1]).update(created_at=base_time)
        QueueItem.objects.filter(pk=item_ids[2]).update(
            created_at=base_time + timedelta(minutes=1)
        )

        resp = auth_client.get(
            next_item_url(queue_id), {"exclude_review_status": "pending_review"}
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"]["id"] == str(item_ids[2])

    def test_annotate_detail_adjacent_items_follow_completed_toggle(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=2)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[2]).update(created_at=base_time)

        auth_client.post(
            submit_annotations_url(queue_id, item_ids[1]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[1]), format="json")

        default_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert default_resp.status_code == status.HTTP_200_OK
        assert _result(default_resp)["next_item_id"] == str(item_ids[2])

        include_resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"include_completed": "true"},
        )
        assert include_resp.status_code == status.HTTP_200_OK
        assert _result(include_resp)["next_item_id"] == str(item_ids[1])

    def test_completed_queue_annotate_detail_uses_completed_adjacency_by_default(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        QueueItem.objects.filter(pk=item_ids[0]).update(
            created_at=base_time + timedelta(minutes=2)
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(pk=item_ids[2]).update(created_at=base_time)

        for item_id in item_ids:
            auth_client.post(
                submit_annotations_url(queue_id, item_id),
                {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
                format="json",
            )
            auth_client.post(complete_url(queue_id, item_id), format="json")

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert detail_resp.status_code == status.HTTP_200_OK
        detail = _result(detail_resp)
        assert detail["next_item_id"] == str(item_ids[1])
        assert detail["prev_item_id"] is None

    def test_next_routes_targeted_rework_only_to_target_annotator(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        third_user,
        workspace,
        label,
        organization,
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "rejected"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemReviewComment.objects.create(
            queue_item=item,
            reviewer=user,
            label=label,
            target_annotator=second_user,
            action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            comment="Only the second annotator should fix this score.",
            organization=organization,
        )
        QueueItemAssignment.objects.create(queue_item=item, user=second_user)
        QueueItemAssignment.objects.create(
            queue_item_id=item_ids[2],
            user=third_user,
        )

        from conftest import WorkspaceAwareAPIClient

        third_client = WorkspaceAwareAPIClient()
        third_client.force_authenticate(user=third_user)
        third_client.set_workspace(workspace)
        non_target_resp = third_client.get(next_item_url(queue_id))

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        target_resp = second_client.get(next_item_url(queue_id))

        assert non_target_resp.status_code == status.HTTP_200_OK
        assert _result(non_target_resp)["item"]["id"] == str(item_ids[2])
        assert target_resp.status_code == status.HTTP_200_OK
        assert _result(target_resp)["item"]["id"] == str(item_ids[0])
        third_client.stop_workspace_injection()
        second_client.stop_workspace_injection()

    def test_next_keeps_global_rework_visible_when_targeted_rework_coexists(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
        label,
        organization,
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "rejected"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_ITEM,
            status=QueueItemReviewThread.STATUS_OPEN,
            blocking=True,
            organization=organization,
            workspace=workspace,
        )
        QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            label=label,
            target_annotator=second_user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_SCORE,
            status=QueueItemReviewThread.STATUS_OPEN,
            blocking=True,
            organization=organization,
            workspace=workspace,
        )

        resp = auth_client.get(next_item_url(queue_id))

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"]["id"] == str(item_ids[0])

    def test_next_does_not_apply_annotator_rework_scope_to_reviewer(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
        label,
        organization,
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "rejected"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=user,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [
                    AnnotatorRole.MANAGER.value,
                    AnnotatorRole.REVIEWER.value,
                    AnnotatorRole.ANNOTATOR.value,
                ],
            },
        )
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemReviewThread.objects.create(
            queue_item=item,
            created_by=user,
            label=label,
            target_annotator=second_user,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            scope=QueueItemReviewThread.SCOPE_SCORE,
            status=QueueItemReviewThread.STATUS_OPEN,
            blocking=True,
            organization=organization,
            workspace=workspace,
        )

        resp = auth_client.get(next_item_url(queue_id))

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"]["id"] == str(item_ids[0])


@pytest.mark.django_db
class TestAnnotateDetail:
    def test_annotate_detail_returns_all_fields(self, auth_client, queue_with_items):
        """Annotate detail includes item, labels, annotations, progress."""
        queue_id, item_ids, label = queue_with_items
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert "item" in result
        assert "labels" in result
        assert "annotations" in result
        assert "progress" in result
        assert result["progress"]["total"] == 3

    def test_annotate_detail_user_progress_includes_navigation_position(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
    ):
        queue_id, item_ids, _ = queue_with_items
        base_time = timezone.now()
        for offset, item_id in enumerate(item_ids):
            QueueItem.objects.filter(pk=item_id).update(
                created_at=base_time - timedelta(minutes=offset)
            )
        QueueItemAssignment.objects.create(queue_item_id=item_ids[0], user=user)
        QueueItemAssignment.objects.create(
            queue_item_id=item_ids[1],
            user=second_user,
        )
        QueueItemAssignment.objects.create(queue_item_id=item_ids[2], user=user)

        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[2]))

        assert resp.status_code == status.HTTP_200_OK
        user_progress = _result(resp)["progress"]["user_progress"]
        assert user_progress["total"] == 2
        assert user_progress["current_position"] == 2

    def test_annotate_detail_user_progress_counts_pending_review_as_submitted(
        self, auth_client, queue_with_items, user
    ):
        queue_id, item_ids, label = queue_with_items
        AnnotationQueue.objects.filter(pk=queue_id).update(requires_review=True)
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0]), str(item_ids[1])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        submitted_item = QueueItem.objects.get(pk=item_ids[0])
        assert submitted_item.review_status == "pending_review"

        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[1]))

        assert resp.status_code == status.HTTP_200_OK
        user_progress = _result(resp)["progress"]["user_progress"]
        assert user_progress["total"] == 2
        assert user_progress["completed"] == 1

    def test_annotate_detail_includes_label_allow_notes(
        self, auth_client, queue_with_items
    ):
        """Workspace labels expose allow_notes so the UI can render per-label notes."""
        queue_id, item_ids, label = queue_with_items
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])

        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["labels"][0]["allow_notes"] is True

    def test_annotate_detail_includes_review_feedback(
        self, auth_client, queue_with_items, user
    ):
        """Rejected items expose reviewer feedback when annotators reopen them."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.review_status = "rejected"
        item.review_notes = "Please re-check the sentiment label."
        item.reviewed_by = user
        item.reviewed_at = timezone.now()
        item.save(
            update_fields=[
                "review_status",
                "review_notes",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ],
        )

        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert resp.status_code == status.HTTP_200_OK
        item_payload = _result(resp)["item"]
        assert item_payload["review_status"] == "rejected"
        assert item_payload["review_notes"] == "Please re-check the sentiment label."
        assert item_payload["reviewed_by_name"] == user.name
        assert item_payload["reviewed_at"] is not None

    def test_annotate_detail_filters_review_feedback_to_target_annotator(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
        label,
        organization,
    ):
        """Annotators only receive feedback targeted to them or global feedback."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        QueueItemReviewComment.objects.create(
            queue_item=item,
            reviewer=user,
            action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            comment="Global item feedback.",
            organization=organization,
        )
        QueueItemReviewComment.objects.create(
            queue_item=item,
            reviewer=user,
            label=label,
            target_annotator=user,
            action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            comment="Feedback for the first annotator.",
            organization=organization,
        )
        QueueItemReviewComment.objects.create(
            queue_item=item,
            reviewer=user,
            label=label,
            target_annotator=second_user,
            action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            comment="Feedback for the second annotator.",
            organization=organization,
        )

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp = second_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert resp.status_code == status.HTTP_200_OK
        comments = _result(resp)["review_comments"]
        comment_texts = {comment["comment"] for comment in comments}
        assert "Global item feedback." in comment_texts
        assert "Feedback for the second annotator." in comment_texts
        assert "Feedback for the first annotator." not in comment_texts
        second_client.stop_workspace_injection()

    def test_reviewer_can_scope_annotate_detail_to_selected_annotator(
        self, auth_client, queue_with_items, user, second_user, organization
    ):
        """Managers can request one annotator's answers instead of merged answers."""
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=item.dataset_row,
            label=label,
            annotator=user,
            value="positive",
            score_source="human",
            queue_item=item,
            organization=organization,
        )
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=item.dataset_row,
            label=label,
            annotator=second_user,
            value="negative",
            score_source="human",
            queue_item=item,
            organization=organization,
        )

        resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"annotator_id": str(second_user.id)},
        )

        assert resp.status_code == status.HTTP_200_OK
        annotations = _result(resp)["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["value"] == "negative"
        assert str(annotations[0]["annotator"]) == str(second_user.id)

    def test_annotate_detail_rejects_legacy_query_aliases(
        self, auth_client, queue_with_items, second_user
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"annotatorId": str(second_user.id), "includeCompleted": "true"},
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "annotatorId" in str(resp.data)
        assert "includeCompleted" in str(resp.data)

    def test_reviewer_annotate_detail_uses_own_draft_outside_review_mode(
        self, auth_client, queue_with_items, user, second_user, organization
    ):
        """A multi-role reviewer can still annotate without loading others' answers."""
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        _create_score_for_item(item, label, user, organization, value="positive")
        _create_score_for_item(item, label, second_user, organization, value="negative")

        annotate_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        review_resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"review_status": "pending_review"},
        )

        assert annotate_resp.status_code == status.HTTP_200_OK
        annotate_values = _result(annotate_resp)["annotations"]
        assert len(annotate_values) == 1
        assert str(annotate_values[0]["annotator"]) == str(user.id)
        assert review_resp.status_code == status.HTTP_200_OK
        assert len(_result(review_resp)["annotations"]) == 2

    def test_manager_review_view_can_see_all_annotations_without_review_required(
        self,
        queue_with_items,
        user,
        second_user,
        third_user,
        organization,
        workspace,
    ):
        """Managers need an explicit comparison mode even on non-review queues."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        assert queue.requires_review is False
        item = QueueItem.objects.get(pk=item_ids[0])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [AnnotatorRole.MANAGER.value],
            },
        )
        _create_score_for_item(item, label, user, organization, value="positive")
        _create_score_for_item(item, label, second_user, organization, value="negative")

        manager_client = WorkspaceAwareAPIClient()
        manager_client.force_authenticate(user=third_user)
        manager_client.set_workspace(workspace)

        own_resp = manager_client.get(annotate_detail_url(queue_id, item_ids[0]))
        review_resp = manager_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"view_mode": "review"},
        )
        scoped_resp = manager_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"view_mode": "review", "annotator_id": str(second_user.id)},
        )

        assert own_resp.status_code == status.HTTP_200_OK
        assert _result(own_resp)["annotations"] == []
        assert review_resp.status_code == status.HTTP_200_OK
        assert {ann["value"] for ann in _result(review_resp)["annotations"]} == {
            "positive",
            "negative",
        }
        assert scoped_resp.status_code == status.HTTP_200_OK
        scoped_annotations = _result(scoped_resp)["annotations"]
        assert len(scoped_annotations) == 1
        assert scoped_annotations[0]["value"] == "negative"
        manager_client.stop_workspace_injection()

    def test_reviewer_review_view_can_see_all_annotations_without_review_required(
        self,
        queue_with_items,
        user,
        second_user,
        third_user,
        organization,
        workspace,
    ):
        """Reviewer-only users can compare submissions on non-review queues."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        assert queue.requires_review is False
        item = QueueItem.objects.get(pk=item_ids[0])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.REVIEWER.value,
                "roles": [AnnotatorRole.REVIEWER.value],
            },
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        _create_score_for_item(item, label, user, organization, value="positive")
        _create_score_for_item(item, label, third_user, organization, value="negative")

        reviewer_client = WorkspaceAwareAPIClient()
        reviewer_client.force_authenticate(user=second_user)
        reviewer_client.set_workspace(workspace)

        own_resp = reviewer_client.get(annotate_detail_url(queue_id, item_ids[0]))
        review_resp = reviewer_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"view_mode": "review"},
        )
        legacy_view_mode_alias_resp = reviewer_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"mode": "submissions"},
        )
        include_all_resp = reviewer_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"include_all_annotations": "true"},
        )

        assert own_resp.status_code == status.HTTP_200_OK
        assert _result(own_resp)["annotations"] == []
        assert legacy_view_mode_alias_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "mode" in str(legacy_view_mode_alias_resp.data)
        for resp in (review_resp, include_all_resp):
            assert resp.status_code == status.HTTP_200_OK
            assert {ann["value"] for ann in _result(resp)["annotations"]} == {
                "positive",
                "negative",
            }
        reviewer_client.stop_workspace_injection()

    def test_annotator_cannot_force_review_view_to_see_other_annotations(
        self,
        queue_with_items,
        user,
        second_user,
        organization,
        workspace,
    ):
        """Annotators cannot bypass own-draft scoping with review query params."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        _create_score_for_item(item, label, user, organization, value="positive")
        _create_score_for_item(item, label, second_user, organization, value="negative")

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=second_user)
        annotator_client.set_workspace(workspace)

        forced_review_resp = annotator_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {
                "view_mode": "review",
                "include_all_annotations": "true",
                "annotator_id": str(user.id),
            },
        )

        assert forced_review_resp.status_code == status.HTTP_200_OK
        annotations = _result(forced_review_resp)["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["value"] == "negative"
        assert str(annotations[0]["annotator"]) == str(second_user.id)
        annotator_client.stop_workspace_injection()

    def test_reviewer_pending_review_detail_can_see_all_annotations(
        self,
        queue_with_items,
        user,
        second_user,
        third_user,
        organization,
        workspace,
    ):
        """The existing review_status=pending_review path still compares all."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.requires_review = True
        queue.save(update_fields=["requires_review", "updated_at"])
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.REVIEWER.value,
                "roles": [AnnotatorRole.REVIEWER.value],
            },
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        _create_score_for_item(item, label, user, organization, value="positive")
        _create_score_for_item(item, label, third_user, organization, value="negative")

        reviewer_client = WorkspaceAwareAPIClient()
        reviewer_client.force_authenticate(user=second_user)
        reviewer_client.set_workspace(workspace)

        resp = reviewer_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"review_status": "pending_review"},
        )

        assert resp.status_code == status.HTTP_200_OK
        assert {ann["value"] for ann in _result(resp)["annotations"]} == {
            "positive",
            "negative",
        }
        reviewer_client.stop_workspace_injection()

    def test_manager_submission_navigation_can_browse_assigned_completed_items(
        self,
        queue_with_items,
        second_user,
        third_user,
        workspace,
    ):
        """View-submissions navigation is full-queue, not manager's own assignment."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [AnnotatorRole.MANAGER.value],
            },
        )
        items = QueueItem.objects.filter(pk__in=item_ids)
        items.update(status=QueueItemStatus.COMPLETED.value)
        for item in items:
            QueueItemAssignment.objects.get_or_create(
                queue_item=item,
                user=second_user,
            )

        manager_client = WorkspaceAwareAPIClient()
        manager_client.force_authenticate(user=third_user)
        manager_client.set_workspace(workspace)

        default_resp = manager_client.get(
            next_item_url(queue_id),
            {"include_completed": "true"},
        )
        review_resp = manager_client.get(
            next_item_url(queue_id),
            {"view_mode": "review", "include_completed": "true"},
        )

        assert default_resp.status_code == status.HTTP_200_OK
        assert _result(default_resp)["item"] is None
        assert review_resp.status_code == status.HTTP_200_OK
        assert _result(review_resp)["item"]["id"] in {
            str(item_id) for item_id in item_ids
        }
        manager_client.stop_workspace_injection()

    def test_reviewer_submission_navigation_can_browse_assigned_review_items(
        self,
        queue_with_items,
        second_user,
        third_user,
        workspace,
    ):
        """Reviewer navigation is not scoped to the reviewer's assignments."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, _ = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.requires_review = True
        queue.save(update_fields=["requires_review", "updated_at"])
        item = QueueItem.objects.get(pk=item_ids[0])
        item.status = QueueItemStatus.IN_PROGRESS.value
        item.review_status = "pending_review"
        item.save(update_fields=["status", "review_status", "updated_at"])
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=third_user,
            defaults={
                "role": AnnotatorRole.REVIEWER.value,
                "roles": [AnnotatorRole.REVIEWER.value],
            },
        )
        QueueItemAssignment.objects.get_or_create(
            queue_item=item,
            user=second_user,
        )

        reviewer_client = WorkspaceAwareAPIClient()
        reviewer_client.force_authenticate(user=third_user)
        reviewer_client.set_workspace(workspace)

        resp = reviewer_client.get(
            next_item_url(queue_id),
            {"view_mode": "review", "review_status": "pending_review"},
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"]["id"] == str(item.id)
        reviewer_client.stop_workspace_injection()

    def test_annotator_cannot_use_review_navigation_to_browse_others_items(
        self,
        queue_with_items,
        user,
        second_user,
        workspace,
    ):
        """Non-reviewers stay assignment-scoped even with review query params."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, _ = queue_with_items
        AnnotationQueueAnnotator.objects.update_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={
                "role": AnnotatorRole.ANNOTATOR.value,
                "roles": [AnnotatorRole.ANNOTATOR.value],
            },
        )
        items = QueueItem.objects.filter(pk__in=item_ids)
        items.update(status=QueueItemStatus.COMPLETED.value)
        for item in items:
            QueueItemAssignment.objects.get_or_create(queue_item=item, user=user)

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=second_user)
        annotator_client.set_workspace(workspace)

        resp = annotator_client.get(
            next_item_url(queue_id),
            {
                "view_mode": "review",
                "include_completed": "true",
                "include_all_annotations": "true",
            },
        )

        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"] is None
        annotator_client.stop_workspace_injection()

    def test_reviewer_annotate_detail_rejects_invalid_annotator_selection(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"annotator_id": "not-a-uuid"},
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_annotate_detail_progress_counts(self, auth_client, queue_with_items):
        """Progress counts reflect current state."""
        queue_id, item_ids, label = queue_with_items
        # Complete one item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        # Check detail on second item
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[1]))
        result = _result(resp)
        assert result["progress"]["completed"] == 1
        assert result["progress"]["total"] == 3

    @pytest.mark.xfail(
        reason="Pre-existing: progress endpoint counts ALL queue items, not "
        "just items assigned to the requesting user. Test expects total=2 "
        "(items assigned to user) but gets 3 (all items in queue)."
    )
    def test_annotate_detail_user_progress(self, auth_client, queue_with_items, user):
        """Annotate detail includes user_progress for assigned items."""
        queue_id, item_ids, label = queue_with_items
        # Assign 2 items to user
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0]), str(item_ids[1])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        # Complete first item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        # Check detail on second item
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[1]))
        result = _result(resp)
        up = result["progress"]["user_progress"]
        assert up["total"] == 2
        assert up["completed"] == 1

    @pytest.mark.xfail(
        reason="Pre-existing backend bug (Team B E14): annotate_detail view "
        "doesn't acquire item reservation. Reservation system needs wiring "
        "in model_hub/views/annotation_queues.py:annotate_detail."
    )
    def test_annotate_detail_acquires_reservation(
        self, auth_client, queue_with_items, user
    ):
        """Opening annotate detail creates a reservation."""
        queue_id, item_ids, _ = queue_with_items
        auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.reserved_by == user
        assert item.reservation_expires_at is not None

    def test_annotate_detail_nonexistent(self, auth_client, queue_with_items):
        queue_id, _, _ = queue_with_items
        resp = auth_client.get(annotate_detail_url(queue_id, uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestReleaseReservation:
    def test_release_reservation(self, auth_client, queue_with_items, user):
        queue_id, item_ids, _ = queue_with_items
        # Acquire reservation
        reserve_resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"reserve": "true"},
        )
        assert reserve_resp.status_code == status.HTTP_200_OK
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.reserved_by == user
        assert item.reserved_at is not None
        assert item.reservation_expires_at is not None
        assert item.reservation_expires_at > item.reserved_at
        # Release
        resp = auth_client.post(release_url(queue_id, item_ids[0]), format="json")
        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.reserved_by is None
        assert item.reserved_at is None
        assert item.reservation_expires_at is None

    def test_cannot_release_another_users_reservation(
        self,
        auth_client,
        queue_with_items,
        second_user,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        auth_client.get(annotate_detail_url(queue_id, item_ids[0]), {"reserve": "true"})

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp = second_client.post(release_url(queue_id, item_ids[0]), format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.reserved_by is not None
        assert item.reserved_at is not None
        assert item.reservation_expires_at is not None

        second_client.stop_workspace_injection()


@pytest.mark.django_db
class TestReservationConflict:
    def test_reserve_true_conflict_returns_409(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
    ):
        """Another user cannot reserve an item while the reservation is active."""
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"reserve": "true"},
        )
        assert resp.status_code == status.HTTP_200_OK

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        QueueItemAssignment.objects.create(
            queue_item_id=item_ids[0],
            user=second_user,
        )

        resp2 = second_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"reserve": "true"},
        )
        assert resp2.status_code == status.HTTP_409_CONFLICT
        assert resp2.json().get("code") == "item_reserved"
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.reserved_by == user
        assert item.reserved_at is not None
        assert item.reservation_expires_at is not None
        second_client.stop_workspace_injection()

    def test_reserve_true_expired_reservation_can_transfer(
        self,
        auth_client,
        queue_with_items,
        second_user,
        workspace,
    ):
        """An expired reservation can be acquired by another user."""
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"reserve": "true"},
        )
        assert resp.status_code == status.HTTP_200_OK

        item = QueueItem.objects.get(pk=item_ids[0])
        QueueItem.objects.filter(pk=item.pk).update(
            reservation_expires_at=timezone.now() - timedelta(minutes=1),
            updated_at=timezone.now(),
        )

        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)
        QueueItemAssignment.objects.create(
            queue_item_id=item_ids[0],
            user=second_user,
        )

        resp2 = second_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"reserve": "true"},
        )
        assert resp2.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.reserved_by == second_user
        assert item.reserved_at is not None
        assert item.reservation_expires_at > timezone.now()
        second_client.stop_workspace_injection()

    @pytest.mark.xfail(
        reason="Pre-existing: reservation conflict check missing. Second user "
        "can open the same item without 400. Part of the broader reservation-"
        "system bug (Team B E14)."
    )
    def test_reservation_conflict_returns_400(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
    ):
        """Another user cannot open an item that is actively reserved."""
        queue_id, item_ids, _ = queue_with_items
        # First user acquires reservation
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK

        # Second user tries to open the same item
        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp2 = second_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp2.status_code == status.HTTP_400_BAD_REQUEST
        second_client.stop_workspace_injection()

    @pytest.mark.xfail(
        reason="Pre-existing: expired reservations don't transfer to a new "
        "user via annotate_detail. Same root cause as the rest of the "
        "reservation system gaps."
    )
    def test_expired_reservation_can_be_acquired(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
    ):
        """An expired reservation allows another user to acquire the item."""
        queue_id, item_ids, _ = queue_with_items
        # First user acquires reservation
        auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        # Manually expire the reservation
        item = QueueItem.objects.get(pk=item_ids[0])
        item.reservation_expires_at = timezone.now() - timedelta(minutes=1)
        item.save(update_fields=["reservation_expires_at", "updated_at"])

        # Second user can now acquire the item
        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp = second_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.reserved_by == second_user
        assert item.reservation_expires_at > timezone.now()
        second_client.stop_workspace_injection()


# ===========================================================================
# Queue item annotation import
# ===========================================================================


@pytest.mark.django_db
class TestImportAnnotations:
    def test_import_annotations_writes_score_for_queue_source(
        self,
        auth_client,
        queue_with_items,
        user,
        label,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            import_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": {"selected": ["positive"]},
                        "notes": "imported label note",
                        "score_source": "imported",
                    }
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["imported"] == 1

        item = QueueItem.objects.get(pk=item_ids[0])
        score = Score.no_workspace_objects.get(
            queue_item=item,
            dataset_row=item.dataset_row,
            label=label,
            annotator=user,
            deleted=False,
        )
        assert score.source_type == item.source_type
        assert score.value == {"selected": ["positive"]}
        assert score.notes == "imported label note"
        assert score.score_source == "imported"
        assert score.organization_id == item.organization_id
        assert score.workspace_id == item.workspace_id
        source_fk_ids = [
            score.dataset_row_id,
            score.trace_id,
            score.observation_span_id,
            score.trace_session_id,
            score.call_execution_id,
            score.prototype_run_id,
        ]
        assert source_fk_ids.count(None) == len(source_fk_ids) - 1
        assert source_fk_ids[0] == item.dataset_row_id

    def test_import_annotations_updates_existing_score_without_duplicates(
        self,
        auth_client,
        queue_with_items,
        user,
        label,
    ):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])

        first_resp = auth_client.post(
            import_annotations_url(queue_id, item.id),
            {
                "annotations": [
                    {"label_id": str(label.id), "value": {"selected": ["positive"]}}
                ]
            },
            format="json",
        )
        second_resp = auth_client.post(
            import_annotations_url(queue_id, item.id),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": {"selected": ["negative"]},
                        "notes": "updated import note",
                    }
                ]
            },
            format="json",
        )

        assert first_resp.status_code == status.HTTP_200_OK, first_resp.data
        assert second_resp.status_code == status.HTTP_200_OK, second_resp.data
        assert _result(first_resp)["imported"] == 1
        assert _result(second_resp)["imported"] == 1
        scores = Score.no_workspace_objects.filter(
            queue_item=item,
            dataset_row=item.dataset_row,
            label=label,
            annotator=user,
            deleted=False,
        )
        assert scores.count() == 1
        score = scores.get()
        assert score.value == {"selected": ["negative"]}
        assert score.notes == "updated import note"
        assert any(
            entry.get("value") == {"selected": ["positive"]}
            for entry in score.value_history
        )

    def test_import_annotations_ignores_label_not_attached_to_queue(
        self,
        auth_client,
        queue_with_items,
        label_b,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            import_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label_b.id),
                        "value": 4,
                        "score_source": "imported",
                    }
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["imported"] == 0
        assert not Score.no_workspace_objects.filter(
            queue_item_id=item_ids[0],
            label=label_b,
            deleted=False,
        ).exists()

    def test_import_annotations_rejects_invalid_value_for_queue_label(
        self,
        auth_client,
        queue_with_items,
        label,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            import_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": {"selected": ["not-configured"]},
                    }
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not Score.no_workspace_objects.filter(
            queue_item_id=item_ids[0],
            label=label,
            deleted=False,
        ).exists()

    def test_import_annotations_requires_annotations_list(
        self,
        auth_client,
        queue_with_items,
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            import_annotations_url(queue_id, item_ids[0]),
            {"annotations": []},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ===========================================================================
# Phase 3B -- Assignment & Distribution
# ===========================================================================


@pytest.mark.django_db
class TestAssignItems:
    def test_assign_items_to_user(self, auth_client, queue_with_items, user):
        """Assign items to a specific annotator."""
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0]), str(item_ids[1])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["assigned"] == 2
        for iid in item_ids[:2]:
            item = QueueItem.objects.get(pk=iid)
            assert item.assigned_to_id == user.id

    def test_assign_rejects_legacy_single_user_alias(
        self, auth_client, queue_with_items, user
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0])], "user_id": str(user.id)},
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "user_id" in str(resp.data)

    def test_unassign_items(self, auth_client, queue_with_items, user):
        """Unassign items by setting an empty assignment list."""
        queue_id, item_ids, _ = queue_with_items
        # Assign first
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        # Unassign
        resp = auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0])], "user_ids": [], "action": "set"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.assigned_to is None

    def test_auto_assign_queue_allows_manager_to_replace_all_annotators(
        self,
        auth_client,
        queue_with_items,
        second_user,
        third_user,
        workspace,
        label,
    ):
        """Managers can override the materialized 'all annotators' assignment."""
        from conftest import WorkspaceAwareAPIClient

        queue_id, item_ids, _ = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.auto_assign = True
        queue.save(update_fields=["auto_assign", "updated_at"])
        for annotator in (second_user, third_user):
            AnnotationQueueAnnotator.objects.update_or_create(
                queue_id=queue_id,
                user=annotator,
                defaults={
                    "role": AnnotatorRole.ANNOTATOR.value,
                    "roles": [AnnotatorRole.ANNOTATOR.value],
                },
            )
            QueueItemAssignment.objects.create(
                queue_item_id=item_ids[0],
                user=annotator,
            )

        resp = auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0])],
                "user_ids": [str(second_user.id)],
                "action": "set",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert set(
            QueueItemAssignment.objects.filter(
                queue_item_id=item_ids[0],
                deleted=False,
            ).values_list("user_id", flat=True)
        ) == {second_user.id}
        assert QueueItem.objects.get(pk=item_ids[0]).assigned_to_id == second_user.id

        third_client = WorkspaceAwareAPIClient()
        third_client.force_authenticate(user=third_user)
        third_client.set_workspace(workspace)
        denied = third_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )

        assert denied.status_code == status.HTTP_403_FORBIDDEN

    def test_custom_queue_assignment_allows_org_admin_without_queue_membership(
        self, auth_client, dataset_rows, organization, workspace
    ):
        """Org/workspace admins can manage custom queue assignments via admin access.

        ``auth_client`` is the org owner. Even without an explicit queue
        membership row, admin access grants manager-equivalent rights, so they
        may assign items on a queue created by someone else.
        """
        from accounts.models.user import User
        from tfc.constants.roles import OrganizationRoles

        dataset, rows = dataset_rows
        manager = User.objects.create_user(
            email=f"assignment-manager-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Assignment Queue Manager",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        queue = AnnotationQueue.objects.create(
            name="Explicit Member Assignment Queue",
            dataset=dataset,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            created_by=manager,
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue=queue,
            user=manager,
            deleted=False,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [AnnotatorRole.MANAGER.value],
            },
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=rows[0],
            organization=organization,
            workspace=workspace,
        )

        resp = auth_client.post(
            assign_url(queue.id),
            {
                "item_ids": [str(item.id)],
                "user_ids": [str(manager.id)],
                "action": "set",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.assigned_to_id == manager.id

    def test_custom_queue_assignment_allows_workspace_admin_without_queue_membership(
        self, dataset_rows, organization, workspace
    ):
        """Workspace admins (not org admins) can manage assignments via admin access.

        The acting user is only an org Member but a workspace admin, with no
        explicit queue membership. This exercises the
        ``WorkspaceMembership.level_or_legacy >= WORKSPACE_ADMIN`` branch of
        ``user_has_annotation_queue_admin_access``.
        """
        from accounts.models.user import User
        from conftest import WorkspaceAwareAPIClient
        from tfc.constants.roles import OrganizationRoles

        dataset, rows = dataset_rows
        manager = User.objects.create_user(
            email=f"ws-admin-assign-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Assignment Queue Owner",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        workspace_admin = _create_workspace_admin_user(
            organization, workspace, email_prefix="ws-admin-assign"
        )
        queue = AnnotationQueue.objects.create(
            name="Workspace Admin Assignment Queue",
            dataset=dataset,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            created_by=manager,
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue=queue,
            user=manager,
            deleted=False,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [AnnotatorRole.MANAGER.value],
            },
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=rows[0],
            organization=organization,
            workspace=workspace,
        )

        admin_client = WorkspaceAwareAPIClient()
        admin_client.force_authenticate(user=workspace_admin)
        admin_client.set_workspace(workspace)
        resp = admin_client.post(
            assign_url(queue.id),
            {
                "item_ids": [str(item.id)],
                "user_ids": [str(manager.id)],
                "action": "set",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.assigned_to_id == manager.id
        admin_client.stop_workspace_injection()

    def test_custom_queue_assignment_rejects_non_admin_non_member(
        self, dataset_rows, organization, workspace
    ):
        """Plain members without admin access or a queue manager role cannot assign."""
        from accounts.models.user import User
        from conftest import WorkspaceAwareAPIClient
        from tfc.constants.roles import OrganizationRoles

        dataset, rows = dataset_rows
        manager = User.objects.create_user(
            email=f"assignment-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Assignment Queue Owner",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        outsider = User.objects.create_user(
            email=f"assignment-outsider-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Assignment Queue Outsider",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        queue = AnnotationQueue.objects.create(
            name="Non Member Assignment Queue",
            dataset=dataset,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            created_by=manager,
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueAnnotator.objects.update_or_create(
            queue=queue,
            user=manager,
            deleted=False,
            defaults={
                "role": AnnotatorRole.MANAGER.value,
                "roles": [AnnotatorRole.MANAGER.value],
            },
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=rows[0],
            organization=organization,
            workspace=workspace,
        )

        outsider_client = WorkspaceAwareAPIClient()
        outsider_client.force_authenticate(user=outsider)
        outsider_client.set_workspace(workspace)
        resp = outsider_client.post(
            assign_url(queue.id),
            {
                "item_ids": [str(item.id)],
                "user_ids": [str(manager.id)],
                "action": "set",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert resp.data["type"] == "permission_error"
        assert resp.data["code"] == "permission_denied"
        item.refresh_from_db()
        assert item.assigned_to_id is None
        outsider_client.stop_workspace_injection()

    def test_assign_empty_item_ids(self, auth_client, queue_with_items):
        """Empty item_ids returns 400."""
        queue_id, _, _ = queue_with_items
        resp = auth_client.post(
            assign_url(queue_id),
            {"item_ids": [], "user_ids": [str(uuid.uuid4())], "action": "set"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_filter_assigned_to_me(self, auth_client, queue_with_items, user):
        """Filter items by assigned_to=me."""
        queue_id, item_ids, _ = queue_with_items
        # Assign first item to user
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        resp = auth_client.get(items_url(queue_id), {"assigned_to": "me"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["id"] == str(item_ids[0])

    def test_default_queue_self_assignment_creates_missing_membership(
        self, auth_client, dataset_rows, organization, workspace, user
    ):
        """Default queue admins can assign themselves even before a member row exists."""
        dataset, rows = dataset_rows
        queue = AnnotationQueue.objects.create(
            name="Default Workflow Assignment Queue",
            dataset=dataset,
            is_default=True,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            organization=organization,
            workspace=workspace,
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=rows[0],
            organization=organization,
            workspace=workspace,
        )
        assert not AnnotationQueueAnnotator.objects.filter(
            queue=queue,
            user=user,
            deleted=False,
        ).exists()

        resp = auth_client.post(
            assign_url(queue.id),
            {
                "item_ids": [str(item.id)],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.assigned_to_id == user.id

        membership = AnnotationQueueAnnotator.objects.get(queue=queue, user=user)
        assert membership.role == AnnotatorRole.MANAGER.value
        assert membership.normalized_roles == [
            AnnotatorRole.MANAGER.value,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.ANNOTATOR.value,
        ]

    def test_next_item_prefers_assigned(self, auth_client, queue_with_items, user):
        """Next-item returns assigned item first, even if it has a higher order."""
        queue_id, item_ids, _ = queue_with_items
        # Assign the last item to user
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[2])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        resp = auth_client.get(next_item_url(queue_id))
        result = _result(resp)
        # Should return the assigned item (item_ids[2]) over unassigned item_ids[0]
        assert result["item"]["id"] == str(item_ids[2])


# ===========================================================================
# Phase 3C -- Progress & Auto-complete
# ===========================================================================


@pytest.mark.django_db
class TestProgress:
    def test_progress_correct_counts(self, auth_client, queue_with_items):
        """Progress endpoint returns correct status counts."""
        queue_id, item_ids, label = queue_with_items
        # Complete 1, skip 1, leave 1 pending
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        auth_client.post(skip_url(queue_id, item_ids[1]), format="json")

        resp = auth_client.get(progress_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["total"] == 3
        assert result["completed"] == 1
        assert result["skipped"] == 1
        assert result["pending"] == 1
        assert result["progress_pct"] == pytest.approx(33.3, abs=0.1)

    def test_progress_counts_pending_review_as_in_review(
        self, auth_client, queue_with_items
    ):
        """Review-pending items are counted as their own workflow status."""
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )

        resp = auth_client.get(progress_url(queue_id))

        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["in_review"] == 1
        assert result["in_progress"] == 0

    def test_analytics_counts_pending_review_as_in_review(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, _ = queue_with_items
        QueueItem.objects.filter(pk=item_ids[0]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="pending_review",
        )
        QueueItem.objects.filter(pk=item_ids[1]).update(
            status=QueueItemStatus.IN_PROGRESS.value,
            review_status="not_started",
        )

        resp = auth_client.get(analytics_url(queue_id))

        assert resp.status_code == status.HTTP_200_OK
        breakdown = _result(resp)["status_breakdown"]
        assert breakdown["in_review"] == 2
        assert "in_progress" not in breakdown

    def test_analytics_annotator_performance_counts_completed_items_not_scores(
        self, auth_client, queue_with_items, label_b
    ):
        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label_b,
            order=1,
            required=False,
        )
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {"label_id": str(label.id), "value": "positive"},
                    {"label_id": str(label_b.id), "value": {"rating": 4}},
                ]
            },
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        resp = auth_client.get(analytics_url(queue_id))

        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["throughput"]["total_completed"] == 1
        assert result["status_breakdown"]["completed"] == 1
        assert result["annotator_performance"][0]["completed"] == 1

    def test_progress_empty_queue(self, auth_client, queue_id):
        """Progress on empty queue returns zeros."""
        resp = auth_client.get(progress_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["total"] == 0
        assert result["progress_pct"] == 0

    def test_numeric_labels_include_cohens_kappa(
        self,
        queue_with_items,
        user,
        second_user,
        organization,
        workspace,
    ):
        queue_id, item_ids, _ = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        label = AnnotationsLabels.objects.create(
            name="Numeric Agreement",
            type="numeric",
            settings={
                "min": 0,
                "max": 5,
                "step_size": 1,
                "display_type": "button",
            },
            organization=organization,
            workspace=workspace,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label,
            order=1,
            required=True,
        )

        for item_id, first_value, second_value in [
            (item_ids[0], 1, 1),
            (item_ids[1], 2, 3),
        ]:
            item = QueueItem.objects.get(pk=item_id)
            _create_score_for_item(
                item,
                label,
                user,
                organization,
                value=first_value,
            )
            _create_score_for_item(
                item,
                label,
                second_user,
                organization,
                value=second_value,
            )

        result = calculate_agreement(queue)
        label_result = result["labels"][str(label.id)]

        assert label_result["agreement_pct"] == 0.5
        assert label_result["cohens_kappa"] is not None
        assert -1.0 <= label_result["cohens_kappa"] <= 1.0

    def test_progress_per_annotator_stats(self, auth_client, queue_with_items, user):
        """Per-annotator stats show when items are assigned."""
        queue_id, item_ids, label = queue_with_items
        # Assign all to user
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(i) for i in item_ids],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        # Complete one
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)
        assert len(result["annotator_stats"]) == 1
        stats = result["annotator_stats"][0]
        assert stats["user_id"] == str(user.id)
        assert stats["completed"] == 1

    def test_progress_user_progress_with_assigned_items(
        self, auth_client, queue_with_items, user
    ):
        """user_progress shows only items assigned to the requesting user."""
        queue_id, item_ids, label = queue_with_items
        # Assign first 2 items to user, leave third unassigned
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0]), str(item_ids[1])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        # Complete the first (assigned) item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)

        # Overall: 3 total, 1 completed
        assert result["total"] == 3
        assert result["completed"] == 1

        # User progress: 2 assigned, 1 completed
        up = result["user_progress"]
        assert up["total"] == 2
        assert up["completed"] == 1
        assert up["pending"] == 1

    def test_progress_user_progress_counts_pending_review_as_submitted(
        self, auth_client, queue_with_items, user
    ):
        """Submitted-for-review items count toward the annotator's own progress."""
        queue_id, item_ids, label = queue_with_items
        AnnotationQueue.objects.filter(pk=queue_id).update(requires_review=True)
        auth_client.post(
            assign_url(queue_id),
            {
                "item_ids": [str(item_ids[0]), str(item_ids[1])],
                "user_ids": [str(user.id)],
                "action": "set",
            },
            format="json",
        )
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        submitted_item = QueueItem.objects.get(pk=item_ids[0])
        assert submitted_item.review_status == "pending_review"

        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)

        assert result["completed"] == 0
        assert result["in_review"] == 1
        up = result["user_progress"]
        assert up["total"] == 2
        assert up["completed"] == 1
        assert up["in_review"] == 1
        assert up["progress_pct"] == 50

    def test_progress_user_progress_no_assigned_items(
        self, auth_client, queue_with_items
    ):
        """user_progress returns zeros when user has no assigned items."""
        queue_id, _, _ = queue_with_items
        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)
        up = result["user_progress"]
        assert up["total"] == 0
        assert up["completed"] == 0
        assert up["progress_pct"] == 0


@pytest.mark.django_db
class TestAutoCompleteQueue:
    def test_auto_complete_when_all_done(
        self, auth_client, active_queue_id, dataset_rows, label, organization
    ):
        """Queue auto-completes when all items are completed."""
        queue_id = active_queue_id
        queue = AnnotationQueue.objects.get(pk=queue_id)

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.get(queue_id=queue_id, deleted=False)
        # Submit and complete
        auth_client.post(
            submit_annotations_url(queue_id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item.id), format="json")

        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.COMPLETED.value

    def test_no_auto_complete_with_pending_items(
        self, auth_client, active_queue_id, dataset_rows, label, organization
    ):
        """Queue stays active when pending items remain."""
        queue_id = active_queue_id
        queue = AnnotationQueue.objects.get(pk=queue_id)

        _, rows = dataset_rows
        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:2]
        ]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        first_item = (
            QueueItem.objects.filter(queue_id=queue_id, deleted=False)
            .order_by("order")
            .first()
        )
        auth_client.post(
            submit_annotations_url(queue_id, first_item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, first_item.id), format="json")

        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.ACTIVE.value


@pytest.mark.django_db
class TestMultiAnnotatorComplete:
    @pytest.mark.xfail(
        reason="Pre-existing: multi-annotator threshold logic doesn't keep "
        "item IN_PROGRESS when annotations_required > 1 and only one "
        "annotator has submitted. EE feature; needs review."
    )
    def test_complete_stays_in_progress_when_not_enough_annotators(
        self, auth_client, queue_id, dataset_rows, label, organization, user
    ):
        """With annotations_required=2, completing with 1 annotation keeps in_progress."""
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.annotations_required = 2
        queue.save(update_fields=["annotations_required"])
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.get(queue_id=queue_id, deleted=False)
        # One annotator submits
        auth_client.post(
            submit_annotations_url(queue_id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item.id), format="json")

        item.refresh_from_db()
        assert item.status == QueueItemStatus.IN_PROGRESS.value


# ===========================================================================
# Full workflow integration test
# ===========================================================================


@pytest.mark.django_db
class TestFullAnnotationWorkflow:
    def test_end_to_end_flow(self, auth_client, queue_with_items):
        """Full flow: annotate detail -> submit -> complete -> next -> skip."""
        queue_id, item_ids, label = queue_with_items
        base_time = timezone.now()
        for index, item_id in enumerate(item_ids):
            QueueItem.objects.filter(pk=item_id).update(
                created_at=base_time + timedelta(minutes=len(item_ids) - index)
            )

        # 1. Open annotate detail for first item
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK

        # 2. Submit annotations
        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        assert _result(resp)["submitted"] == 1

        # 3. Complete item
        resp = auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        result = _result(resp)
        assert result["next_item"] is not None
        next_id = result["next_item"]["id"]

        # 4. Skip the next item
        resp = auth_client.post(skip_url(queue_id, next_id), format="json")
        assert _result(resp)["skipped_item_id"] == next_id

        # 5. Verify progress
        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)
        assert result["completed"] == 1
        assert result["skipped"] == 1
        assert result["total"] == 3


# ===========================================================================
# Phase 3B -- Auto-Assignment Strategies
# ===========================================================================


@pytest.fixture
def second_user(organization):
    """Second user for assignment tests."""
    from accounts.models.user import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="annotator2@futureagi.com",
        password="testpassword123",
        name="Annotator Two",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


@pytest.fixture
def third_user(organization):
    """Third user for assignment tests."""
    from accounts.models.user import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="annotator3@futureagi.com",
        password="testpassword123",
        name="Annotator Three",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


@pytest.mark.django_db
class TestRoundRobinAssignment:
    @pytest.fixture
    def rr_queue(self, auth_client, user, second_user, label, organization):
        """Create a round-robin queue with 2 annotators."""
        resp = auth_client.post(
            QUEUE_URL,
            {
                "name": "RR Queue",
                "assignment_strategy": "round_robin",
                "label_ids": [str(label.id)],
            },
            format="json",
        )
        queue_id = resp.data["id"]
        auth_client.post(
            queue_status_url(queue_id), {"status": "active"}, format="json"
        )
        queue = AnnotationQueue.objects.get(pk=queue_id)
        # Creator (user) is auto-added as manager by the queue-create serializer.
        # Use get_or_create to avoid violating unique_active_queue_annotator.
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=user)
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=second_user)
        return queue_id, queue

    def test_round_robin_distributes_evenly(
        self, auth_client, rr_queue, dataset_rows, user, second_user
    ):
        """Round-robin assigns items alternating between annotators."""
        queue_id, queue = rr_queue
        _, rows = dataset_rows

        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:4]
        ]
        resp = auth_client.post(
            add_items_url(queue_id), {"items": items}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["added"] == 4

        created = list(
            QueueItem.objects.filter(queue_id=queue_id, deleted=False).order_by("order")
        )
        assert len(created) == 4
        # All items should have an assigned annotator
        for item in created:
            assert item.assigned_to_id is not None

        # Items should alternate between the two annotators
        assigned_to_ids = [item.assigned_to_id for item in created]
        assert assigned_to_ids[0] != assigned_to_ids[1]
        assert assigned_to_ids[0] == assigned_to_ids[2]
        assert assigned_to_ids[1] == assigned_to_ids[3]

    def test_round_robin_continues_offset(
        self, auth_client, rr_queue, dataset_rows, user, second_user
    ):
        """Round-robin considers existing assignments when adding more items."""
        queue_id, queue = rr_queue
        _, rows = dataset_rows

        # Add 1 item first
        items1 = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items1}, format="json")

        first_item = QueueItem.objects.filter(queue_id=queue_id, deleted=False).first()
        first_assignee = first_item.assigned_to_id

        # Add 1 more item
        items2 = [{"source_type": "dataset_row", "source_id": str(rows[1].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items2}, format="json")

        second_item = (
            QueueItem.objects.filter(queue_id=queue_id, deleted=False)
            .order_by("order")
            .last()
        )
        # Second item should go to the other annotator
        assert second_item.assigned_to_id != first_assignee

    def test_manual_strategy_does_not_assign(
        self, auth_client, queue_id, dataset_rows, user, label
    ):
        """Manual strategy leaves items unassigned."""
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.assignment_strategy = "manual"
        queue.save(update_fields=["assignment_strategy"])
        # Creator (user) is auto-added as manager by the queue-create serializer.
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=user)

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.filter(queue_id=queue_id, deleted=False).first()
        assert item.assigned_to_id is None


@pytest.mark.django_db
class TestLoadBalancedAssignment:
    @pytest.fixture
    def lb_queue(self, auth_client, user, second_user, third_user, label, organization):
        """Create a load-balanced queue with 3 annotators."""
        resp = auth_client.post(
            QUEUE_URL,
            {
                "name": "LB Queue",
                "assignment_strategy": "load_balanced",
                "label_ids": [str(label.id)],
            },
            format="json",
        )
        queue_id = resp.data["id"]
        auth_client.post(
            queue_status_url(queue_id), {"status": "active"}, format="json"
        )
        queue = AnnotationQueue.objects.get(pk=queue_id)
        # Creator (user) is auto-added as manager by the queue-create serializer.
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=user)
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=second_user)
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=third_user)
        return queue_id, queue

    def test_load_balanced_assigns_to_least_loaded(
        self, auth_client, lb_queue, dataset_rows, user, second_user, third_user
    ):
        """Load-balanced assigns each item to the annotator with fewest pending items."""
        queue_id, queue = lb_queue
        _, rows = dataset_rows

        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:3]
        ]
        resp = auth_client.post(
            add_items_url(queue_id), {"items": items}, format="json"
        )
        assert _result(resp)["added"] == 3

        created = list(
            QueueItem.objects.filter(queue_id=queue_id, deleted=False).order_by("order")
        )
        # All 3 items should be assigned
        assigned_ids = {item.assigned_to_id for item in created}
        assert len(assigned_ids) == 3  # Each annotator gets exactly 1

    def test_load_balanced_considers_existing_workload(
        self, auth_client, lb_queue, dataset_rows, user, second_user, third_user
    ):
        """Load-balanced correctly picks the least-loaded annotator for new items."""
        queue_id, queue = lb_queue
        _, rows = dataset_rows

        # Add 3 items (distributed evenly)
        items1 = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:3]
        ]
        auth_client.post(add_items_url(queue_id), {"items": items1}, format="json")

        # Now add 1 more — it should go to any annotator (all have 1 each)
        items2 = [{"source_type": "dataset_row", "source_id": str(rows[3].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items2}, format="json")

        created = list(
            QueueItem.objects.filter(queue_id=queue_id, deleted=False).order_by("order")
        )
        assert len(created) == 4
        # All should be assigned
        for item in created:
            assert item.assigned_to_id is not None

    @pytest.mark.xfail(
        reason="Pre-existing: load-balanced auto-assign attempts to assign "
        "even when the queue has zero annotators registered. Should leave "
        "items un-assigned and skip silently."
    )
    def test_no_annotators_leaves_unassigned(
        self, auth_client, queue_id, dataset_rows, label
    ):
        """Queue with no annotators leaves items unassigned regardless of strategy."""
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.assignment_strategy = "round_robin"
        queue.save(update_fields=["assignment_strategy"])
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
        # No annotators added

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.filter(queue_id=queue_id, deleted=False).first()
        assert item.assigned_to_id is None
