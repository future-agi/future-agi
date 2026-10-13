"""Creation API evidence: real permissions, atomic snapshots and lineage."""

import json
import uuid

import pytest
from django.urls import reverse
from rest_framework.exceptions import ParseError
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.serializers.dataset_versions import (
    DatasetVersionCreateResponseSerializer,
    DatasetVersionCreateSerializer,
)
from model_hub.views.dataset_versions import (
    _MAX_SNAPSHOT_BODY_BYTES,
    BoundedSnapshotJSONParser,
    DatasetVersionListView,
)
from tfc.constants.levels import Level


@pytest.fixture
def snapshot_api_dataset(organization, workspace, user):
    return Dataset.objects.create(
        name="Snapshot API dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )


def _url(dataset):
    return reverse("dataset-version-list", kwargs={"dataset_id": dataset.id})


@pytest.mark.django_db
def test_snapshot_api_creates_lineage_and_round_trips_provenance(
    auth_client, snapshot_api_dataset, user
):
    row = Row.objects.create(dataset=snapshot_api_dataset, order=1)
    item = {
        "input": {"question": "hello", "optional": None},
        "expected": {"answer": "world"},
        "metadata": {"split": "eval", "optional": None},
        "external_id": " case-1 ",
        "source_trace_id": "trace-1",
        "source_span_id": "span-1",
        "legacy_row_id": str(row.id),
    }
    response = auth_client.post(
        _url(snapshot_api_dataset),
        {
            "items": [item],
            "description": "Baseline",
            "source_revision": "git:abc",
            "metadata": {"source": "manual", "optional": None},
        },
        format="json",
    )
    assert response.status_code == 201
    contract = DatasetVersionCreateResponseSerializer(data=response.json())
    assert contract.is_valid(), contract.errors
    baseline = response.json()["result"]
    assert baseline["item_count"] == 1
    assert baseline["created_by"] == str(user.id)
    assert baseline["dataset"] == str(snapshot_api_dataset.id)
    assert baseline["organization"] == str(snapshot_api_dataset.organization_id)
    assert baseline["workspace"] == str(snapshot_api_dataset.workspace_id)
    assert baseline["source_revision"] == "git:abc"
    assert len(baseline["content_hash"]) == 64
    assert "items" not in baseline

    child_response = auth_client.post(
        _url(snapshot_api_dataset),
        {"parent_version_id": baseline["id"], "items": [{**item, "expected": "new"}]},
        format="json",
    )
    assert child_response.status_code == 201
    child = child_response.json()["result"]
    assert child["parent_version"] == baseline["id"]
    assert child["content_hash"] != baseline["content_hash"]
    reads = []
    for version in (baseline, child):
        detail = auth_client.get(
            reverse(
                "dataset-version-detail",
                kwargs={
                    "dataset_id": snapshot_api_dataset.id,
                    "version_id": version["id"],
                },
            )
        )
        assert detail.status_code == 200
        reads.append(detail.json()["result"]["items"][0])
    assert reads[0]["example_id"] == reads[1]["example_id"]
    assert reads[0]["example_id"] == str(
        DatasetVersionItem.example_id_for_legacy_row(row.id)
    )
    assert reads[0]["expected"] == {"answer": "world"}
    assert reads[1]["expected"] == "new"
    assert reads[1]["external_id"] == " case-1 "
    assert reads[1]["source_trace_id"] == "trace-1"
    assert reads[1]["source_span_id"] == "span-1"
    assert reads[1]["legacy_row"] == str(row.id)
    assert reads[1]["metadata"] == item["metadata"]


@pytest.mark.django_db
def test_explicit_empty_snapshot_and_retry_create_fresh_versions(
    auth_client, snapshot_api_dataset
):
    Row.objects.create(dataset=snapshot_api_dataset, order=1)
    versions = [
        auth_client.post(_url(snapshot_api_dataset), {"items": []}, format="json")
        for _ in range(2)
    ]
    assert all(response.status_code == 201 for response in versions)
    first, second = [response.json()["result"] for response in versions]
    assert first["id"] != second["id"]
    assert first["content_hash"] == second["content_hash"]
    assert first["item_count"] == second["item_count"] == 0
    assert not DatasetVersionItem.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "level,expected_status",
    [
        (Level.VIEWER, 403),
        (Level.MEMBER, 201),
        (Level.ADMIN, 201),
    ],
)
def test_snapshot_api_uses_real_api_key_write_permissions(
    snapshot_api_dataset, user, organization, workspace, level, expected_status
):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(
        level=level,
        role=Level.to_org_string(level),
    )
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(
        level=level,
        role=Level.to_ws_role(level),
    )
    assert user.can_write_to_workspace(workspace) is (level >= Level.MEMBER)
    key = OrgApiKey.no_workspace_objects.create(
        name="snapshot-api-test-key",
        organization=organization,
        type="user",
        user=user,
        workspace=workspace,
        api_key="snapshot-api-local-public",
        secret_key="snapshot-api-local-secret",
    )
    response = APIClient().post(
        _url(snapshot_api_dataset),
        {"items": [{"input": "test"}]},
        format="json",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
    )
    assert response.status_code == expected_status
    if expected_status == 201:
        version = DatasetVersion.objects.get(id=response.json()["result"]["id"])
        assert version.created_by_id == user.id
        assert version.items.get().input == "test"
    else:
        assert not DatasetVersion.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hidden_by", ["organization", "workspace", "deleted", "missing"]
)
def test_snapshot_api_hides_inaccessible_datasets(
    auth_client, snapshot_api_dataset, organization, user, hidden_by
):
    if hidden_by == "organization":
        Dataset.no_workspace_objects.filter(id=snapshot_api_dataset.id).update(
            organization=Organization.objects.create(name="Hidden"),
            workspace=None,
        )
    elif hidden_by == "workspace":
        other_workspace = Workspace.no_workspace_objects.create(
            name="Hidden",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        Dataset.no_workspace_objects.filter(id=snapshot_api_dataset.id).update(
            workspace=other_workspace
        )
    elif hidden_by == "deleted":
        Dataset.no_workspace_objects.filter(id=snapshot_api_dataset.id).update(
            deleted=True
        )
    else:
        snapshot_api_dataset.id = uuid.uuid4()
    response = auth_client.post(
        _url(snapshot_api_dataset), {"items": []}, format="json"
    )
    assert response.status_code == 404
    assert not DatasetVersion.no_workspace_objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("hidden_by", ["dataset", "deleted", "missing"])
def test_snapshot_api_requires_visible_same_dataset_parent(
    auth_client, snapshot_api_dataset, hidden_by
):
    parent = DatasetVersion.objects.create(dataset=snapshot_api_dataset)
    if hidden_by == "dataset":
        other = Dataset.objects.create(
            name="Other",
            organization=snapshot_api_dataset.organization,
            workspace=snapshot_api_dataset.workspace,
            user=snapshot_api_dataset.user,
            column_order=[],
            column_config={},
        )
        DatasetVersion.no_workspace_objects.filter(id=parent.id).update(dataset=other)
    elif hidden_by == "deleted":
        DatasetVersion.no_workspace_objects.filter(id=parent.id).update(deleted=True)
    else:
        parent.id = uuid.uuid4()
    response = auth_client.post(
        _url(snapshot_api_dataset),
        {
            "parent_version_id": str(parent.id),
            "items": [],
        },
        format="json",
    )
    assert response.status_code == 404
    assert DatasetVersion.no_workspace_objects.count() == (
        0 if hidden_by == "deleted" else 1
    )
    assert str(parent.id) not in str(response.json())


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_item",
    [
        {"input": "bad\x00text"},
        {"input": "bad\ud800text"},
        {"input": "ok", "legacy_row_id": str(uuid.uuid4())},
        {"input": "ok", "external_id": "duplicate"},
    ],
)
def test_snapshot_service_rejections_leave_no_partial_header_or_items(
    auth_client, snapshot_api_dataset, invalid_item
):
    response = auth_client.post(
        _url(snapshot_api_dataset),
        json.dumps(
            {
                "items": [
                    {"input": "first", "external_id": "duplicate"},
                    invalid_item,
                ]
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["status"] is False
    assert not DatasetVersion.no_workspace_objects.exists()
    assert not DatasetVersionItem.no_workspace_objects.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": None},
        {"items": {}},
        {"items": [None]},
        {"items": [{}]},
        {"items": [{"input": None}]},
        {"items": [{"input": "ok", "metadata": []}]},
        {"items": [], "metadata": []},
        {"items": [], "source_revision": "x" * 256},
        {"items": [{"input": "ok", "external_id": 123}]},
        {"items": [{"input": "ok", "example_id": "not-a-uuid"}]},
        {"items": [{"input": "ok", "created_by": str(uuid.uuid4())}]},
        {"items": [], "organization": str(uuid.uuid4())},
        {"items": [], "workspace_id": str(uuid.uuid4())},
        {"items": [], "content_hash": "caller-hash"},
    ],
)
def test_snapshot_request_rejects_malformed_or_server_owned_fields(payload):
    request = APIRequestFactory().post("/", payload, format="json")
    force_authenticate(request, user=User(email="no-org@example.invalid"))
    response = DatasetVersionListView.as_view()(request, dataset_id=uuid.uuid4())
    assert response.status_code == 400
    assert response.data["status"] is False


def test_snapshot_request_cap_is_inclusive_and_does_not_truncate():
    items = [{"input": "case"}] * 10_000
    serializer = DatasetVersionCreateSerializer(data={"items": items})
    assert serializer.is_valid(), serializer.errors
    assert len(serializer.validated_data["items"]) == 10_000
    too_many = DatasetVersionCreateSerializer(
        data={"items": items + [{"input": "last"}]}
    )
    assert not too_many.is_valid()
    assert "items" in too_many.errors


def test_snapshot_create_does_not_fallback_from_denied_auth_organization():
    request = APIRequestFactory().post("/", {"items": []}, format="json")
    request.organization = None
    force_authenticate(
        request,
        user=User(
            email="removed@example.invalid",
            organization=Organization(id=uuid.uuid4()),
        ),
    )
    response = DatasetVersionListView.as_view()(request, dataset_id=uuid.uuid4())
    assert response.status_code == 404


@pytest.mark.django_db
def test_snapshot_create_requires_authentication(api_client, snapshot_api_dataset):
    response = api_client.post(_url(snapshot_api_dataset), {"items": []}, format="json")
    assert response.status_code == 401
    assert not DatasetVersion.no_workspace_objects.exists()


def test_snapshot_body_parser_rejects_only_above_inclusive_byte_bound():
    import io

    parser = BoundedSnapshotJSONParser()
    prefix = b'{"items":[]}'
    below = prefix + b" " * (_MAX_SNAPSHOT_BODY_BYTES - len(prefix) - 1)
    exact = below + b" "
    above = exact + b" "
    assert parser.parse(io.BytesIO(below)) == {"items": []}
    assert parser.parse(io.BytesIO(exact)) == {"items": []}
    with pytest.raises(ParseError, match="at most"):
        parser.parse(io.BytesIO(above))

    request = APIRequestFactory().post("/", above, content_type="application/json")
    force_authenticate(request, user=User(email="no-org@example.invalid"))
    response = DatasetVersionListView.as_view()(request, dataset_id=uuid.uuid4())
    assert response.status_code == 400
    assert "at most 10485760 bytes" in str(response.data)
