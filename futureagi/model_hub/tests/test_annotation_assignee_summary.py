"""Task assignees use the read-only member summary contract."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models import AIModel, AnnotationTask
from model_hub.views.develop_annotations import AnnotationTaskViewSet as View
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def scope():
    org = Organization.objects.create(name="Task organization")
    other = Organization.objects.create(name="Other organization")
    user = User.objects.create_user(
        email="reader@example.test", name="Task reader", organization=org
    )
    default = Workspace.objects.create(
        name="Default", organization=org, is_default=True, created_by=user
    )
    other_workspace = Workspace.objects.create(
        name="Other default", organization=other, is_default=True, created_by=user
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=org, level=3, role="Member"
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=default,
        organization_membership=membership,
        level=3,
        role="workspace_member",
    )
    set_workspace_context(workspace=default, organization=org)
    yield SimpleNamespace(
        org=org,
        other=other,
        user=user,
        default=default,
        other_workspace=other_workspace,
    )
    clear_workspace_context()


def task(scope, name="Task", workspace="default", organization=None):
    organization = organization or scope.org
    workspace = scope.other_workspace if workspace == "other" else scope.default
    model = AIModel.objects.create(
        user_model_id=f"task-model-{uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )
    return AnnotationTask.objects.create(
        task_name=name, ai_model=model, organization=organization, workspace=workspace
    )


def invoke(scope, view, action, pk=None):
    request = APIRequestFactory().get("/")
    request.organization = scope.org
    request.workspace = scope.default
    force_authenticate(request, user=scope.user)
    return view.as_view({"get": action})(request, **({"pk": str(pk)} if pk else {}))


@pytest.mark.parametrize("other_default_organization", [False, True])
@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_assignee_projection_is_exact_member_summary(
    scope, other_default_organization, action
):
    assignee = User.objects.create_user(
        email="assignee@example.test",
        name="Assignee",
        organization=scope.other if other_default_organization else scope.org,
        goals=["Private profile goal"],
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=assignee,
        organization=scope.org,
        level=3,
        role="Member",
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=assignee,
        workspace=scope.default,
        organization_membership=membership,
        level=3,
        role="workspace_member",
    )
    value = task(scope)
    value.assigned_users.set([assignee])
    response = invoke(scope, View, action, pk=value.pk)
    assert response.status_code == 200
    row = response.data["results"][0] if action == "list" else response.data
    assert row["assigned_users"] == [
        {"id": str(assignee.pk), "name": "Assignee", "email": "assignee@example.test"}
    ]


@pytest.mark.parametrize("action", ["list", "retrieve"])
@pytest.mark.parametrize("assignee_count", [0, 2])
def test_empty_and_mixed_assignee_collections(scope, action, assignee_count):
    value = task(scope)
    value.assigned_users.clear()
    expected = []
    for index in range(assignee_count):
        assignee = User.objects.create_user(
            email=f"member-{index}@example.test",
            name=f"Member {index}",
            organization=scope.org if index == 0 else scope.other,
            goals=["Private goal"],
            organization_role="Admin",
        )
        membership = OrganizationMembership.no_workspace_objects.create(
            user=assignee, organization=scope.org, level=3, role="Member"
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=assignee,
            workspace=scope.default,
            organization_membership=membership,
            level=3,
            role="workspace_member",
        )
        value.assigned_users.add(assignee)
        expected.append(
            {"id": str(assignee.pk), "name": assignee.name, "email": assignee.email}
        )
    response = invoke(scope, View, action, pk=value.pk)
    assert response.status_code == 200
    row = response.data["results"][0] if action == "list" else response.data
    assert sorted(row["assigned_users"], key=lambda member: member["id"]) == sorted(
        expected, key=lambda member: member["id"]
    )


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_anonymous_task_read_returns_no_task_or_member_data(scope, action):
    value = task(scope)
    request = APIRequestFactory().get("/")
    response = View.as_view({"get": action})(request, pk=str(value.pk))
    assert response.status_code == 401
    message = "Authentication credentials were not provided."
    # The shared handler documents a full envelope, not DRF's detail-only body.
    # Exact equality pins every allowed value and rejects task/member additions.
    assert response.data == {
        "status": False,
        "type": "authentication_error",
        "code": "not_authenticated",
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
        "details": {"detail": [message]},
    }


def test_other_organization_task_retrieve_returns_no_task_or_member_data(scope):
    value = task(scope, "other", workspace="other", organization=scope.other)
    response = invoke(scope, View, "retrieve", pk=value.pk)
    assert response.status_code == 404
    message = "No AnnotationTask matches the given query."
    assert response.data == {
        "status": False,
        "type": "not_found",
        "code": "error",
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
        "details": {"detail": [message]},
    }


def test_other_organization_task_is_absent_from_list(scope):
    task(scope, "other", workspace="other", organization=scope.other)
    response = invoke(scope, View, "list")
    assert response.status_code == 200
    assert response.data["results"] == []
    assert response.data["count"] == 0


def test_projection_preserves_generic_profile_serialization(scope):
    """Narrow task responses without changing independent profile consumers."""
    from accounts.serializers.organization import OrganizationSerializer
    from accounts.serializers.user import UserSerializer

    assignee = User.objects.create_user(
        email="profile-default@example.test",
        name="Profile member",
        organization=scope.other,
        goals=["Private profile goal"],
        organization_role="Admin",
        config={"private_marker": "Private profile config"},
    )
    value = task(scope)
    value.assigned_users.set([assignee])
    response = invoke(scope, View, "retrieve", pk=value.pk)
    assert response.status_code == 200
    assert response.data["assigned_users"] == [
        {
            "id": str(assignee.pk),
            "name": "Profile member",
            "email": "profile-default@example.test",
        }
    ]
    profile = UserSerializer(assignee).data
    assert profile["goals"] == ["Private profile goal"]
    assert profile["organization_role"] == "Admin"
    assert profile["organization"] == OrganizationSerializer(scope.other).data
    assignee.refresh_from_db()
    assert assignee.config == {"private_marker": "Private profile config"}


@pytest.mark.parametrize("representation", ["model", "dictionary"])
def test_annotation_assignees_are_member_summaries(scope, representation):
    from model_hub.models.develop_annotations import Annotations
    from model_hub.serializers.develop_annotations import AnnotationsSerializer

    annotation = Annotations.objects.create(
        name="Annotation", organization=scope.org, workspace=scope.default
    )
    annotation.assigned_users.add(scope.user)
    value = (
        annotation if representation == "model" else {"assigned_users": [scope.user]}
    )
    assert AnnotationsSerializer().get_assigned_users(value) == [
        {"id": scope.user.id, "name": scope.user.name, "email": scope.user.email}
    ]


@pytest.fixture
def queue_item(scope):
    from model_hub.models.annotation_queues import (
        AnnotationQueue,
        QueueItem,
        QueueItemAssignment,
    )

    queue = AnnotationQueue.objects.create(
        name="Queue", organization=scope.org, workspace=scope.default
    )
    item = QueueItem.objects.create(
        queue=queue,
        source_type="dataset_row",
        organization=scope.org,
        workspace=scope.default,
    )
    QueueItemAssignment.objects.create(queue_item=item, user=scope.user)
    return item


@pytest.mark.parametrize("prefetched", [False, True])
def test_queue_item_assignees_are_member_summaries(scope, queue_item, prefetched):
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    if prefetched:
        queue_item.active_assignments = list(queue_item.assignments.all())
    assert QueueItemSerializer().get_assigned_users(queue_item) == [
        {"id": str(scope.user.id), "name": scope.user.name, "email": scope.user.email}
    ]


def test_annotation_detail_assignees_keep_identity_summary(scope, queue_item, mocker):
    from model_hub.serializers.annotation_queues import AnnotateDetailSerializer

    mocker.patch(
        "model_hub.serializers.annotation_queues.resolve_source_content",
        return_value={},
    )
    mocker.patch(
        "model_hub.serializers.annotation_queues.resolve_source_preview",
        return_value={},
    )
    data = AnnotateDetailSerializer(
        context={"ch_source_cache": object()}
    ).to_representation(
        {
            "item": queue_item,
            "queue": queue_item.queue,
            "labels": [],
            "annotations": [],
            "progress": {},
        }
    )
    assert data["item"]["assigned_users"] == [
        {"id": str(scope.user.id), "name": scope.user.name}
    ]


def test_filtered_task_list_returns_member_summaries(scope):
    selected = task(scope, "Selected")
    selected.assigned_users.add(scope.user)
    task(scope, "Unselected")
    request = APIRequestFactory().get(
        "/", {"predictive_journey": str(selected.ai_model_id)}
    )
    request.organization = scope.org
    request.workspace = scope.default
    force_authenticate(request, user=scope.user)
    response = View.as_view({"get": "list"})(request)
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(selected.id)
    assert response.data["results"][0]["assigned_users"] == [
        {"id": str(scope.user.id), "name": scope.user.name, "email": scope.user.email}
    ]
