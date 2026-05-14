"""
Phase 1B – Annotation Queue CRUD API Tests.

Tests cover:
- List queues (with filters, search, counts, ordering)
- Create queues (with/without labels/annotators, validation)
- Retrieve queue
- Update queue (name, labels, annotators, status)
- Archive (soft delete) & Restore
- Status transitions
"""

import uuid
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.annotation_queues import AnnotationQueue, AnnotationQueueAnnotator
from model_hub.models.choices import AnnotationQueueStatusChoices, AnnotatorRole
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.ee_gating import EEResource, FeatureUnavailable
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.project import Project

QUEUE_URL = "/model-hub/annotation-queues/"
LABEL_URL = "/model-hub/annotations-labels/"
FULL_ACCESS_ROLES = {
    AnnotatorRole.MANAGER.value,
    AnnotatorRole.REVIEWER.value,
    AnnotatorRole.ANNOTATOR.value,
}


def queue_detail_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/"


def queue_restore_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/restore/"


def queue_status_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/update-status/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_queue(auth_client, **overrides):
    payload = {
        "name": overrides.pop("name", "Test Queue"),
        **overrides,
    }
    return auth_client.post(QUEUE_URL, payload, format="json")


def create_label_for_queue(auth_client, name="QL"):
    """Create a label via the labels API and return its ID."""
    payload = {
        "name": name,
        "type": "categorical",
        "settings": {
            "options": [{"label": "A"}, {"label": "B"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
    }
    auth_client.post(LABEL_URL, payload, format="json")
    resp = auth_client.get(LABEL_URL, {"search": name})
    return resp.data["results"][0]["id"]


def get_queue_id(auth_client, name=None):
    """Get the first queue ID from the list, optionally filtered by name."""
    params = {}
    if name:
        params["search"] = name
    resp = auth_client.get(QUEUE_URL, params)
    return resp.data["results"][0]["id"]


def assert_default_queue_full_access(queue_id, user):
    membership = AnnotationQueueAnnotator.objects.get(
        queue_id=queue_id,
        user=user,
        deleted=False,
    )
    assert membership.role == AnnotatorRole.MANAGER.value
    assert set(membership.roles) == FULL_ACCESS_ROLES


def create_workspace_admin_user(organization, workspace):
    user = User.objects.create_user(
        email=f"workspace-admin-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Workspace Admin",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
        organization_membership=org_membership,
    )
    return user


# ---------------------------------------------------------------------------
# 1.1 – List Queues
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListQueues:

    def test_list_all_queues_empty(self, auth_client):
        """TC-1: Empty list."""
        resp = auth_client.get(QUEUE_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 0

    def test_list_all_queues(self, auth_client):
        """TC-1: List populated queues."""
        create_queue(auth_client, name="Q1")
        create_queue(auth_client, name="Q2")
        resp = auth_client.get(QUEUE_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 2

    def test_filter_by_status(self, auth_client):
        """TC-2: Filter by status=draft."""
        create_queue(auth_client, name="Draft Q")
        resp = auth_client.get(QUEUE_URL, {"status": "draft"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1

    def test_search_by_name(self, auth_client):
        """TC-3: Search by name."""
        create_queue(auth_client, name="Review Items")
        create_queue(auth_client, name="Other Queue")
        resp = auth_client.get(QUEUE_URL, {"search": "review"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["name"] == "Review Items"

    def test_include_counts(self, auth_client):
        """TC-4: include_counts=true adds count fields."""
        create_queue(auth_client, name="Counted")
        resp = auth_client.get(QUEUE_URL, {"include_counts": "true"})
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data["results"][0]
        assert "label_count" in result

    def test_combined_filters(self, auth_client):
        """TC-5: Combined status + search."""
        create_queue(auth_client, name="Test Draft")
        create_queue(auth_client, name="Test Active")
        resp = auth_client.get(QUEUE_URL, {"status": "draft", "search": "test"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 2  # Both are draft by default

    def test_ordered_by_created_at_desc(self, auth_client):
        """TC-6: Most recent first."""
        create_queue(auth_client, name="First")
        create_queue(auth_client, name="Second")
        resp = auth_client.get(QUEUE_URL)
        results = resp.data["results"]
        assert results[0]["name"] == "Second"
        assert results[1]["name"] == "First"


# ---------------------------------------------------------------------------
# 1.2 – Create Queue
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateQueue:

    def test_create_with_name_only(self, auth_client):
        """TC-7: Create with name only, defaults to draft."""
        resp = create_queue(auth_client, name="Simple Queue")
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["status"] == "draft"

    def test_create_gives_creator_all_roles(self, auth_client, user):
        """Creator can manage, annotate, and review the queue by default."""
        resp = create_queue(auth_client, name="Creator Roles Queue")
        assert resp.status_code == status.HTTP_201_CREATED

        creator = next(
            a for a in resp.data["annotators"] if str(a["user_id"]) == str(user.id)
        )
        assert creator["role"] == AnnotatorRole.MANAGER.value
        assert set(creator["roles"]) == {
            AnnotatorRole.MANAGER.value,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.ANNOTATOR.value,
        }

        membership = AnnotationQueueAnnotator.objects.get(
            queue_id=resp.data["id"],
            user=user,
            deleted=False,
        )
        assert membership.role == AnnotatorRole.MANAGER.value
        assert set(membership.roles) == set(creator["roles"])

    def test_create_with_labels_and_annotators(self, auth_client, user):
        """TC-8: Create with label_ids and annotator_ids."""
        label_id = create_label_for_queue(auth_client, name="Queue Label")
        resp = create_queue(
            auth_client,
            name="Full Queue",
            label_ids=[str(label_id)],
            annotator_ids=[str(user.id)],
        )
        assert resp.status_code == status.HTTP_201_CREATED
        # Verify nested data
        data = resp.data
        assert len(data.get("labels", [])) > 0

    def test_create_with_description_instructions(self, auth_client):
        """TC-9: Create with description + instructions."""
        resp = create_queue(
            auth_client,
            name="Detailed Queue",
            description="A detailed queue",
            instructions="Please annotate carefully",
        )
        assert resp.status_code == status.HTTP_201_CREATED

    def test_create_missing_name(self, auth_client):
        """TC-10: Missing name returns 400."""
        resp = auth_client.post(QUEUE_URL, {"description": "no name"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_duplicate_name(self, auth_client):
        """TC-11: Duplicate name returns 400."""
        create_queue(auth_client, name="Unique")
        resp = create_queue(auth_client, name="Unique")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_checks_queue_plan_limit(
        self, auth_client, organization, user
    ):
        AnnotationQueue.objects.create(
            name="Existing Queue",
            organization=organization,
            created_by=user,
        )

        with (
            patch("tfc.ee_gating.is_oss", return_value=False),
            patch("tfc.ee_gating.check_ee_can_create") as check_can_create,
        ):
            resp = create_queue(auth_client, name="Plan Counted Queue")

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        check_can_create.assert_called_once_with(
            EEResource.ANNOTATION_QUEUES,
            org_id=str(organization.id),
            current_count=1,
        )

    def test_create_checks_queue_plan_limit_against_org_wide_queue_count(
        self, auth_client, organization, user, workspace
    ):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        AnnotationQueue.objects.create(
            name="Current Workspace Queue",
            organization=organization,
            workspace=workspace,
            created_by=user,
        )
        AnnotationQueue.objects.create(
            name="Other Workspace Queue",
            organization=organization,
            workspace=other_workspace,
            created_by=user,
        )

        set_workspace_context(
            workspace=workspace,
            organization=organization,
            user=user,
        )
        try:
            with (
                patch("tfc.ee_gating.is_oss", return_value=False),
                patch("tfc.ee_gating.check_ee_can_create") as check_can_create,
            ):
                resp = create_queue(auth_client, name="Plan Counted Org Queue")
        finally:
            clear_workspace_context()

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        check_can_create.assert_called_once_with(
            EEResource.ANNOTATION_QUEUES,
            org_id=str(organization.id),
            current_count=2,
        )

    def test_create_surfaces_queue_plan_limit_message(self, auth_client):
        with (
            patch("tfc.ee_gating.is_oss", return_value=False),
            patch(
                "tfc.ee_gating.check_ee_can_create",
                side_effect=FeatureUnavailable(
                    EEResource.ANNOTATION_QUEUES.value,
                    detail=(
                        "You've reached the 3 annotation queues limit "
                        "(3 existing). Archive unused queues or upgrade your plan."
                    ),
                    code="ENTITLEMENT_LIMIT",
                    metadata={
                        "resource": EEResource.ANNOTATION_QUEUES.value,
                        "current_usage": 3,
                        "limit": 3,
                    },
                ),
            ),
        ):
            resp = create_queue(auth_client, name="Denied Queue")

        assert resp.status_code == status.HTTP_402_PAYMENT_REQUIRED
        assert "reached the 3 annotation queues limit" in str(resp.data)
        assert resp.data["error"]["code"] == "ENTITLEMENT_LIMIT"
        assert resp.data["error"]["detail"]["current_usage"] == 3
        assert resp.data["error"]["detail"]["limit"] == 3
        assert not AnnotationQueue.objects.filter(name="Denied Queue").exists()

    def test_create_limit_message_explains_other_workspace_queues(
        self, auth_client, organization, user, workspace
    ):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        AnnotationQueue.objects.create(
            name="Current Workspace Queue",
            organization=organization,
            workspace=workspace,
            created_by=user,
        )
        AnnotationQueue.objects.create(
            name="Other Workspace Queue",
            organization=organization,
            workspace=other_workspace,
            created_by=user,
        )

        with (
            patch("tfc.ee_gating.is_oss", return_value=False),
            patch(
                "tfc.ee_gating.check_ee_can_create",
                side_effect=FeatureUnavailable(
                    EEResource.ANNOTATION_QUEUES.value,
                    detail="You've reached the 2 annotation queues limit",
                    code="ENTITLEMENT_LIMIT",
                    metadata={
                        "resource": EEResource.ANNOTATION_QUEUES.value,
                        "current_usage": 2,
                        "limit": 2,
                    },
                ),
            ),
        ):
            resp = create_queue(auth_client, name="Denied Cross Workspace Queue")

        assert resp.status_code == status.HTTP_402_PAYMENT_REQUIRED
        assert "2 existing queues" in resp.data["error"]["message"]
        assert "1 in the current workspace" in resp.data["error"]["message"]
        assert "1 in other workspaces" in resp.data["error"]["message"]
        assert resp.data["error"]["detail"]["current_usage"] == 2
        assert resp.data["error"]["detail"]["workspace_usage"] == 1
        assert resp.data["error"]["detail"]["other_workspace_usage"] == 1
        assert not AnnotationQueue.objects.filter(
            name="Denied Cross Workspace Queue"
        ).exists()

    def test_get_or_create_default_checks_queue_plan_limit(
        self, auth_client, organization, workspace
    ):
        project = Project.objects.create(
            name="Default Queue Limit Project",
            organization=organization,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )

        with (
            patch("tfc.ee_gating.is_oss", return_value=False),
            patch("tfc.ee_gating.check_ee_can_create") as check_can_create,
        ):
            resp = auth_client.post(
                f"{QUEUE_URL}get-or-create-default/",
                {"project_id": str(project.id)},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        check_can_create.assert_called_once_with(
            EEResource.ANNOTATION_QUEUES,
            org_id=str(organization.id),
            current_count=0,
        )

    def test_get_or_create_default_gives_requester_full_manager_access(
        self, auth_client, organization, workspace, user
    ):
        project = Project.objects.create(
            name="Default Queue Manager Project",
            organization=organization,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )

        with patch("tfc.ee_gating.is_oss", return_value=True):
            resp = auth_client.post(
                f"{QUEUE_URL}get-or-create-default/",
                {"project_id": str(project.id)},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        queue_id = resp.data["result"]["queue"]["id"]
        assert resp.data["result"]["action"] == "created"
        assert_default_queue_full_access(queue_id, user)

    def test_get_or_create_default_repairs_existing_queue_membership(
        self, auth_client, organization, workspace, user
    ):
        project = Project.objects.create(
            name="Existing Default Queue Manager Project",
            organization=organization,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        queue = AnnotationQueue.objects.create(
            name="Existing Default Queue",
            description="Existing project default queue",
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            organization=organization,
            workspace=workspace,
            project=project,
            is_default=True,
        )

        with patch("tfc.ee_gating.is_oss", return_value=True):
            resp = auth_client.post(
                f"{QUEUE_URL}get-or-create-default/",
                {"project_id": str(project.id)},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["result"]["queue"]["id"] == str(queue.id)
        assert resp.data["result"]["action"] == "fetched"
        assert_default_queue_full_access(queue.id, user)


# ---------------------------------------------------------------------------
# 1.3 – Retrieve Queue
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetrieveQueue:

    def test_get_queue_by_id(self, auth_client):
        """TC-12: Retrieve includes nested labels/annotators."""
        create_queue(auth_client, name="Retrievable")
        queue_id = get_queue_id(auth_client, "Retrievable")
        resp = auth_client.get(queue_detail_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "Retrievable"

    def test_get_nonexistent_queue(self, auth_client):
        """TC-13: Non-existent queue returns 404."""
        resp = auth_client.get(queue_detail_url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# 1.4 – Update Queue
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateQueue:

    def test_update_name(self, auth_client):
        """TC-14: Update queue name."""
        create_queue(auth_client, name="Original")
        queue_id = get_queue_id(auth_client, "Original")
        resp = auth_client.patch(
            queue_detail_url(queue_id), {"name": "Updated"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_update_labels(self, auth_client):
        """TC-15: Update labels (sync)."""
        label_id1 = create_label_for_queue(auth_client, name="L1")
        label_id2 = create_label_for_queue(auth_client, name="L2")
        create_queue(auth_client, name="Label Queue", label_ids=[str(label_id1)])
        queue_id = get_queue_id(auth_client, "Label Queue")
        # Replace label_ids
        resp = auth_client.patch(
            queue_detail_url(queue_id),
            {"label_ids": [str(label_id2)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_org_owner_can_manage_queue_without_queue_membership(
        self, auth_client, organization, workspace, user
    ):
        queue = AnnotationQueue.objects.create(
            name="Owner Managed Queue",
            organization=organization,
            workspace=workspace,
            created_by=None,
        )
        assert not AnnotationQueueAnnotator.objects.filter(
            queue=queue,
            user=user,
            deleted=False,
        ).exists()

        detail = auth_client.get(queue_detail_url(queue.id))
        assert detail.status_code == status.HTTP_200_OK, detail.data
        assert set(detail.data["viewer_roles"]) == FULL_ACCESS_ROLES

        resp = auth_client.patch(
            queue_detail_url(queue.id),
            {"name": "Owner Updated Queue"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["name"] == "Owner Updated Queue"

    def test_workspace_admin_can_manage_queue_without_queue_membership(
        self, api_client, organization, workspace
    ):
        workspace_admin = create_workspace_admin_user(organization, workspace)
        api_client.force_authenticate(user=workspace_admin)
        api_client.set_workspace(workspace)
        queue = AnnotationQueue.objects.create(
            name="Workspace Admin Managed Queue",
            organization=organization,
            workspace=workspace,
            created_by=None,
        )

        detail = api_client.get(queue_detail_url(queue.id))
        assert detail.status_code == status.HTTP_200_OK, detail.data
        assert set(detail.data["viewer_roles"]) == FULL_ACCESS_ROLES

        resp = api_client.patch(
            queue_detail_url(queue.id),
            {"name": "Workspace Admin Updated Queue"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["name"] == "Workspace Admin Updated Queue"

    def test_org_owner_keeps_manager_access_with_limited_queue_membership(
        self, auth_client, organization, workspace, user
    ):
        queue = AnnotationQueue.objects.create(
            name="Owner Limited Membership Queue",
            organization=organization,
            workspace=workspace,
            created_by=None,
        )
        AnnotationQueueAnnotator.objects.create(
            queue=queue,
            user=user,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )

        detail = auth_client.get(queue_detail_url(queue.id))
        assert detail.status_code == status.HTTP_200_OK, detail.data
        assert set(detail.data["viewer_roles"]) == FULL_ACCESS_ROLES

        resp = auth_client.patch(
            queue_detail_url(queue.id),
            {"name": "Owner Limited Membership Updated"},
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["name"] == "Owner Limited Membership Updated"

    def test_update_annotators(self, auth_client, user):
        """TC-16: Update annotators."""
        create_queue(auth_client, name="Ann Queue")
        queue_id = get_queue_id(auth_client, "Ann Queue")
        resp = auth_client.patch(
            queue_detail_url(queue_id),
            {"annotator_ids": [str(user.id)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_update_annotator_multiple_roles(self, auth_client, user):
        """Queue settings can store multiple roles for one member."""
        create_queue(auth_client, name="Multi Role Queue")
        queue_id = get_queue_id(auth_client, "Multi Role Queue")
        resp = auth_client.patch(
            queue_detail_url(queue_id),
            {
                "annotator_ids": [str(user.id)],
                "annotator_roles": {
                    str(user.id): [
                        AnnotatorRole.MANAGER.value,
                        AnnotatorRole.ANNOTATOR.value,
                    ]
                },
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        member = next(
            a for a in resp.data["annotators"] if str(a["user_id"]) == str(user.id)
        )
        assert member["role"] == AnnotatorRole.MANAGER.value
        assert member["roles"] == [
            AnnotatorRole.MANAGER.value,
            AnnotatorRole.ANNOTATOR.value,
        ]

    def test_update_status_via_patch(self, auth_client):
        """TC-17: Update status via PATCH (not transition endpoint)."""
        create_queue(auth_client, name="Status Queue")
        queue_id = get_queue_id(auth_client, "Status Queue")
        # PATCH status directly — this should work via serializer
        resp = auth_client.patch(
            queue_detail_url(queue_id),
            {"status": "active"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# 1.4b – Multi-role data backfill
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnnotationQueueRoleBackfill:

    def test_backfill_command_upgrades_existing_creator_manager(
        self, organization, workspace, user
    ):
        queue = AnnotationQueue.objects.create(
            name=f"Legacy Creator Queue {uuid.uuid4()}",
            organization=organization,
            workspace=workspace,
            created_by=user,
        )
        membership = AnnotationQueueAnnotator.objects.create(
            queue=queue,
            user=user,
            role=AnnotatorRole.MANAGER.value,
            roles=[],
        )

        out = StringIO()
        call_command("backfill_annotation_queue_roles", stdout=out)

        membership.refresh_from_db()
        assert membership.role == AnnotatorRole.MANAGER.value
        assert membership.roles == [
            AnnotatorRole.MANAGER.value,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.ANNOTATOR.value,
        ]
        assert "1 memberships updated" in out.getvalue()

    def test_backfill_command_creates_missing_creator_membership(
        self, organization, workspace, user
    ):
        queue = AnnotationQueue.objects.create(
            name=f"Missing Creator Membership Queue {uuid.uuid4()}",
            organization=organization,
            workspace=workspace,
            created_by=user,
        )

        out = StringIO()
        call_command("backfill_annotation_queue_roles", stdout=out)

        membership = AnnotationQueueAnnotator.objects.get(
            queue=queue,
            user=user,
            deleted=False,
        )
        assert membership.role == AnnotatorRole.MANAGER.value
        assert membership.roles == [
            AnnotatorRole.MANAGER.value,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.ANNOTATOR.value,
        ]
        assert "1 creator memberships created" in out.getvalue()


# ---------------------------------------------------------------------------
# 1.5 – Archive & Restore
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestArchiveAndRestoreQueue:

    def test_archive_queue(self, auth_client):
        """TC-18: Delete (archive) queue."""
        create_queue(auth_client, name="To Archive")
        queue_id = get_queue_id(auth_client, "To Archive")
        resp = auth_client.delete(queue_detail_url(queue_id))
        assert resp.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT)

    def test_archived_queue_hidden(self, auth_client):
        """TC-19: Archived queue not in list."""
        create_queue(auth_client, name="Hidden Queue")
        queue_id = get_queue_id(auth_client, "Hidden Queue")
        auth_client.delete(queue_detail_url(queue_id))
        resp = auth_client.get(QUEUE_URL)
        ids = [str(r["id"]) for r in resp.data["results"]]
        assert str(queue_id) not in ids

    def test_restore_archived_queue(self, auth_client):
        """TC-20: Restore archived queue."""
        create_queue(auth_client, name="Restorable")
        queue_id = get_queue_id(auth_client, "Restorable")
        auth_client.delete(queue_detail_url(queue_id))
        resp = auth_client.post(queue_restore_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK

    def test_restore_nonexistent(self, auth_client):
        """TC-21: Restore non-existent returns 404."""
        resp = auth_client.post(queue_restore_url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# 1.6 – Status Transitions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStatusTransitions:

    def _create_and_get_id(self, auth_client, name="Trans Q"):
        create_queue(auth_client, name=name)
        return get_queue_id(auth_client, name)

    def test_draft_to_active(self, auth_client):
        """TC-22: draft → active."""
        qid = self._create_and_get_id(auth_client, "D2A")
        resp = auth_client.post(
            queue_status_url(qid), {"status": "active"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_active_to_paused(self, auth_client):
        """TC-23: active → paused."""
        qid = self._create_and_get_id(auth_client, "A2P")
        auth_client.post(queue_status_url(qid), {"status": "active"}, format="json")
        resp = auth_client.post(
            queue_status_url(qid), {"status": "paused"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_active_to_completed(self, auth_client):
        """TC-24: active → completed."""
        qid = self._create_and_get_id(auth_client, "A2C")
        auth_client.post(queue_status_url(qid), {"status": "active"}, format="json")
        resp = auth_client.post(
            queue_status_url(qid), {"status": "completed"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_paused_to_active(self, auth_client):
        """TC-25: paused → active."""
        qid = self._create_and_get_id(auth_client, "P2A")
        auth_client.post(queue_status_url(qid), {"status": "active"}, format="json")
        auth_client.post(queue_status_url(qid), {"status": "paused"}, format="json")
        resp = auth_client.post(
            queue_status_url(qid), {"status": "active"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_completed_to_active(self, auth_client):
        """TC-26: completed → active."""
        qid = self._create_and_get_id(auth_client, "C2A")
        auth_client.post(queue_status_url(qid), {"status": "active"}, format="json")
        auth_client.post(queue_status_url(qid), {"status": "completed"}, format="json")
        resp = auth_client.post(
            queue_status_url(qid), {"status": "active"}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_draft_to_paused_invalid(self, auth_client):
        """TC-27: draft → paused is invalid."""
        qid = self._create_and_get_id(auth_client, "D2P")
        resp = auth_client.post(
            queue_status_url(qid), {"status": "paused"}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_draft_to_completed_invalid(self, auth_client):
        """TC-28: draft → completed is invalid."""
        qid = self._create_and_get_id(auth_client, "D2C")
        resp = auth_client.post(
            queue_status_url(qid), {"status": "completed"}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_status(self, auth_client):
        """TC-29: Missing status in request returns 400."""
        qid = self._create_and_get_id(auth_client, "No Status")
        resp = auth_client.post(queue_status_url(qid), {}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
