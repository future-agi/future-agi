import uuid

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.views.dataset_versions import (
    DatasetVersionDetailView,
    DatasetVersionListView,
)
from tfc.middleware.workspace_context import workspace_context


@pytest.fixture
def version_api_dataset(organization, workspace, user):
    return Dataset.objects.create(
        name="Version API dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )


@pytest.mark.django_db
def test_dataset_version_list_is_scoped_and_reports_item_count(
    auth_client, version_api_dataset
):
    version = DatasetVersion.objects.create(
        dataset=version_api_dataset,
        description="Evaluation baseline",
        source_revision="git:abc123",
    )
    DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "2 + 2?"},
        expected={"answer": "4"},
    )
    DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "3 + 3?"},
        deleted=True,
    )

    response = auth_client.get(
        reverse("dataset-version-list", kwargs={"dataset_id": version_api_dataset.id})
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["dataset_id"] == str(version_api_dataset.id)
    assert payload["versions"][0]["id"] == str(version.id)
    assert payload["versions"][0]["item_count"] == 1
    assert payload["versions"][0]["source_revision"] == "git:abc123"
    assert payload["limit"] == 100
    assert payload["offset"] == 0
    assert payload["has_more"] is False


@pytest.mark.django_db
def test_dataset_version_list_pages_active_versions_without_overlap(
    auth_client, version_api_dataset
):
    oldest = DatasetVersion.objects.create(dataset=version_api_dataset)
    newest = DatasetVersion.objects.create(dataset=version_api_dataset)
    DatasetVersion.objects.create(dataset=version_api_dataset, deleted=True)
    url = reverse("dataset-version-list", kwargs={"dataset_id": version_api_dataset.id})

    first = auth_client.get(url, {"limit": 1}).json()["result"]
    second = auth_client.get(url, {"limit": 1, "offset": 1}).json()["result"]
    empty = auth_client.get(url, {"limit": 1, "offset": 2}).json()["result"]

    assert [version["id"] for version in first["versions"]] == [str(newest.id)]
    assert first["has_more"] is True
    assert [version["id"] for version in second["versions"]] == [str(oldest.id)]
    assert second["offset"] == 1
    assert second["has_more"] is False
    assert empty["versions"] == []
    assert empty["has_more"] is False


@pytest.mark.django_db
def test_dataset_version_detail_returns_provenance_and_bounded_items(
    auth_client, version_api_dataset
):
    version = DatasetVersion.objects.create(dataset=version_api_dataset)
    first = DatasetVersionItem.objects.create(
        dataset_version=version,
        example_id=uuid.uuid4(),
        external_id="case-1",
        input={"question": "hello"},
        expected={"answer": "world"},
        metadata={"split": "eval"},
        source_trace_id="trace-1",
        source_span_id="span-1",
    )
    DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "second"},
    )

    response = auth_client.get(
        reverse(
            "dataset-version-detail",
            kwargs={"dataset_id": version_api_dataset.id, "version_id": version.id},
        ),
        {"limit": 1},
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["version"]["id"] == str(version.id)
    assert payload["items"][0]["id"] == str(first.id)
    assert payload["items"][0]["source_trace_id"] == "trace-1"
    assert payload["items"][0]["source_span_id"] == "span-1"
    assert payload["items"][0]["content_hash"] == first.content_hash
    assert payload["has_more"] is True


@pytest.mark.django_db
def test_dataset_version_detail_rejects_invalid_page_parameters(
    auth_client, version_api_dataset
):
    version = DatasetVersion.objects.create(dataset=version_api_dataset)

    response = auth_client.get(
        reverse(
            "dataset-version-detail",
            kwargs={"dataset_id": version_api_dataset.id, "version_id": version.id},
        ),
        {"limit": 0},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.unit
@pytest.mark.parametrize("view", [DatasetVersionListView, DatasetVersionDetailView])
@pytest.mark.parametrize(
    "query,field",
    [
        ({"limit": 0}, "limit"),
        ({"limit": 501}, "limit"),
        ({"limit": "not-an-integer"}, "limit"),
        ({"offset": -1}, "offset"),
        ({"offset": "not-an-integer"}, "offset"),
        ({"offset": 2147483648}, "offset"),
    ],
)
def test_version_routes_reject_invalid_queries_before_reading_data(view, query, field):
    request = APIRequestFactory().get("/", query)
    force_authenticate(request, user=User(email="no-org@example.invalid"))
    kwargs = {"dataset_id": uuid.uuid4()}
    if view is DatasetVersionDetailView:
        kwargs["version_id"] = uuid.uuid4()

    # No database access is allowed: validation must fail before any lookup.
    response = view.as_view()(request, **kwargs)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["status"] is False
    assert field in response.data["details"]


@pytest.mark.unit
@pytest.mark.parametrize("view", [DatasetVersionListView, DatasetVersionDetailView])
def test_version_routes_deny_missing_organization_without_database_access(view):
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=User(email="no-org@example.invalid"))
    kwargs = {"dataset_id": uuid.uuid4()}
    if view is DatasetVersionDetailView:
        kwargs["version_id"] = uuid.uuid4()

    with workspace_context(None, organization=None):
        response = view.as_view()(request, **kwargs)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["status"] is False


@pytest.mark.django_db
@pytest.mark.parametrize("hidden_by", ["organization", "workspace", "deleted"])
def test_version_routes_do_not_expose_inaccessible_dataset(
    auth_client, organization, workspace, user, hidden_by
):
    other_organization = (
        Organization.objects.create(name="Other version organization")
        if hidden_by == "organization"
        else organization
    )
    other_workspace = (
        Workspace.no_workspace_objects.create(
            name="Other version workspace",
            organization=other_organization,
            created_by=user,
            is_default=False,
        )
        if hidden_by in {"organization", "workspace"}
        else workspace
    )
    hidden = Dataset.no_workspace_objects.create(
        name="Hidden version dataset",
        organization=other_organization,
        workspace=other_workspace,
        user=user,
        column_order=[],
        column_config={},
        deleted=hidden_by == "deleted",
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=hidden)
    list_url = reverse("dataset-version-list", kwargs={"dataset_id": hidden.id})
    detail_url = reverse(
        "dataset-version-detail",
        kwargs={"dataset_id": hidden.id, "version_id": version.id},
    )

    for url in (list_url, detail_url):
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert str(version.id) not in str(response.data)
