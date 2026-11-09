"""Int6 list payload, SQL and validation stay unchanged while F44 is OFF.

The body-level two-query check excludes middleware/auth lookups; full route
checks below separately exercise the workspace-aware authenticated clients.
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.develop_dataset import Dataset
from model_hub.serializers.develop_dataset_contracts import DatasetListItemSerializer
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from model_hub.views.develop_dataset import GetDatasetsView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

FLAG = "DSX_DATASET_LIST_COLUMNS_ENABLED"
LIST = "/model-hub/develops/get-datasets/"
METADATA = {"created_by", "created_by_name", "source", "updated_at"}


@pytest.fixture
def tenants(db):
    a, b = _tenant("columns-off-a"), _tenant("columns-off-b")
    yield a, b
    clear_workspace_context()


def legacy_row(actor):
    dataset = actor["dataset"]
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "description": dataset.description,
        "tags": dataset.tags,
        "starred": False,
        "folder": "",
        "folder_id": None,
        "number_of_datapoints": 2,
        "number_of_experiments": 0,
        "number_of_optimisations": 0,
        "derived_datasets": 0,
        "created_at": dataset.created_at.strftime("%Y-%m-%d %H:%M"),
        "dataset_type": dataset.model_type,
    }


def body_list(actor, **params):
    # Exercise the real query body without auth/middleware DB overhead.
    request = SimpleNamespace(
        GET={"page_size": 100, **params},
        user=actor["user"],
        workspace=actor["workspace"],
        organization=actor["org"],
        validated_query_data={},
    )
    response = GetDatasetsView.get.__wrapped__(GetDatasetsView(), request)
    assert response.status_code == 200, response.data
    return response.data["result"]


@pytest.mark.django_db
@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_off_payload_sql_and_query_count_match_int6(tenants, settings, flag):
    a, b = tenants
    if flag is None:
        if hasattr(settings, FLAG):
            delattr(settings, FLAG)
    else:
        setattr(settings, FLAG, flag)
    for actor in (a, b):
        with TestCase().assertNumQueries(2) as queries:
            result = body_list(actor)
        assert result == {
            "datasets": [legacy_row(actor)],
            "total_count": 1,
            "total_pages": 1,
        }
        user_table = User._meta.db_table.lower()
        assert all(user_table not in query["sql"].lower() for query in queries)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["owner", "member", "viewer", "foreign"])
def test_off_route_preserves_exact_payload_for_each_role(tenants, role):
    a, b = tenants
    actor = b if role == "foreign" else a
    if role in ("member", "viewer"):
        level = Level.VIEWER if role == "viewer" else Level.MEMBER
        org_role = (
            OrganizationRoles.MEMBER_VIEW_ONLY
            if role == "viewer"
            else OrganizationRoles.MEMBER
        )
        user = User.objects.create_user(
            email=f"columns-off-{role}@example.test",
            password="synthetic-columns-password",
            name=f"Columns {role}",
            organization=a["org"],
            organization_role=org_role,
        )
        membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
            organization=a["org"],
            user=user,
            defaults={"role": org_role, "level": level, "is_active": True},
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=a["workspace"],
            user=user,
            defaults={
                "role": "Workspace Viewer" if role == "viewer" else "Workspace Member",
                "level": level,
                "is_active": True,
                "organization_membership": membership,
            },
        )
        actor = {**a, "user": user}
    client = _client(actor)
    try:
        with override_settings(**{FLAG: False}):
            response = client.get(LIST, {"page_size": 100})
            assert response.status_code == 200, response.content
            assert response.data["result"] == {
                "datasets": [legacy_row(actor)],
                "total_count": 1,
                "total_pages": 1,
            }
            foreign = a if role == "foreign" else b
            response = client.get(LIST, {"search_text": foreign["dataset"].name})
            assert response.status_code == 200, response.content
            assert response.data["result"] == {
                "datasets": [],
                "total_count": 0,
                "total_pages": 0,
            }
    finally:
        client.stop_workspace_injection()


@pytest.mark.django_db
@pytest.mark.parametrize("direction", ["ascending", "descending"])
def test_off_updated_sort_is_ignored_before_pagination(tenants, direction):
    a, _ = tenants
    now = timezone.now()
    second = Dataset.no_workspace_objects.create(
        name="off-newer",
        organization=a["org"],
        workspace=a["workspace"],
        user=a["user"],
    )
    Dataset.no_workspace_objects.filter(pk=a["dataset"].pk).update(
        created_at=now - timedelta(days=2), updated_at=now
    )
    Dataset.no_workspace_objects.filter(pk=second.pk).update(
        created_at=now, updated_at=now - timedelta(days=2)
    )
    with override_settings(**{FLAG: False}):
        for page, expected in enumerate((second, a["dataset"])):
            params = {"page": page, "page_size": 1}
            control = body_list(a, **params)
            sorted_result = body_list(
                a,
                sort=json.dumps([{"column_id": "updated_at", "type": direction}]),
                **params,
            )
            assert sorted_result == control
            assert [row["id"] for row in control["datasets"]] == [str(expected.pk)]


@pytest.mark.django_db
@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_serializer_off_validation_and_representation_match_int6(
    tenants, settings, flag
):
    # The schema-only profile must not change runtime OFF validation or emit nulls.
    a, _ = tenants
    row = legacy_row(a)
    if flag is None:
        if hasattr(settings, FLAG):
            delattr(settings, FLAG)
    else:
        setattr(settings, FLAG, flag)
    legacy = DatasetListItemSerializer(data=row)
    extra = DatasetListItemSerializer(
        data={
            **row,
            "created_by": "not-a-uuid",
            "created_by_name": {},
            "source": None,
            "updated_at": "not-a-date",
        }
    )
    assert legacy.is_valid(), legacy.errors
    assert extra.is_valid(), extra.errors
    assert extra.validated_data == legacy.validated_data
    assert not METADATA.intersection(extra.fields)
    assert DatasetListItemSerializer({**row, "source": "build"}).data == row
    invalid = DatasetListItemSerializer(data={**row, "number_of_datapoints": "bad"})
    assert not invalid.is_valid()
    assert set(invalid.errors) == {"number_of_datapoints"}


@pytest.mark.django_db
def test_serializer_on_metadata_is_optional_nullable_and_validated(tenants):
    a, _ = tenants
    row = legacy_row(a)
    with override_settings(**{FLAG: True}):
        serializer = DatasetListItemSerializer(data=row)
        assert METADATA.issubset(serializer.fields)
        assert all(not serializer.fields[name].required for name in METADATA)
        assert serializer.is_valid(), serializer.errors
        nullable = DatasetListItemSerializer(
            data={
                **row,
                "created_by": None,
                "created_by_name": None,
            }
        )
        assert nullable.is_valid(), nullable.errors
        invalid = DatasetListItemSerializer(data={**row, "created_by": "not-a-uuid"})
        assert not invalid.is_valid()
        assert set(invalid.errors) == {"created_by"}
