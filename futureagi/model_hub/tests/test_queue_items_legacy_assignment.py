"""Singular-user requests preserve additive assignment and explicit unassignment."""

from copy import deepcopy
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
    QueueItemAssignment,
)
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.serializers.annotation_queues import AssignItemsSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module", autouse=True)
def load_unscoped_urlconf():
    from django.urls import get_resolver
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    get_resolver().url_patterns


@pytest.fixture
def assignment_items(organization, workspace, user):
    queue = AnnotationQueue.objects.create(
        name="Assignment queue", organization=organization, workspace=workspace,
        created_by=user,
    )
    other = get_user_model().objects.create(
        email=f"annotator-{uuid4().hex}@example.invalid",
        name="Other annotator", organization=organization,
    )
    for member in (user, other):
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=member)
    dataset = Dataset.objects.create(
        name="Assignment data", organization=organization, workspace=workspace,
    )
    items = [
        QueueItem.objects.create(
            queue=queue, source_type="dataset_row",
            dataset_row=Row.objects.create(dataset=dataset, order=index),
            organization=organization, workspace=workspace, assigned_to=other,
        )
        for index in range(2)
    ]
    for item in items:
        QueueItemAssignment.objects.create(queue_item=item, user=other)
    return queue, items, other


def assignees(item):
    return set(
        QueueItemAssignment.objects.filter(queue_item=item, deleted=False)
        .values_list("user_id", flat=True)
    )


@pytest.mark.parametrize("unassign", [False, True])
def test_singular_user_normalizes_without_mutating_payload(unassign):
    user_id = None if unassign else str(uuid4())
    payload = {"item_ids": [str(uuid4())], "user_id": user_id}
    original = deepcopy(payload)
    serializer = AssignItemsSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["action"] == ("set" if unassign else "add")
    assert [str(pk) for pk in serializer.validated_data["user_ids"]] == (
        [] if unassign else [user_id]
    )
    assert "user_id" not in serializer.validated_data
    assert payload == original


def test_singular_assign_keeps_other_assignees_and_unselected_items(
    auth_client, assignment_items, user,
):
    queue, items, other = assignment_items
    before = QueueItemAssignment.objects.get(queue_item=items[0], user=other)
    for _ in range(2):
        response = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/items/assign/",
            {"item_ids": [str(items[0].id)], "user_id": str(user.id)},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert assignees(items[0]) == {other.id, user.id}
        assert assignees(items[1]) == {other.id}
    before.refresh_from_db()
    assert not before.deleted
    assert QueueItemAssignment.objects.filter(queue_item=items[0], deleted=False).count() == 2
    items[1].refresh_from_db()
    assert items[1].assigned_to_id == other.id


@pytest.mark.parametrize("deleted_history", [False, True])
def test_singular_assignment_retains_legacy_only_assignee(
    auth_client, assignment_items, organization, workspace, user, deleted_history,
):
    queue, existing_items, other = assignment_items
    legacy_item = QueueItem.objects.create(
        queue=queue,
        source_type="dataset_row",
        dataset_row=Row.objects.create(
            dataset=existing_items[0].dataset_row.dataset,
            order=2,
        ),
        organization=organization,
        workspace=workspace,
        assigned_to=other,
    )
    if deleted_history:
        QueueItemAssignment.objects.create(
            queue_item=legacy_item, user=other, deleted=True,
        )
    assert legacy_item.assigned_to_id == other.id
    assert assignees(legacy_item) == set()
    unselected_rows = set(
        QueueItemAssignment.all_objects.filter(queue_item__in=existing_items)
        .values_list("id", "queue_item_id", "user_id", "deleted")
    )

    for _ in range(2):
        response = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/items/assign/",
            {"item_ids": [str(legacy_item.id)], "user_id": str(user.id)},
            format="json",
        )
        assert response.status_code == 200, response.data
        legacy_item.refresh_from_db()
        assert legacy_item.assigned_to_id == other.id
        assert assignees(legacy_item) == {other.id, user.id}
        assert QueueItemAssignment.objects.filter(queue_item=legacy_item).count() == 2
        assert QueueItemAssignment.all_objects.filter(queue_item=legacy_item).count() == (
            3 if deleted_history else 2
        )
        for item in existing_items:
            item.refresh_from_db()
            assert item.assigned_to_id == other.id
            assert assignees(item) == {other.id}
        assert set(
            QueueItemAssignment.all_objects.filter(queue_item__in=existing_items)
            .values_list("id", "queue_item_id", "user_id", "deleted")
        ) == unselected_rows


def test_null_user_clears_selected_assignments_and_never_assigns(
    auth_client, assignment_items, user,
):
    queue, items, other = assignment_items
    QueueItemAssignment.objects.create(queue_item=items[0], user=user)
    before_count = QueueItemAssignment.all_objects.filter(queue_item__in=items).count()
    for _ in range(2):
        response = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/items/assign/",
            {"item_ids": [str(items[0].id)], "user_id": None},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["result"]["assigned"] == 0
        assert assignees(items[0]) == set()
        assert assignees(items[1]) == {other.id}
        items[0].refresh_from_db()
        assert items[0].assigned_to_id is None
        assert QueueItemAssignment.all_objects.filter(queue_item__in=items).count() == before_count


@pytest.mark.parametrize("user_value", ["member", None])
@pytest.mark.parametrize("extra", [{"user_ids": []}, {"action": "set"}, {"action": "add"}])
def test_mixed_assignment_forms_are_refused_without_writes(
    auth_client, assignment_items, user, user_value, extra,
):
    queue, items, other = assignment_items
    response = auth_client.post(
        f"/model-hub/annotation-queues/{queue.id}/items/assign/",
        {"item_ids": [str(items[0].id)],
         "user_id": str(user.id) if user_value else None, **extra},
        format="json",
    )
    assert response.status_code == 400, response.data
    assert "Use either user_id or user_ids/action" in str(response.data)
    assert assignees(items[0]) == {other.id}


@pytest.mark.parametrize("invalid", ["", "invalid", [], {}])
def test_invalid_singular_user_is_not_an_unassign(
    auth_client, assignment_items, invalid,
):
    queue, items, other = assignment_items
    response = auth_client.post(
        f"/model-hub/annotation-queues/{queue.id}/items/assign/",
        {"item_ids": [str(items[0].id)], "user_id": invalid},
        format="json",
    )
    assert response.status_code == 400, response.data
    assert assignees(items[0]) == {other.id}


def test_unknown_assignment_key_is_refused_without_writes(
    auth_client, assignment_items, user,
):
    queue, items, other = assignment_items
    response = auth_client.post(
        f"/model-hub/annotation-queues/{queue.id}/items/assign/",
        {"item_ids": [str(items[0].id)], "user_id": str(user.id), "unexpected": True},
        format="json",
    )
    assert response.status_code == 400, response.data
    assert "unexpected" in str(response.data)
    assert "Unknown field." in str(response.data)
    assert assignees(items[0]) == {other.id}


def test_plural_assignment_form_keeps_explicit_replacement(
    auth_client, assignment_items, user,
):
    queue, items, other = assignment_items
    response = auth_client.post(
        f"/model-hub/annotation-queues/{queue.id}/items/assign/",
        {"item_ids": [str(items[0].id)], "user_ids": [str(user.id)], "action": "set"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert assignees(items[0]) == {user.id}
    assert assignees(items[1]) == {other.id}
