"""The workspace tag registry and an experiment's tags (EXP-E2E-010).

The page (frontend sections/common/Tags/useTags.js) reads
GET /model-hub/tags/?include=experiments as result.results [{id, name, color,
description, used_on}] and result.experiments {experiment_id: [tag_id, ...]},
creates with POST {name}, edits with PATCH {name} | {color} | {description},
deletes with DELETE, and replaces an experiment's tags with
PUT /model-hub/experiments/v2/{id}/tags/ {tag_ids}. An error's sentence is
the response's `result`. Another organization, or another workspace, sees
none of it and changes none of it.
"""

from __future__ import annotations

import uuid
from importlib import import_module
from unittest.mock import patch

import pytest
from django.db import migrations
from django.db.migrations.loader import MigrationLoader
from django.urls import resolve

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.workspace_tags import ExperimentTag, WorkspaceTag
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

TAGS = "/model-hub/tags/"


def tag_url(tag_id):
    return f"/model-hub/tags/{tag_id}/"


def experiment_tags_url(experiment_id):
    return f"/model-hub/experiments/v2/{experiment_id}/tags/"


def make_experiment(organization, workspace, user, name="Tagged experiment"):
    dataset = Dataset.no_workspace_objects.create(
        name=f"{name} data", organization=organization, workspace=workspace, user=user
    )
    return ExperimentsTable.no_workspace_objects.create(
        name=name, dataset=dataset, user=user, status="Completed"
    )


@pytest.fixture
def experiment(organization, workspace, user):
    return make_experiment(organization, workspace, user)


@pytest.fixture
def second_user(db):
    organization = Organization.objects.create(name="Other Organization")
    other = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=other,
        organization=organization,
        defaults={"role": OrganizationRoles.OWNER, "level": Level.OWNER, "is_active": True},
    )
    workspace = Workspace.objects.create(
        name="Other Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=other,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=other,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return other


@pytest.fixture
def second_workspace(second_user):
    return Workspace.objects.get(organization=second_user.organization, is_default=True)


@pytest.fixture
def other_org_client(second_user, second_workspace):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=second_user)
    client.set_workspace(second_workspace)
    yield client
    client.stop_workspace_injection()


def create(client, name, **extra):
    response = client.post(TAGS, {"name": name, **extra}, format="json")
    assert response.status_code == 200, response.data
    return response.data["result"]


def listed(client):
    response = client.get(TAGS, {"include": "experiments"})
    assert response.status_code == 200, response.data
    return response.data["result"]


def stored_links(experiment):
    return sorted(
        str(t)
        for t in ExperimentTag.objects.filter(experiment=experiment).values_list(
            "tag_id", flat=True
        )
    )


# --- The registry ---------------------------------------------------------


def test_create_normalises_the_name_and_takes_colours_in_first_use_order(auth_client):
    first = create(auth_client, "  release   candidate ")
    assert first["name"] == "release candidate"
    assert first["color"] == "blue"
    assert first["description"] == ""
    assert first["used_on"] == {"experiments": 0, "datasets": 0, "rows": 0}
    assert first["created_by"] == "Test User"
    second = create(auth_client, "golden")
    assert second["color"] == "green"
    chosen = create(auth_client, "support", color="teal", description="Support asks")
    assert (chosen["color"], chosen["description"]) == ("teal", "Support asks")


def test_list_returns_the_registry_and_each_experiments_tags(auth_client, experiment):
    a = create(auth_client, "candidate")
    b = create(auth_client, "golden")
    empty = listed(auth_client)
    assert [t["id"] for t in empty["results"]] == [a["id"], b["id"]]
    assert empty["experiments"] == {}

    response = auth_client.put(
        experiment_tags_url(experiment.id), {"tag_ids": [b["id"], a["id"]]}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["result"] == {
        "experiment_id": str(experiment.id),
        "tag_ids": [b["id"], a["id"]],
    }
    result = listed(auth_client)
    assert sorted(result["experiments"][str(experiment.id)]) == sorted([a["id"], b["id"]])
    assert [t["used_on"]["experiments"] for t in result["results"]] == [1, 1]

    plain = auth_client.get(TAGS)
    assert plain.status_code == 200
    assert "experiments" not in plain.data["result"]


@pytest.mark.parametrize("name", ["Candidate", "  CANDIDATE  ", "candidate"])
def test_a_name_used_in_the_workspace_is_refused_with_the_page_sentence(auth_client, name):
    create(auth_client, "candidate")
    response = auth_client.post(TAGS, {"name": name}, format="json")
    assert response.status_code == 400
    assert response.data["result"] == "A tag named “candidate” already exists."
    assert WorkspaceTag.objects.count() == 1


@pytest.mark.parametrize(
    "body",
    [{}, {"name": ""}, {"name": "   "}, {"name": 7}, {"name": "x" * 65},
     {"name": "ok", "color": "orange"}, {"name": "ok", "workspace_id": "x"}],
)
def test_an_unusable_create_is_refused_and_writes_nothing(auth_client, body):
    response = auth_client.post(TAGS, body, format="json")
    assert response.status_code == 400, response.data
    assert response.data["status"] is False
    assert WorkspaceTag.objects.count() == 0


def test_patch_renames_recolours_and_redescribes(auth_client):
    tag = create(auth_client, "candidate")
    create(auth_client, "golden")
    renamed = auth_client.patch(tag_url(tag["id"]), {"name": "  Release  candidate"}, format="json")
    assert renamed.status_code == 200, renamed.data
    assert renamed.data["result"]["name"] == "Release candidate"
    recoloured = auth_client.patch(tag_url(tag["id"]), {"color": "red"}, format="json")
    assert recoloured.data["result"]["color"] == "red"
    described = auth_client.patch(tag_url(tag["id"]), {"description": "Ready to ship"}, format="json")
    assert described.data["result"]["description"] == "Ready to ship"
    stored = WorkspaceTag.objects.get(pk=tag["id"])
    assert (stored.name, stored.name_key, stored.color, stored.description) == (
        "Release candidate", "release candidate", "red", "Ready to ship",
    )
    clash = auth_client.patch(tag_url(tag["id"]), {"name": "GOLDEN"}, format="json")
    assert clash.status_code == 400
    assert clash.data["result"] == "A tag named “golden” already exists."
    assert WorkspaceTag.objects.get(pk=tag["id"]).name == "Release candidate"
    same_key = auth_client.patch(tag_url(tag["id"]), {"name": "release CANDIDATE"}, format="json")
    assert same_key.status_code == 200
    assert same_key.data["result"]["name"] == "release CANDIDATE"
    nothing = auth_client.patch(tag_url(tag["id"]), {}, format="json")
    assert nothing.status_code == 400


def test_delete_removes_the_tag_from_every_experiment(auth_client, organization, workspace, user):
    tag = create(auth_client, "candidate")
    keep = create(auth_client, "golden")
    one = make_experiment(organization, workspace, user, "One")
    two = make_experiment(organization, workspace, user, "Two")
    for experiment in (one, two):
        auth_client.put(
            experiment_tags_url(experiment.id), {"tag_ids": [tag["id"], keep["id"]]}, format="json"
        )
    before = {t["id"]: t["used_on"]["experiments"] for t in listed(auth_client)["results"]}
    assert before == {tag["id"]: 2, keep["id"]: 2}
    response = auth_client.delete(tag_url(tag["id"]))
    assert response.status_code == 200, response.data
    assert response.data["result"] == {"id": tag["id"]}
    assert not WorkspaceTag.objects.filter(pk=tag["id"]).exists()
    assert stored_links(one) == stored_links(two) == [keep["id"]]
    again = auth_client.delete(tag_url(tag["id"]))
    assert again.status_code == 404
    assert again.data["result"] == "Tag not found"


# --- An experiment's tags -------------------------------------------------


def test_put_replaces_the_experiments_tags_and_is_idempotent(auth_client, experiment):
    a, b, c = (create(auth_client, n)["id"] for n in ("a", "b", "c"))
    for tag_ids, expected in (
        ([a, b], [a, b]),
        ([b, c, c], [b, c]),
        ([b, c], [b, c]),
        ([], []),
    ):
        response = auth_client.put(
            experiment_tags_url(experiment.id), {"tag_ids": tag_ids}, format="json"
        )
        assert response.status_code == 200, response.data
        assert response.data["result"]["tag_ids"] == expected
        assert stored_links(experiment) == sorted(expected)


@pytest.mark.parametrize(
    "body", [{}, {"tag_ids": None}, {"tag_ids": "a"}, {"tag_ids": ["not-a-uuid"]},
             {"tag_ids": [], "extra": 1}],
)
def test_an_unusable_put_is_refused(auth_client, experiment, body):
    tag = create(auth_client, "keep")
    auth_client.put(experiment_tags_url(experiment.id), {"tag_ids": [tag["id"]]}, format="json")
    response = auth_client.put(experiment_tags_url(experiment.id), body, format="json")
    assert response.status_code == 400, response.data
    assert stored_links(experiment) == [tag["id"]]


def test_put_locks_the_wanted_tags_while_it_writes_the_links(auth_client, experiment):
    # A tag deleted while this PUT runs must wait for it, so the PUT never
    # writes a link to a tag that is gone (a foreign key error at commit).
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    tag = create(auth_client, "candidate")
    with CaptureQueriesContext(connection) as queries:
        response = auth_client.put(
            experiment_tags_url(experiment.id), {"tag_ids": [tag["id"]]}, format="json"
        )
    assert response.status_code == 200, response.data
    locked = [
        query["sql"]
        for query in queries.captured_queries
        if "FOR UPDATE" in query["sql"]
        and 'FROM "model_hub_workspace_tag"' in query["sql"]
    ]
    assert locked, "the wanted tags are read FOR UPDATE before the links are written"


def test_put_refuses_a_tag_that_is_not_this_workspaces(auth_client, other_org_client, experiment):
    mine = create(auth_client, "mine")
    theirs = create(other_org_client, "theirs")
    for tag_ids in ([mine["id"], theirs["id"]], [str(uuid.uuid4())]):
        response = auth_client.put(
            experiment_tags_url(experiment.id), {"tag_ids": tag_ids}, format="json"
        )
        assert response.status_code == 400
        assert response.data["result"] == "A tag in tag_ids is not one of this workspace's tags."
        assert stored_links(experiment) == []


@pytest.mark.parametrize("scope", ["deleted", "deleted_dataset", "missing"])
def test_put_on_an_unavailable_experiment_is_not_found(auth_client, experiment, scope):
    tag = create(auth_client, "candidate")
    target = experiment.id
    if scope == "deleted":
        ExperimentsTable.no_workspace_objects.filter(pk=experiment.id).update(deleted=True)
    elif scope == "deleted_dataset":
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(deleted=True)
    else:
        target = uuid.uuid4()
    response = auth_client.put(experiment_tags_url(target), {"tag_ids": [tag["id"]]}, format="json")
    assert response.status_code == 404, response.data
    assert response.data["result"] == "Experiment not found"
    assert ExperimentTag.objects.count() == 0


# --- Two tenants ------------------------------------------------------------


def test_another_organization_sees_and_changes_nothing(auth_client, other_org_client, experiment):
    tag = create(auth_client, "candidate")
    auth_client.put(experiment_tags_url(experiment.id), {"tag_ids": [tag["id"]]}, format="json")

    theirs = listed(other_org_client)
    assert theirs == {"results": [], "experiments": {}}
    assert other_org_client.patch(tag_url(tag["id"]), {"name": "taken"}, format="json").status_code == 404
    assert other_org_client.delete(tag_url(tag["id"])).status_code == 404
    response = other_org_client.put(experiment_tags_url(experiment.id), {"tag_ids": []}, format="json")
    assert response.status_code == 404
    assert response.data["result"] == "Experiment not found"
    # Their own tag of the same name is theirs alone.
    same = create(other_org_client, "candidate")
    assert same["id"] != tag["id"]

    stored = WorkspaceTag.objects.get(pk=tag["id"])
    assert stored.name == "candidate"
    assert stored_links(experiment) == [tag["id"]]
    mine = listed(auth_client)
    assert [t["id"] for t in mine["results"]] == [tag["id"]]
    assert mine["experiments"] == {str(experiment.id): [tag["id"]]}


def test_another_workspace_of_the_organization_sees_nothing(auth_client, organization, user, experiment):
    from conftest import WorkspaceAwareAPIClient

    tag = create(auth_client, "candidate")
    auth_client.put(experiment_tags_url(experiment.id), {"tag_ids": [tag["id"]]}, format="json")
    other = Workspace.objects.create(
        name="Second workspace", organization=organization, is_default=False,
        is_active=True, created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user, workspace=other,
        defaults={"role": "Workspace Owner", "level": Level.OWNER, "is_active": True,
                  "organization_membership": OrganizationMembership.no_workspace_objects.get(
                      user=user, organization=organization)},
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(other)
    try:
        assert listed(client) == {"results": [], "experiments": {}}
        assert client.patch(tag_url(tag["id"]), {"name": "x"}, format="json").status_code == 404
        assert client.put(
            experiment_tags_url(experiment.id), {"tag_ids": []}, format="json"
        ).status_code == 404
    finally:
        client.stop_workspace_injection()
    assert stored_links(experiment) == [tag["id"]]


# --- Access -------------------------------------------------------------------


def test_a_read_only_member_reads_but_cannot_write(auth_client, user, experiment):
    tag = create(auth_client, "candidate")
    with patch.object(type(user), "can_write_to_workspace", return_value=False):
        assert auth_client.get(TAGS).status_code == 200
        for response in (
            auth_client.post(TAGS, {"name": "new"}, format="json"),
            auth_client.patch(tag_url(tag["id"]), {"name": "new"}, format="json"),
            auth_client.delete(tag_url(tag["id"])),
            auth_client.put(experiment_tags_url(experiment.id), {"tag_ids": [tag["id"]]}, format="json"),
        ):
            assert response.status_code == 403, response.data
            assert response.data["result"] == (
                "You do not have permission to change tags in this workspace."
            )
    assert list(WorkspaceTag.objects.values_list("name", flat=True)) == ["candidate"]
    assert stored_links(experiment) == []


def test_unauthenticated_calls_are_refused(api_client, experiment):
    assert api_client.get(TAGS).status_code in {401, 403}
    assert api_client.post(TAGS, {"name": "x"}, format="json").status_code in {401, 403}
    assert api_client.put(
        experiment_tags_url(experiment.id), {"tag_ids": []}, format="json"
    ).status_code in {401, 403}
    assert WorkspaceTag.objects.count() == 0


# --- Routes and the migration ------------------------------------------------


@pytest.mark.parametrize(
    "path,view",
    [
        (TAGS, "WorkspaceTagsView"),
        (tag_url(uuid.uuid4()), "WorkspaceTagDetailView"),
        (experiment_tags_url(uuid.uuid4()), "ExperimentTagsView"),
    ],
)
def test_the_three_paths_are_served(path, view):
    assert resolve(path).func.view_class.__name__ == view


def test_every_app_keeps_one_migration_leaf_and_0144_only_adds_the_two_tables():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    leaves = {}
    for app, name in loader.graph.leaf_nodes():
        leaves.setdefault(app, []).append(name)
    assert {app: names for app, names in leaves.items() if len(names) > 1} == {}
    assert leaves["model_hub"] == ["0144_workspace_tags"]
    migration = import_module("model_hub.migrations.0144_workspace_tags").Migration
    assert ("model_hub", "0143_row_activity") in migration.dependencies
    assert all(isinstance(op, migrations.CreateModel) for op in migration.operations)
    assert {op.name for op in migration.operations} == {"WorkspaceTag", "ExperimentTag"}
