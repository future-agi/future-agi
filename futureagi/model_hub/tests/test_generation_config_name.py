"""A saved generation configuration names its own dataset and can be replayed."""

import json
import uuid

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.tests import test_generate_rows as base
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

grid = base.grid
config = base.config
model = base.model
path = base.path
CREATE = base.CREATE
APPEND = {"generation_mode": "append"}


def without_name(config, variant):
    requirements = {k: v for k, v in config["dataset"].items() if k != "name"}
    if variant == "blank":
        requirements["name"] = ""
    return {**config, "dataset": requirements}


def named(config, name):
    return {**config, "dataset": {**config["dataset"], "name": name}}


def replay(client, dataset):
    saved = client.get(path(dataset, "synthetic-config"), APPEND)
    assert saved.status_code == 200, saved.content
    data = saved.json()["result"]["data"]
    repeated = client.put(
        path(dataset, "update-synthetic-config"),
        {**data, "regenerate": True},
        format="json",
    )
    return data, repeated


def prompt(call):
    return json.loads(call.messages[-1]["content"])["requirements"]


@pytest.mark.django_db
@pytest.mark.parametrize("variant", ["omitted", "blank"])
def test_unnamed_request_saves_a_replayable_configuration(
    auth_client, grid, config, model, variant
):
    request = without_name(config, variant)
    first = auth_client.post(path(grid, "add_synthetic_data"), request, format="json")
    assert first.status_code == 200, first.content
    data, repeated = replay(auth_client, grid)
    assert repeated.status_code == 200, repeated.content
    assert data == named(request, grid.name)
    assert repeated.json()["result"]["data"]["dataset_name"] == grid.name
    assert Row.objects.filter(dataset=grid).count() == 5
    assert len(model) == 2
    assert model[0].messages == model[1].messages
    assert prompt(model[0])["name"] == grid.name
    grid.refresh_from_db()
    assert grid.dataset_config["row_generation"] == named(request, grid.name)


@pytest.mark.django_db
def test_renamed_dataset_replays_under_its_current_name(
    auth_client, grid, config, model
):
    assert (
        auth_client.post(
            path(grid, "add_synthetic_data"), config, format="json"
        ).status_code
        == 200
    )
    Dataset.objects.filter(id=grid.id).update(name="Renamed generation target")
    data, repeated = replay(auth_client, grid)
    assert data["dataset"]["name"] == "Renamed generation target"
    assert repeated.status_code == 200, repeated.content
    assert Row.objects.filter(dataset=grid).count() == 5


@pytest.mark.django_db
def test_configuration_saved_without_a_name_replays(auth_client, grid, config, model):
    # A configuration persisted with no name stays replayable from its own readback.
    saved = without_name(config, "omitted")
    stored = {**(grid.dataset_config or {}), "row_generation": saved}
    Dataset.objects.filter(id=grid.id).update(dataset_config=stored)
    data, repeated = replay(auth_client, grid)
    assert data == named(saved, grid.name)
    assert repeated.status_code == 200, repeated.content
    assert Row.objects.filter(dataset=grid).count() == 3


def _tenant(label):
    clear_workspace_context()
    org = Organization.objects.create(name=f"Generation {label}")
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=f"generation-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="generation-test-password",
        name=f"Generation {label}",
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
        name=f"Generation {label}",
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
    clear_workspace_context()
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


@pytest.fixture
def foreign(db, config, model):
    """Another organization's dataset with its own saved configuration."""
    client = _tenant("foreign")
    name = f"foreign-target-{uuid.uuid4().hex[:8]}"
    created = client.post(
        CREATE,
        {
            "dataset_name": name,
            "number_of_rows": 1,
            "columns": [{"name": "question", "type": "text", "role": "input"}],
        },
        format="json",
    )
    assert created.status_code == 200, created.content
    dataset = Dataset.objects.get(id=created.json()["result"]["dataset_id"])
    saved = client.post(
        path(dataset, "add_synthetic_data"), named(config, name), format="json"
    )
    assert saved.status_code == 200, saved.content
    model.clear()
    dataset.refresh_from_db()
    yield dataset, dataset.dataset_config, Row.objects.filter(dataset=dataset).count()
    client.stop_workspace_injection()
    clear_workspace_context()


@pytest.mark.django_db
def test_foreign_dataset_name_is_never_saved_or_returned(
    auth_client, grid, config, model, foreign
):
    dataset, _, _ = foreign
    first = auth_client.post(
        path(grid, "add_synthetic_data"),
        named(config, dataset.name),
        format="json",
    )
    assert first.status_code == 200, first.content
    data, repeated = replay(auth_client, grid)
    assert repeated.status_code == 200, repeated.content
    assert data == config
    assert dataset.name not in json.dumps(data)
    assert dataset.name not in repeated.content.decode()
    assert all(prompt(call)["name"] == grid.name for call in model)
    grid.refresh_from_db()
    assert dataset.name not in json.dumps(grid.dataset_config)


@pytest.mark.django_db
def test_foreign_target_is_refused_without_revealing_its_name(
    auth_client, config, model, foreign
):
    dataset, before, rows = foreign
    request = without_name(config, "omitted")
    responses = [
        auth_client.post(path(dataset, "add_synthetic_data"), request, format="json"),
        auth_client.get(path(dataset, "synthetic-config"), APPEND),
        auth_client.put(
            path(dataset, "update-synthetic-config"),
            {**named(request, dataset.name), "regenerate": True},
            format="json",
        ),
    ]
    assert [response.status_code for response in responses] == [404, 404, 404]
    assert all(dataset.name not in r.content.decode() for r in responses)
    assert model == []
    dataset.refresh_from_db()
    assert dataset.dataset_config == before
    assert Row.objects.filter(dataset=dataset).count() == rows
