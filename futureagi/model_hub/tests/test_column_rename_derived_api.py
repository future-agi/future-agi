"""Column rename updates dataset-local references, not shared prompt assets."""

import json
import uuid
from copy import deepcopy

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.run_prompt import PromptTemplate, PromptVersion, RunPrompter
from model_hub.views.develop_dataset import UpdateColumnNameView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


def _client(user, organization, workspace):
    clear_workspace_context()
    client = APIClient()
    login = client.post(
        "/accounts/token/",
        {"email": user.email, "password": "testpassword123"},
        format="json",
    )
    assert login.status_code == 200, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


@pytest.fixture
def owner_client(user, organization, workspace):
    return _client(user, organization, workspace)


@pytest.fixture
def other_tenant():
    clear_workspace_context()
    organization = Organization.objects.create(name="Rename tenant B")
    user = User.objects.create_user(
        email=f"rename-{uuid.uuid4().hex}@futureagi.com",
        password="testpassword123",
        name="Rename owner B",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.no_workspace_objects.create(
        name="Rename workspace B",
        organization=organization,
        created_by=user,
        is_default=True,
        is_active=True,
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
    return organization, workspace, _client(user, organization, workspace)


def _seed(organization, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Rename dataset",
        organization=organization,
        workspace=workspace,
    )
    config = {
        "derived_variables": {
            "full_variables": ["A.user.name", "A.score", "Another.score"],
            "paths": ["user.name", "score"],
            "schema": {"score": {"type": "number"}},
            "is_json": True,
        },
        "unrelated": {"keep": "A.score"},
    }
    prompter = RunPrompter.no_workspace_objects.create(
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        name="Shared name",
        model="fixture-model",
        messages=[],
        run_prompt_config=deepcopy(config),
    )
    column = Column.no_workspace_objects.create(
        dataset=dataset,
        name="A",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=prompter.id,
    )
    dataset.column_order = [str(column.id)]
    dataset.column_config = {str(column.id): {"is_visible": True, "is_frozen": None}}
    dataset.save(update_fields=["column_order", "column_config"])
    template = PromptTemplate.no_workspace_objects.create(
        name="Shared name",
        organization=organization,
        workspace=workspace,
    )
    versions = [
        PromptVersion.no_workspace_objects.create(
            original_template=template,
            template_version=f"v{index}",
            metadata={
                "derived_variables": {"A": deepcopy(config["derived_variables"])},
                "notes": f"immutable version {index}",
            },
        )
        for index in (1, 2)
    ]
    return dataset, column, prompter, versions


@pytest.fixture
def seeded(organization, workspace):
    return _seed(organization, workspace)


def _rename(client, dataset, column):
    return client.put(
        f"/model-hub/develops/{dataset.id}/update_column_name/{column.id}/",
        {"new_column_name": "B"},
        format="json",
    )


def _metadata_bytes(versions):
    # Read PostgreSQL's stored JSON representation, not a mutable fixture object.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, metadata::text FROM model_hub_promptversion WHERE id = ANY(%s) ORDER BY id",
            [[version.id for version in versions]],
        )
        return [(str(pk), value.encode("utf-8")) for pk, value in cursor.fetchall()]


def test_rename_persists_derived_references_and_preserves_prompt_versions(
    owner_client, seeded
):
    dataset, column, prompter, versions = seeded
    metadata_before = _metadata_bytes(versions)
    expected = deepcopy(prompter.run_prompt_config)
    expected["derived_variables"]["full_variables"] = [
        "B.user.name",
        "B.score",
        "Another.score",
    ]

    response = _rename(owner_client, dataset, column)

    assert response.status_code == 200, response.content
    column.refresh_from_db()
    prompter.refresh_from_db()
    assert column.name == "B"
    assert prompter.run_prompt_config == expected
    assert _metadata_bytes(versions) == metadata_before
    # The actual read API must also reflect the persisted rename.
    read = owner_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )
    assert read.status_code == 200, read.content
    assert any(c["name"] == "B" for c in read.json()["result"]["column_config"])


def test_rename_propagates_legacy_null_org_prompter(owner_client, seeded):
    dataset, column, prompter, versions = seeded
    # QuerySet.update preserves the historical NULL instead of save-time defaults.
    RunPrompter.no_workspace_objects.filter(id=prompter.id).update(organization=None)
    prompter.refresh_from_db()
    assert prompter.organization_id is None
    metadata_before = _metadata_bytes(versions)
    expected = deepcopy(prompter.run_prompt_config)
    expected["derived_variables"]["full_variables"] = [
        "B.user.name",
        "B.score",
        "Another.score",
    ]

    for _ in range(2):
        response = _rename(owner_client, dataset, column)
        assert response.status_code == 200, response.content
        column.refresh_from_db()
        prompter.refresh_from_db()
        assert column.name == "B"
        assert prompter.run_prompt_config == expected
        assert prompter.organization_id is None
        assert _metadata_bytes(versions) == metadata_before

    read = owner_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )
    assert read.status_code == 200, read.content
    assert any(c["name"] == "B" for c in read.json()["result"]["column_config"])


@pytest.mark.parametrize("mismatch", ["foreign-organization", "null-other-dataset"])
def test_rename_skips_out_of_scope_prompter(
    owner_client, seeded, other_tenant, mismatch
):
    dataset, column, prompter, versions = seeded
    organization_b, _, _ = other_tenant
    if mismatch == "foreign-organization":
        # Keep dataset and workspace matching: the organization predicate alone
        # must reject an inconsistent foreign-org prompter reference.
        changes = {"organization_id": organization_b.id}
    else:
        different_dataset = Dataset.no_workspace_objects.create(
            name="Other dataset in same workspace",
            organization=dataset.organization,
            workspace=dataset.workspace,
        )
        changes = {"organization_id": None, "dataset_id": different_dataset.id}
    RunPrompter.no_workspace_objects.filter(id=prompter.id).update(**changes)
    before = deepcopy(prompter.run_prompt_config)
    metadata_before = _metadata_bytes(versions)

    response = _rename(owner_client, dataset, column)

    assert response.status_code == 200, response.content
    column.refresh_from_db()
    prompter.refresh_from_db()
    assert column.name == "B"
    assert prompter.run_prompt_config == before
    for field, value in changes.items():
        assert getattr(prompter, field) == value
    assert _metadata_bytes(versions) == metadata_before


def test_rename_locks_prompter_before_column(owner_client, seeded):
    dataset, column, _, _ = seeded
    with CaptureQueriesContext(connection) as queries:
        response = _rename(owner_client, dataset, column)
    assert response.status_code == 200, response.content
    locks = [
        query["sql"]
        for query in queries
        if "FOR UPDATE" in query["sql"] or "FOR NO KEY UPDATE" in query["sql"]
    ]
    tables = ["model_hub_dataset", "model_hub_runprompter", "model_hub_column"]
    modes = ["NO KEY UPDATE", "UPDATE", "NO KEY UPDATE"]
    assert len(locks) >= len(tables), locks
    for query, table, mode in zip(locks[:3], tables, modes, strict=True):
        assert f'FROM "{table}"' in query, query
        assert f'FOR {mode} OF "{table}"' in query, query


def test_rename_uses_one_lookup_helper_inside_atomic(owner_client, seeded, monkeypatch):
    dataset, column, _, _ = seeded
    lookup = UpdateColumnNameView._locked_dataset_and_column
    calls = []
    savepoint_depth = len(connection.savepoint_ids)

    def capture(self, request, dataset_id, column_id):
        assert connection.in_atomic_block
        assert len(connection.savepoint_ids) == savepoint_depth + 1
        calls.append((dataset_id, column_id))
        return lookup(self, request, dataset_id, column_id)

    monkeypatch.setattr(UpdateColumnNameView, "_locked_dataset_and_column", capture)
    response = _rename(owner_client, dataset, column)

    assert response.status_code == 200, response.content
    assert calls == [(dataset.id, column.id)]


def test_successful_rename_does_not_swallow_an_attribute_error(owner_client, seeded):
    dataset, column, _, _ = seeded
    with capture_logs() as events:
        response = _rename(owner_client, dataset, column)
    assert response.status_code == 200, response.content
    failures = [
        event
        for event in events
        if event.get("log_level") in {"warning", "error", "exception"}
        and "derived variables" in event.get("event", "")
    ]
    assert failures == [], failures


@pytest.mark.parametrize(
    "null_organization", [False, True], ids=["owned-org", "null-org"]
)
def test_other_tenant_same_named_prompter_and_versions_are_untouched(
    owner_client,
    seeded,
    other_tenant,
    null_organization,
):
    dataset, column, prompter, versions = seeded
    organization_b, workspace_b, client_b = other_tenant
    dataset_b, column_b, prompter_b, versions_b = _seed(organization_b, workspace_b)
    if null_organization:
        RunPrompter.no_workspace_objects.filter(
            id__in=[prompter.id, prompter_b.id]
        ).update(organization=None)
        for scoped_prompter in (prompter, prompter_b):
            scoped_prompter.refresh_from_db()
            assert scoped_prompter.organization_id is None
    config_before = json.dumps(prompter_b.run_prompt_config, sort_keys=True).encode()
    metadata_before = _metadata_bytes(versions + versions_b)

    assert _rename(owner_client, dataset, column).status_code == 200
    denied = _rename(owner_client, dataset_b, column_b)
    # The existing view maps a missing scoped object to its generic error envelope.
    assert denied.status_code in {403, 404, 500}, denied.content
    assert _rename(client_b, dataset, column).status_code in {403, 404, 500}
    column_b.refresh_from_db()
    prompter_b.refresh_from_db()
    assert column_b.name == "A"
    assert (
        json.dumps(prompter_b.run_prompt_config, sort_keys=True).encode()
        == config_before
    )
    assert _metadata_bytes(versions + versions_b) == metadata_before
    own_read = client_b.get(
        f"/model-hub/develops/{dataset_b.id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )
    assert own_read.status_code == 200, own_read.content
    assert any(c["name"] == "A" for c in own_read.json()["result"]["column_config"])


@pytest.mark.parametrize("source_id", [None, "missing"])
def test_rename_without_a_run_prompter(owner_client, seeded, source_id):
    dataset, column, prompter, versions = seeded
    column.source_id = uuid.uuid4() if source_id else None
    column.save(update_fields=["source_id"])
    before = deepcopy(prompter.run_prompt_config)
    metadata_before = _metadata_bytes(versions)
    response = _rename(owner_client, dataset, column)
    assert response.status_code == 200, response.content
    column.refresh_from_db()
    prompter.refresh_from_db()
    assert column.name == "B"
    assert prompter.run_prompt_config == before
    assert _metadata_bytes(versions) == metadata_before


def test_invalid_stored_derived_config_returns_error_instead_of_success(
    owner_client, seeded
):
    dataset, column, prompter, versions = seeded
    # Valid JSON but invalid historical shape causes the real helper's .get to fail.
    prompter.run_prompt_config = {"derived_variables": ["A.score"]}
    prompter.save(update_fields=["run_prompt_config"])
    metadata_before = _metadata_bytes(versions)
    response = _rename(owner_client, dataset, column)
    assert response.status_code == 500, response.content
    column.refresh_from_db()
    prompter.refresh_from_db()
    assert column.name == "A"
    assert prompter.run_prompt_config == {"derived_variables": ["A.score"]}
    assert _metadata_bytes(versions) == metadata_before


@pytest.mark.parametrize(
    "null_organization", [False, True], ids=["owned-org", "null-org"]
)
@pytest.mark.parametrize("fail_after_propagation", [False, True])
def test_propagation_failure_rolls_back_both_writes_and_allows_retry(
    owner_client, seeded, monkeypatch, fail_after_propagation, null_organization
):
    dataset, column, prompter, versions = seeded
    if null_organization:
        RunPrompter.no_workspace_objects.filter(id=prompter.id).update(
            organization=None
        )
        prompter.refresh_from_db()
        assert prompter.organization_id is None
    ownership_before = (
        prompter.organization_id,
        prompter.workspace_id,
        prompter.dataset_id,
    )
    before = deepcopy(prompter.run_prompt_config)
    metadata_before = _metadata_bytes(versions)
    propagate = UpdateColumnNameView._update_derived_variables_for_renamed_column
    injected_failures = []

    def fail(self, dataset, column, old_name, new_name):
        if fail_after_propagation:
            # Persist through the real helper before simulating a later failure.
            propagate(self, dataset, column, old_name, new_name)
            stored = RunPrompter.no_workspace_objects.get(id=prompter.id)
            assert stored.run_prompt_config["derived_variables"]["full_variables"] == [
                "B.user.name",
                "B.score",
                "Another.score",
            ]
        injected_failures.append((old_name, new_name))
        raise RuntimeError("Injected propagation failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            UpdateColumnNameView, "_update_derived_variables_for_renamed_column", fail
        )
        response = _rename(owner_client, dataset, column)

    assert response.status_code == 500, response.content
    assert injected_failures == [("A", "B")]
    column.refresh_from_db()
    prompter.refresh_from_db()
    assert (column.name, prompter.run_prompt_config) == ("A", before)
    assert (
        prompter.organization_id,
        prompter.workspace_id,
        prompter.dataset_id,
    ) == ownership_before
    assert _metadata_bytes(versions) == metadata_before

    # An identical request after recovery must perform both writes.
    response = _rename(owner_client, dataset, column)
    assert response.status_code == 200, response.content
    column.refresh_from_db()
    prompter.refresh_from_db()
    expected = deepcopy(before)
    expected["derived_variables"]["full_variables"] = [
        "B.user.name",
        "B.score",
        "Another.score",
    ]
    assert (column.name, prompter.run_prompt_config) == ("B", expected)
    assert (
        prompter.organization_id,
        prompter.workspace_id,
        prompter.dataset_id,
    ) == ownership_before
    assert _metadata_bytes(versions) == metadata_before


def test_same_name_retry_reexecutes_propagation_and_is_idempotent(
    owner_client, seeded, monkeypatch
):
    dataset, column, prompter, versions = seeded
    assert _rename(owner_client, dataset, column).status_code == 200
    prompter.refresh_from_db()
    before = deepcopy(prompter.run_prompt_config)
    metadata_before = _metadata_bytes(versions)
    propagate = UpdateColumnNameView._update_derived_variables_for_renamed_column
    calls = []

    def fail(self, dataset, column, old_name, new_name):
        calls.append((old_name, new_name))
        propagate(self, dataset, column, old_name, new_name)
        raise RuntimeError("Injected same-name propagation failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            UpdateColumnNameView, "_update_derived_variables_for_renamed_column", fail
        )
        response = _rename(owner_client, dataset, column)

    assert response.status_code == 500, response.content
    assert calls == [("B", "B")]
    for _ in range(2):
        response = _rename(owner_client, dataset, column)
        assert response.status_code == 200, response.content
        column.refresh_from_db()
        prompter.refresh_from_db()
        assert (column.name, prompter.run_prompt_config) == ("B", before)
        assert _metadata_bytes(versions) == metadata_before


@pytest.mark.parametrize("owner", ["same-tenant", "other-tenant"])
def test_rename_never_touches_null_tenancy_prompter_of_another_dataset(
    owner_client, seeded, other_tenant, owner
):
    # A legacy prompter with neither an organization nor a workspace is only in
    # scope through its own dataset. A column of another dataset that names it
    # must never edit it, whichever tenant that other dataset belongs to.
    dataset, column, prompter, versions = seeded
    if owner == "same-tenant":
        organization, workspace = dataset.organization, dataset.workspace
    else:
        organization, workspace, _ = other_tenant
    elsewhere = Dataset.no_workspace_objects.create(
        name="Prompter's own dataset",
        organization=organization,
        workspace=workspace,
    )
    RunPrompter.no_workspace_objects.filter(id=prompter.id).update(
        organization=None, workspace=None, dataset=elsewhere
    )
    prompter.refresh_from_db()
    identity = (None, None, elsewhere.id)
    assert (
        prompter.organization_id,
        prompter.workspace_id,
        prompter.dataset_id,
    ) == identity
    config_before = json.dumps(prompter.run_prompt_config, sort_keys=True).encode()
    metadata_before = _metadata_bytes(versions)

    response = _rename(owner_client, dataset, column)

    assert response.status_code == 200, response.content
    column.refresh_from_db()
    prompter.refresh_from_db()
    assert column.name == "B"
    assert (
        json.dumps(prompter.run_prompt_config, sort_keys=True).encode() == config_before
    )
    assert (
        prompter.organization_id,
        prompter.workspace_id,
        prompter.dataset_id,
    ) == identity
    assert _metadata_bytes(versions) == metadata_before
