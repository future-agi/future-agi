"""Queue lifecycle and label mutations require queue management permission."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AutomationRule,
    QueueItem,
    QueueItemAssignment,
    QueueItemNote,
    QueueItemReviewComment,
    QueueItemReviewThread,
)
from model_hub.models.choices import AnnotatorRole, QueueItemSourceType
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db


def _workspace_member(organization, workspace):
    member = User.objects.create_user(
        email=f"queue-member-{uuid.uuid4().hex}@futureagi.com",
        password="testpassword123",
        name="Queue workspace member",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    return member


@pytest.fixture
def queue_state(organization, workspace, user):
    manager = _workspace_member(organization, workspace)
    member = _workspace_member(organization, workspace)
    scope = {"organization": organization, "workspace": workspace}
    queue = AnnotationQueue.objects.create(
        name="Manager-owned queue", created_by=user, status="active", **scope
    )
    manager_role = AnnotationQueueAnnotator.objects.create(
        queue=queue,
        user=manager,
        role=AnnotatorRole.MANAGER.value,
        roles=[AnnotatorRole.MANAGER.value],
    )
    dataset = Dataset.objects.create(name="Queue source", **scope)
    row = Row.objects.create(dataset=dataset, order=1)
    item = QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.DATASET_ROW.value,
        dataset_row=row,
        assigned_to=member,
        **scope,
    )
    assignment = QueueItemAssignment.objects.create(queue_item=item, user=member)
    note = QueueItemNote.objects.create(
        queue_item=item, annotator=member, notes="Keep this note", **scope
    )
    thread = QueueItemReviewThread.objects.create(
        queue_item=item, created_by=manager, **scope
    )
    comment = QueueItemReviewComment.objects.create(
        queue_item=item,
        thread=thread,
        reviewer=manager,
        comment="Keep this review",
        **scope,
    )
    rule = AutomationRule.objects.create(
        queue=queue,
        name="Keep this rule",
        source_type=QueueItemSourceType.DATASET_ROW.value,
        organization=organization,
        created_by=manager,
        last_triggered_at=timezone.now() - timedelta(days=7),
    )
    label = AnnotationsLabels.objects.create(
        name="Queue label",
        type="categorical",
        settings={
            "options": [{"label": "A"}, {"label": "B"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        **scope,
    )
    binding = AnnotationQueueLabel.objects.create(
        queue=queue, label=label, required=True
    )
    return SimpleNamespace(
        queue=queue,
        manager=manager,
        member=member,
        item=item,
        label=label,
        binding=binding,
        rule=rule,
        rows=[
            queue,
            manager_role,
            item,
            assignment,
            note,
            thread,
            comment,
            rule,
            binding,
        ],
    )


def _snapshot(rows):
    # Bypass workspace/soft-delete filters so hidden rows cannot look unchanged.
    return [list(QuerySet(model=type(row)).filter(pk=row.pk).values()) for row in rows]


def _mutate(client, state, action):
    url = f"/model-hub/annotation-queues/{state.queue.pk}/"
    if action == "archive":
        return client.delete(url)
    if action == "delete-item":
        return client.delete(f"{url}items/{state.item.pk}/")
    payloads = {
        "hard-delete": {"force": True, "confirm_name": state.queue.name},
        "restore": {},
        "add-label": {"label_id": str(state.label.pk), "required": False},
        "remove-label": {"label_id": str(state.label.pk)},
        "update-status": {"status": "paused"},
        "items/bulk-remove": {"item_ids": [str(state.item.pk)]},
        "items/assign": {
            "item_ids": [str(state.item.pk)],
            "user_ids": [str(state.member.pk)],
            "action": "remove",
        },
    }
    return client.post(f"{url}{action}/", payloads[action], format="json")


@pytest.mark.parametrize("role", [None, "annotator", "reviewer"])
@pytest.mark.parametrize(
    "action",
    [
        "hard-delete",
        "archive",
        "restore",
        "add-label",
        "remove-label",
        "update-status",
        "delete-item",
        "items/bulk-remove",
        "items/assign",
    ],
)
def test_non_manager_mutations_are_forbidden_and_preserve_rows(
    api_client, queue_state, organization, workspace, role, action
):
    state = queue_state
    if role:
        membership = AnnotationQueueAnnotator.objects.create(
            queue=state.queue, user=state.member, role=role, roles=[role]
        )
        state.rows.append(membership)
    if action == "restore":
        state.queue.delete()
    before = _snapshot(state.rows)
    api_client.force_authenticate(user=state.member)
    api_client.set_workspace(workspace)

    if action == "hard-delete":
        response = api_client.patch(
            f"/model-hub/annotation-queues/{state.queue.pk}/",
            {"name": "Not allowed"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
        assert _snapshot(state.rows) == before

    response = _mutate(api_client, state, action)

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
    assert "Only queue managers" in str(response.data)
    assert _snapshot(state.rows) == before


@pytest.mark.parametrize(
    "action", ["hard-delete", "archive", "restore", "add-label", "remove-label"]
)
def test_explicit_queue_manager_can_mutate(
    api_client, queue_state, organization, workspace, action
):
    state = queue_state
    if action == "restore":
        state.queue.delete()
    children_before = _snapshot(state.rows[1:])
    api_client.force_authenticate(user=state.manager)
    api_client.set_workspace(workspace)

    response = _mutate(api_client, state, action)

    assert response.status_code == status.HTTP_200_OK, response.data
    if action == "hard-delete":
        assert response.data["result"]["hard_deleted"] is True
        for row in state.rows:
            assert not QuerySet(model=type(row)).filter(pk=row.pk).exists()
        assert AnnotationsLabels.objects.filter(pk=state.label.pk).exists()
    elif action == "archive":
        state.queue.refresh_from_db()
        assert state.queue.deleted is True
        assert _snapshot(state.rows[1:]) == children_before
    elif action == "restore":
        state.queue.refresh_from_db()
        state.rule.refresh_from_db()
        assert state.queue.deleted is False
        assert state.queue.deleted_at is None
        assert state.rule.last_triggered_at >= timezone.now() - timedelta(minutes=1)
        assert QueueItem.objects.filter(pk=state.item.pk).exists()
    else:
        state.binding.refresh_from_db()
        if action == "add-label":
            assert state.binding.required is False
            assert state.binding.deleted is False
        else:
            assert state.binding.deleted is True


def test_restore_does_not_cross_workspace_scope(
    auth_client, queue_state, organization, workspace, user
):
    other_workspace = Workspace.objects.create(
        name="Other workspace",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    state = queue_state
    state.queue.workspace = other_workspace
    state.queue.delete()
    before = _snapshot(state.rows)

    response = _mutate(auth_client, state, "restore")

    assert response.status_code == status.HTTP_404_NOT_FOUND, response.data
    assert _snapshot(state.rows) == before
