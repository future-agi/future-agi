"""The dataset list and the grid's immediate writes are tenant-scoped.

The Datasets page lists datasets through
``GET develops/get-datasets/`` and writes the grid straight away through
``POST develops/{id}/update_cell_value/`` and ``DELETE develops/{id}/delete_row/``.
Two organizations, one user each. Every negative check is paired with a
positive control (the owner can do it), and every refused write is checked
for "nothing changed", not only for its status code.
"""

import json
import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

LIST = "/model-hub/develops/get-datasets/"


def _tenant(label):
    """A new organization with its own owner, default workspace and dataset."""
    clear_workspace_context()
    org = Organization.objects.create(name=f"TenantOrg {label}")
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=f"tenant-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="tenant-test-password",
        name=f"Tenant{label}",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=f"TenantWorkspace {label}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    set_workspace_context(workspace=workspace, organization=org, user=user)
    dataset = Dataset.objects.create(
        name=f"tenant-{label}-only-{uuid.uuid4().hex[:6]}",
        organization=org,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )
    column = Column.objects.create(
        name="question",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    rows = [Row.objects.create(dataset=dataset, order=i) for i in range(2)]
    cells = [
        Cell.objects.create(
            dataset=dataset, row=row, column=column, value=f"{label}-value-{i}"
        )
        for i, row in enumerate(rows)
    ]
    clear_workspace_context()
    return {
        "org": org,
        "user": user,
        "workspace": workspace,
        "dataset": dataset,
        "column": column,
        "rows": rows,
        "cells": cells,
    }


def _client(tenant):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=tenant["user"])
    client.set_workspace(tenant["workspace"])
    return client


def _names(response):
    body = response.json()
    return [d["name"] for d in body["result"]["datasets"]]


@pytest.fixture
def tenants(db):
    a = _tenant("a")
    b = _tenant("b")
    clients = {"a": _client(a), "b": _client(b)}
    yield a, b, clients
    for client in clients.values():
        client.stop_workspace_injection()
    clear_workspace_context()


@pytest.mark.django_db
class TestDatasetListIsTenantScoped:
    def test_each_org_lists_only_its_own_datasets(self, tenants):
        a, b, clients = tenants
        # The exact query the dataset list sends to the backend
        # (realDatasetListParams): page, page_size, sort; no other field.
        params = {
            "page": 0,
            "page_size": 25,
            "sort": json.dumps([{"column_id": "created_at", "type": "descending"}]),
        }
        ra = clients["a"].get(LIST, params)
        rb = clients["b"].get(LIST, params)
        assert ra.status_code == status.HTTP_200_OK, ra.content
        assert rb.status_code == status.HTTP_200_OK, rb.content
        assert _names(ra) == [a["dataset"].name]
        assert _names(rb) == [b["dataset"].name]
        assert ra.json()["result"]["total_count"] == 1
        assert rb.json()["result"]["total_count"] == 1

    def test_search_never_finds_another_orgs_dataset(self, tenants):
        a, b, clients = tenants
        own = clients["a"].get(
            LIST, {"search_text": a["dataset"].name, "page": 0, "page_size": 10}
        )
        assert own.status_code == status.HTTP_200_OK
        assert _names(own) == [a["dataset"].name]
        other = clients["b"].get(
            LIST, {"search_text": a["dataset"].name, "page": 0, "page_size": 10}
        )
        assert other.status_code == status.HTTP_200_OK
        assert _names(other) == []
        assert other.json()["result"]["total_count"] == 0

    def test_list_refuses_unsupported_query_fields(self, tenants):
        # The strict serializer rejects unsupported fields, so the dataset
        # list must omit them from its request.
        _a, _b, clients = tenants
        for extra in (
            {"filters": "[]"},
            {"group_by": "owner"},
            {"starred_first": "false"},
        ):
            r = clients["a"].get(LIST, {"page": 0, "page_size": 25, **extra})
            assert r.status_code == status.HTTP_400_BAD_REQUEST, (extra, r.content)

    def test_list_refuses_a_page_size_over_100(self, tenants):
        _a, _b, clients = tenants
        r = clients["a"].get(LIST, {"page": 0, "page_size": 101})
        assert r.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestCellWritesAreTenantScoped:
    def _post(self, client, dataset_id, row_id, column_id, value):
        return client.post(
            f"/model-hub/develops/{dataset_id}/update_cell_value/",
            {"row_id": str(row_id), "column_id": str(column_id), "new_value": value},
            format="json",
        )

    def test_owner_can_edit_own_cell(self, tenants):
        a, _b, clients = tenants
        cell = a["cells"][0]
        r = self._post(
            clients["a"], a["dataset"].id, cell.row_id, a["column"].id, "a-edited"
        )
        assert r.status_code == status.HTTP_200_OK, r.content
        cell.refresh_from_db()
        assert cell.value == "a-edited"

    def test_other_org_cannot_edit_through_the_owners_dataset_url(self, tenants):
        a, _b, clients = tenants
        cell = a["cells"][0]
        r = self._post(
            clients["b"], a["dataset"].id, cell.row_id, a["column"].id, "b-was-here"
        )
        assert r.status_code >= 400, r.content
        cell.refresh_from_db()
        assert cell.value == "a-value-0"

    def test_other_org_cannot_edit_through_its_own_dataset_url(self, tenants):
        # The view's lookup used to fall back to the cell by row and column
        # alone, whatever dataset the URL named.
        a, b, clients = tenants
        cell = a["cells"][1]
        r = self._post(
            clients["b"], b["dataset"].id, cell.row_id, a["column"].id, "b-was-here"
        )
        assert r.status_code >= 400, r.content
        cell.refresh_from_db()
        assert cell.value == "a-value-1"

    def test_refusal_does_not_echo_the_other_orgs_value(self, tenants):
        a, b, clients = tenants
        cell = a["cells"][0]
        r = self._post(clients["b"], b["dataset"].id, cell.row_id, a["column"].id, "x")
        assert r.status_code >= 400
        assert b"a-value-0" not in r.content


@pytest.mark.django_db
class TestRowDeletesAreTenantScoped:
    def _delete(self, client, dataset_id, row_ids):
        return client.delete(
            f"/model-hub/develops/{dataset_id}/delete_row/",
            {"row_ids": [str(r) for r in row_ids], "selected_all_rows": False},
            format="json",
        )

    def test_owner_can_delete_own_row(self, tenants):
        a, _b, clients = tenants
        row = a["rows"][0]
        r = self._delete(clients["a"], a["dataset"].id, [row.id])
        assert r.status_code == status.HTTP_200_OK, r.content
        assert not Row.objects.filter(id=row.id).exists()  # soft-deleted
        assert Row.all_objects.filter(id=row.id, deleted=True).exists()

    def test_other_org_cannot_delete_through_the_owners_dataset_url(self, tenants):
        a, _b, clients = tenants
        row = a["rows"][0]
        r = self._delete(clients["b"], a["dataset"].id, [row.id])
        assert r.status_code >= 400, r.content
        assert Row.all_objects.filter(id=row.id, deleted=False).exists()

    def test_other_org_cannot_delete_through_its_own_dataset_url(self, tenants):
        a, b, clients = tenants
        row = a["rows"][1]
        r = self._delete(clients["b"], b["dataset"].id, [row.id])
        assert r.status_code >= 400, r.content
        assert Row.all_objects.filter(id=row.id, deleted=False).exists()
        # B's own rows are untouched by the refused request.
        assert Row.objects.filter(dataset=b["dataset"]).count() == 2

    def test_other_org_cannot_delete_all_rows_of_the_owners_dataset(self, tenants):
        a, _b, clients = tenants
        r = clients["b"].delete(
            f"/model-hub/develops/{a['dataset'].id}/delete_row/",
            {"row_ids": [], "selected_all_rows": True},
            format="json",
        )
        assert r.status_code >= 400, r.content
        assert Row.objects.filter(dataset=a["dataset"]).count() == 2
