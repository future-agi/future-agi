"""FA-ANNOT-03: manual managers can navigate to claimable unassigned work."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
    QueueItemAssignment,
    annotation_queue_effective_roles,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_dataset import Dataset, Row
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["legacy", "multi"])
def manual_queue(request, organization, workspace, auth_client):
    """One ordinary workspace member manages a queue with two items."""
    member = User.objects.create_user(
        email=f"manual-next-{uuid.uuid4().hex}@futureagi.com",
        name="Manual queue manager",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=member,
        organization=organization,
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=member,
        workspace=workspace,
        defaults={
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "organization_membership": membership,
            "is_active": True,
        },
    )
    scope = {"organization": organization, "workspace": workspace}
    queue = AnnotationQueue.objects.create(
        name="Manual next-item regression",
        created_by=member,
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        auto_assign=False,
        **scope,
    )
    AnnotationQueueAnnotator.objects.filter(queue=queue, user=member).update(
        role=AnnotatorRole.MANAGER.value,
        roles=[AnnotatorRole.MANAGER.value],
    )
    dataset = Dataset.objects.create(name="Manual next-item source", **scope)
    completed = QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.DATASET_ROW.value,
        dataset_row=Row.objects.create(dataset=dataset, order=0),
        status=QueueItemStatus.COMPLETED.value,
        assigned_to=member if request.param == "legacy" else None,
        **scope,
    )
    if request.param == "multi":
        QueueItemAssignment.objects.create(queue_item=completed, user=member)
    pending = QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.DATASET_ROW.value,
        dataset_row=Row.objects.create(dataset=dataset, order=1),
        status=QueueItemStatus.PENDING.value,
        **scope,
    )
    auth_client.force_authenticate(user=member)
    return SimpleNamespace(
        queue=queue,
        member=member,
        pending=pending,
        url=f"/model-hub/annotation-queues/{queue.id}/items/next-item/",
    )


def test_sole_manual_manager_finds_unassigned_work_after_completed_assignment(
    auth_client, manual_queue
):
    """FA-ANNOT-03: Start Annotating must not report All done for this state."""
    state = manual_queue
    assert list(
        AnnotationQueueAnnotator.objects.filter(
            queue=state.queue, deleted=False
        ).values_list("user_id", flat=True)
    ) == [state.member.id]
    assert annotation_queue_effective_roles(state.queue, state.member) == [
        AnnotatorRole.MANAGER.value
    ]
    assert not QueueItemAssignment.objects.filter(
        queue_item=state.pending, deleted=False
    ).exists()
    assert state.pending.assigned_to_id is None

    # Match Start Annotating: no review-mode or include-completed query params.
    response = auth_client.get(state.url)

    assert response.status_code == status.HTTP_200_OK, response.data
    item = response.data["result"]["item"]
    assert item is not None
    assert item["id"] == str(state.pending.id)


def test_manual_annotator_still_needs_assignment(auth_client, manual_queue):
    state = manual_queue
    AnnotationQueueAnnotator.objects.filter(
        queue=state.queue, user=state.member
    ).update(
        role=AnnotatorRole.ANNOTATOR.value,
        roles=[AnnotatorRole.ANNOTATOR.value],
    )
    assert annotation_queue_effective_roles(state.queue, state.member) == [
        AnnotatorRole.ANNOTATOR.value
    ]

    response = auth_client.get(state.url)

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["item"] is None

    QueueItemAssignment.objects.create(queue_item=state.pending, user=state.member)
    assigned_response = auth_client.get(state.url)

    assert assigned_response.status_code == status.HTTP_200_OK, assigned_response.data
    assert assigned_response.data["result"]["item"]["id"] == str(state.pending.id)


@pytest.mark.parametrize("unavailable_reason", ["legacy", "multi", "reserved"])
def test_manual_manager_does_not_navigate_to_someone_elses_work(
    auth_client, manual_queue, user, unavailable_reason
):
    state = manual_queue
    if unavailable_reason == "legacy":
        QueueItem.objects.filter(pk=state.pending.pk).update(assigned_to=user)
    elif unavailable_reason == "multi":
        QueueItemAssignment.objects.create(queue_item=state.pending, user=user)
    else:
        now = timezone.now()
        QueueItem.objects.filter(pk=state.pending.pk).update(
            reserved_by=user,
            reserved_at=now,
            reservation_expires_at=now + timedelta(minutes=30),
        )

    response = auth_client.get(state.url)

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["item"] is None
