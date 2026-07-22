from datetime import timedelta
import uuid

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.project import Project
from tracer.models.shared_link import SharedLink


@pytest.mark.django_db
def test_trace_shared_link_lifecycle_resolves_valid_token(
    api_client,
    auth_client,
    organization,
    trace,
    observation_span,
    user,
):
    response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": str(trace.id),
            "access_type": "public",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    public_link = response.json()["result"]
    assert public_link["resource_type"] == "trace"
    assert public_link["access_type"] == "public"
    assert public_link["access_list"] == []
    assert public_link["share_url"].endswith(f"/shared/{public_link['token']}")

    detail_response = auth_client.get(f"/tracer/shared-links/{public_link['id']}/")
    assert detail_response.status_code == status.HTTP_200_OK
    assert detail_response.json()["result"]["id"] == public_link["id"]

    list_response = auth_client.get(
        "/tracer/shared-links/",
        data={"resource_type": "trace", "resource_id": str(trace.id)},
    )
    assert list_response.status_code == status.HTTP_200_OK
    listed_ids = {row["id"] for row in list_response.json()["result"]}
    assert public_link["id"] in listed_ids

    resolved = api_client.get(f"/tracer/shared/{public_link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK
    payload = resolved.json()
    assert payload["resource_type"] == "trace"
    assert payload["resource_id"] == str(trace.id)
    assert payload["access_type"] == "public"
    assert payload["data"]["trace"]["id"] == str(trace.id)
    assert payload["data"]["trace"]["project_id"] == str(trace.project_id)
    assert payload["data"]["summary"]["total_spans"] == 1
    assert (
        payload["data"]["observation_spans"][0]["observation_span"]["id"]
        == observation_span.id
    )

    restricted_response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": str(trace.id),
            "access_type": "restricted",
            "emails": ["viewer@example.com"],
        },
        format="json",
    )
    assert restricted_response.status_code == status.HTTP_201_CREATED
    restricted_link = restricted_response.json()["result"]
    assert [entry["email"] for entry in restricted_link["access_list"]] == [
        "viewer@example.com"
    ]

    updated_expiry = timezone.now() + timedelta(days=1)
    patch_response = auth_client.patch(
        f"/tracer/shared-links/{restricted_link['id']}/",
        data={"expires_at": updated_expiry.isoformat()},
        format="json",
    )
    assert patch_response.status_code == status.HTTP_200_OK
    assert patch_response.json()["result"]["expires_at"] is not None

    put_response = auth_client.put(
        f"/tracer/shared-links/{restricted_link['id']}/",
        data={
            "access_type": "restricted",
            "expires_at": updated_expiry.isoformat(),
        },
        format="json",
    )
    assert put_response.status_code == status.HTTP_200_OK
    assert put_response.json()["result"]["access_type"] == "restricted"

    add_access = auth_client.post(
        f"/tracer/shared-links/{restricted_link['id']}/access/",
        data={"emails": ["second-viewer@example.com"]},
        format="json",
    )
    assert add_access.status_code == status.HTTP_201_CREATED
    added_access = add_access.json()["result"][0]
    assert added_access["email"] == "second-viewer@example.com"

    remove_access = auth_client.delete(
        f"/tracer/shared-links/{restricted_link['id']}/access/{added_access['id']}/"
    )
    assert remove_access.status_code == status.HTTP_200_OK

    unauthenticated = api_client.get(f"/tracer/shared/{restricted_link['token']}/")
    assert unauthenticated.status_code == status.HTTP_401_UNAUTHORIZED
    assert unauthenticated.data["code"] == "not_authenticated"

    creator_resolved = auth_client.get(f"/tracer/shared/{restricted_link['token']}/")
    assert creator_resolved.status_code == status.HTTP_200_OK
    assert creator_resolved.json()["data"]["trace"]["id"] == str(trace.id)

    viewer = User.objects.create_user(
        email="viewer@example.com",
        password="testpassword123",
        name="Shared Link Viewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    api_client.force_authenticate(user=viewer)
    viewer_resolved = api_client.get(f"/tracer/shared/{restricted_link['token']}/")
    assert viewer_resolved.status_code == status.HTTP_200_OK
    assert viewer_resolved.json()["data"]["trace"]["id"] == str(trace.id)

    revoke_response = auth_client.delete(f"/tracer/shared-links/{public_link['id']}/")
    assert revoke_response.status_code == status.HTTP_200_OK

    revoked = api_client.get(f"/tracer/shared/{public_link['token']}/")
    assert revoked.status_code == status.HTTP_410_GONE
    assert revoked.data["code"] == "gone"

    expired_link = SharedLink.objects.create(
        resource_type="trace",
        resource_id=str(trace.id),
        access_type="public",
        created_by=user,
        organization=trace.project.organization,
        workspace=trace.project.workspace,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    expired = api_client.get(f"/tracer/shared/{expired_link.token}/")
    assert expired.status_code == status.HTTP_410_GONE
    assert "expired" in expired.data["detail"]


@pytest.mark.django_db
def test_dashboard_shared_link_resolves_dashboard_detail_payload(
    api_client,
    auth_client,
    workspace,
    user,
):
    dashboard = Dashboard.objects.create(
        workspace=workspace,
        name="Shared dashboard",
        description="Dashboard resolved through a share token",
        created_by=user,
        updated_by=user,
    )
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Latency widget",
        position=0,
        width=6,
        height=4,
        query_config={
            "time_range": {"preset": "7D"},
            "metrics": [{"name": "latency", "type": "system_metric"}],
        },
        chart_config={"chart_type": "line"},
        created_by=user,
    )

    response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "dashboard",
            "resource_id": str(dashboard.id),
            "access_type": "public",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    link = response.json()["result"]

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK
    payload = resolved.json()
    assert payload["resource_type"] == "dashboard"
    assert payload["resource_id"] == str(dashboard.id)
    assert payload["data"]["id"] == str(dashboard.id)
    assert payload["data"]["name"] == dashboard.name
    assert payload["data"]["description"] == dashboard.description
    assert payload["data"]["widget_count"] == 1
    assert payload["data"]["widgets"][0]["id"] == str(widget.id)
    assert payload["data"]["widgets"][0]["chart_config"]["chart_type"] == "line"


@pytest.mark.django_db
def test_project_shared_link_resolves_observe_project_payload(
    api_client,
    auth_client,
    observe_project,
):
    response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "project",
            "resource_id": str(observe_project.id),
            "access_type": "public",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    link = response.json()["result"]

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK
    payload = resolved.json()
    assert payload["resource_type"] == "project"
    assert payload["resource_id"] == str(observe_project.id)
    assert payload["data"]["id"] == str(observe_project.id)
    assert payload["data"]["name"] == observe_project.name
    assert payload["data"]["trace_type"] == "observe"
    assert payload["data"]["model_type"] == observe_project.model_type
    assert (
        payload["data"]["url_path"]
        == f"/dashboard/observe/{observe_project.id}/llm-tracing"
    )


@pytest.mark.django_db
def test_shared_link_create_rejects_unsupported_and_cross_workspace_resources(
    auth_client,
    organization,
    user,
):
    unsupported_id = str(uuid.uuid4())
    unsupported = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "dataset",
            "resource_id": unsupported_id,
            "access_type": "public",
        },
        format="json",
    )
    assert unsupported.status_code == status.HTTP_400_BAD_REQUEST
    assert not SharedLink.no_workspace_objects.filter(
        resource_type="dataset",
        resource_id=unsupported_id,
    ).exists()

    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other Shared Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
    )
    other_project = Project.no_workspace_objects.create(
        name=f"Other Workspace Project {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    cross_workspace = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "project",
            "resource_id": str(other_project.id),
            "access_type": "public",
        },
        format="json",
    )
    assert cross_workspace.status_code == status.HTTP_404_NOT_FOUND
    assert not SharedLink.no_workspace_objects.filter(
        resource_type="project",
        resource_id=str(other_project.id),
    ).exists()
