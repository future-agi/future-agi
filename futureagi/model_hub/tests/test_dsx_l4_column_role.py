"""A user column's role is stored and read back.

The dataset grid sets a column's role (Input / Expected output / Metadata) when a
column is added (``add_static_column``) and from the column menu
(``edit_dataset_behavior``), and reads it from ``get-dataset-table``'s
``column_config``. Dev dropped the role on both writes and never returned it.
Every write is also checked with two organizations: tenant B can neither set
a role on, nor read the roles of, tenant A's dataset.
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


def _add_url(dataset_id):
    return f"/model-hub/develops/{dataset_id}/add_static_column/"


def _behavior_url(dataset_id):
    return f"/model-hub/develops/{dataset_id}/edit_dataset_behavior/"


def _table(client, dataset_id):
    return client.get(
        f"/model-hub/develops/{dataset_id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )


def _roles(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return {
        c["name"]: c.get("role", "MISSING")
        for c in response.json()["result"]["column_config"]
    }


def _second_tenant(label="Tenant B"):
    """A separate organization with its own owner, workspace and API client."""
    from conftest import WorkspaceAwareAPIClient

    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"{label} {suffix}")
    user = User.objects.create_user(
        email=f"l4-role-b-{suffix}@futureagi.com",
        password="testpassword123",
        name=f"{label} Owner",
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
    workspace = Workspace.no_workspace_objects.create(
        name=f"{label} Workspace {suffix}",
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
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return org, workspace, client


@pytest.fixture
def tenant_b(db):
    org, workspace, client = _second_tenant()
    yield org, workspace, client
    client.stop_workspace_injection()


@pytest.fixture
def seeded(organization, workspace):
    """Tenant A's dataset: one user column with a row, one generated column."""
    dataset = Dataset.no_workspace_objects.create(
        name=f"l4-role-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    question = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    generated = Column.no_workspace_objects.create(
        name="answer-run",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, column=question, row=row, value="l4-a-secret-question"
    )
    dataset.column_order = [str(question.id), str(generated.id)]
    dataset.column_config = {
        str(question.id): {"is_visible": True, "is_frozen": None},
        str(generated.id): {"is_visible": True, "is_frozen": None},
    }
    dataset.save()
    return dataset, question, generated


@pytest.mark.django_db(transaction=True)
def test_add_static_column_stores_the_role_and_the_table_returns_it(
    auth_client, seeded
):
    dataset, _question, _generated = seeded

    response = auth_client.post(
        _add_url(dataset.id),
        {
            "new_column_name": "expected_answer",
            "column_type": "text",
            "role": "expected",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    column = Column.no_workspace_objects.get(dataset=dataset, name="expected_answer")
    dataset.refresh_from_db()
    assert dataset.column_config[str(column.id)] == {
        "is_visible": True,
        "is_frozen": None,
        "role": "expected",
    }
    roles = _roles(_table(auth_client, dataset.id))
    assert roles == {
        "question": None,
        "answer-run": None,
        "expected_answer": "expected",
    }


@pytest.mark.django_db(transaction=True)
def test_add_static_column_with_no_role_stores_none(auth_client, seeded):
    dataset, _question, _generated = seeded

    response = auth_client.post(
        _add_url(dataset.id),
        {"new_column_name": "notes", "column_type": "text", "role": "none"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    column = Column.no_workspace_objects.get(dataset=dataset, name="notes")
    dataset.refresh_from_db()
    assert "role" not in dataset.column_config[str(column.id)]
    assert _roles(_table(auth_client, dataset.id))["notes"] is None


@pytest.mark.django_db
def test_add_static_column_refuses_an_unknown_role(auth_client, seeded):
    dataset, _question, _generated = seeded

    response = auth_client.post(
        _add_url(dataset.id),
        {"new_column_name": "judge", "column_type": "text", "role": "judge"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Column.no_workspace_objects.filter(
        dataset=dataset, name="judge"
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_set_and_clear_a_role_from_the_column_menu(auth_client, seeded):
    dataset, question, _generated = seeded

    set_role = auth_client.put(
        _behavior_url(dataset.id),
        {"column_config": {str(question.id): {"role": "metadata"}}},
        format="json",
    )
    assert set_role.status_code == status.HTTP_200_OK, set_role.content
    dataset.refresh_from_db()
    assert dataset.column_config[str(question.id)] == {
        "is_visible": True,
        "is_frozen": None,
        "role": "metadata",
    }
    assert _roles(_table(auth_client, dataset.id))["question"] == "metadata"

    # columnRole.js sends "none" for "None".
    clear = auth_client.put(
        _behavior_url(dataset.id),
        {"column_config": {str(question.id): {"role": "none"}}},
        format="json",
    )
    assert clear.status_code == status.HTTP_200_OK, clear.content
    dataset.refresh_from_db()
    assert "role" not in dataset.column_config[str(question.id)]
    assert _roles(_table(auth_client, dataset.id))["question"] is None


@pytest.mark.django_db
def test_column_menu_refuses_an_unknown_role(auth_client, seeded):
    dataset, question, _generated = seeded

    response = auth_client.put(
        _behavior_url(dataset.id),
        {"column_config": {str(question.id): {"role": "judge"}}},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    dataset.refresh_from_db()
    assert "role" not in dataset.column_config[str(question.id)]


@pytest.mark.django_db
def test_a_generated_column_takes_no_role(auth_client, seeded):
    dataset, _question, generated = seeded

    response = auth_client.put(
        _behavior_url(dataset.id),
        {"column_config": {str(generated.id): {"role": "expected"}}},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    dataset.refresh_from_db()
    assert "role" not in dataset.column_config[str(generated.id)]


@pytest.mark.django_db(transaction=True)
def test_other_org_cannot_set_or_read_roles(auth_client, seeded, tenant_b):
    dataset, question, _generated = seeded
    _org_b, _ws_b, client_b = tenant_b
    own = auth_client.put(
        _behavior_url(dataset.id),
        {"column_config": {str(question.id): {"role": "input"}}},
        format="json",
    )
    assert own.status_code == status.HTTP_200_OK, own.content

    # B cannot change A's role ...
    put_b = client_b.put(
        _behavior_url(dataset.id),
        {"column_config": {str(question.id): {"role": "expected"}}},
        format="json",
    )
    assert put_b.status_code == status.HTTP_404_NOT_FOUND, put_b.content

    # ... nor add a column with a role to A's dataset ...
    add_b = client_b.post(
        _add_url(dataset.id),
        {"new_column_name": "b_injected", "column_type": "text", "role": "expected"},
        format="json",
    )
    assert add_b.status_code == status.HTTP_404_NOT_FOUND, add_b.content
    assert not Column.no_workspace_objects.filter(name="b_injected").exists()

    # ... nor read A's table (no column names, roles or values leak).
    read_b = _table(client_b, dataset.id)
    assert read_b.status_code != status.HTTP_200_OK
    body_b = read_b.content.decode()
    assert "l4-a-secret-question" not in body_b
    assert str(question.id) not in body_b

    dataset.refresh_from_db()
    assert dataset.column_config[str(question.id)]["role"] == "input"
    # Positive control: A still reads her own role.
    assert _roles(_table(auth_client, dataset.id))["question"] == "input"
