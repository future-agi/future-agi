"""Folders persist independently of rows and never cross workspace boundaries."""

import pytest
from django.apps import apps

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Dataset
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context

ROOT = "/model-hub/dataset-folders/"
MOVE = "/model-hub/dataset-folders/move-datasets/"
LIST = "/model-hub/develops/get-datasets/"


def folders():
    return apps.get_model("model_hub", "DatasetFolder").objects


def create(client, path):
    response = client.post(ROOT, {"path": path}, format="json")
    assert response.status_code == 201, response.content
    return response.data["result"]


def move(client, datasets, folder_id):
    return client.post(
        MOVE,
        {"dataset_ids": [str(d.id) for d in datasets], "folder_id": folder_id},
        format="json",
    )


def listing(client, **params):
    response = client.get(LIST, {"page_size": 100, **params})
    assert response.status_code == 200, response.content
    return response.data["result"]


@pytest.fixture
def actors(db):
    a, b = _tenant("folder-a"), _tenant("folder-b")
    clients = [_client(a), _client(b)]
    yield a, b, clients
    for client in clients:
        client.stop_workspace_injection()
    clear_workspace_context()


@pytest.mark.django_db
def test_create_empty_nested_folder_and_duplicate_conflict(actors):
    a, _b, (client, _) = actors
    child = create(client, "support/billing")
    assert (
        folders().filter(organization=a["org"], workspace=a["workspace"]).count() == 2
    )
    response = client.get(ROOT)
    assert response.status_code == 200
    assert [
        (f["path"], f["count"], f["total"]) for f in response.data["result"]["folders"]
    ] == [("support", 0, 0), ("support/billing", 0, 0)]
    assert str(folders().get(pk=child["id"]).workspace_id) == str(a["workspace"].id)
    duplicate = client.post(ROOT, {"path": "support/billing"}, format="json")
    assert duplicate.status_code == 400
    assert folders().count() == 2


@pytest.mark.django_db
def test_move_filter_before_pagination_and_remove_preserves_datasets(actors):
    a, _b, (client, _) = actors
    child = create(client, "support/billing")
    create(client, "support-other")
    target = a["dataset"]
    extra = Dataset.no_workspace_objects.create(
        name="other dataset", organization=a["org"], workspace=a["workspace"]
    )
    assert move(client, [target], child["id"]).status_code == 200
    target.refresh_from_db()
    assert str(target.folder_id) == child["id"]
    result = listing(client, folder_path="support", page_size=1)
    assert result["total_count"] == 1
    assert result["datasets"][0]["folder"] == "support/billing"
    assert result["datasets"][0]["folder_id"] == child["id"]
    assert listing(client, folder_path="support", page=1, page_size=1)["datasets"] == []
    assert listing(client, folder_path="support-other")["datasets"] == []
    assert [r["id"] for r in listing(client, folder_path="")["datasets"]] == [
        str(extra.id)
    ]
    assert (
        listing(client, folder_path="support", search_text="absent")["datasets"] == []
    )
    rows = client.get(ROOT).data["result"]
    assert rows["all_count"] == 2 and rows["no_folder_count"] == 1
    assert [(f["path"], f["total"]) for f in rows["folders"]] == [
        ("support", 1),
        ("support-other", 0),
        ("support/billing", 1),
    ]
    parent = folders().get(path="support")
    assert client.delete(f"{ROOT}{parent.id}/").status_code == 200
    target.refresh_from_db()
    assert target.folder_id is None and target.deleted is False
    assert listing(client)["total_count"] == 2
    assert list(folders().values_list("path", flat=True)) == ["support-other"]


@pytest.mark.django_db
def test_rename_and_move_subtree_keep_stable_folder_ids(actors):
    a, _b, (client, _) = actors
    child = create(client, "support/billing")
    parent = folders().get(path="support")
    create(client, "archive")
    assert move(client, [a["dataset"]], child["id"]).status_code == 200
    response = client.patch(
        f"{ROOT}{parent.id}/", {"path": "archive/help"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.data["result"]["count"] == 0
    assert response.data["result"]["total"] == 1
    detail = client.get(f"{ROOT}{child['id']}/")
    assert detail.status_code == 200
    assert detail.data["result"]["count"] == detail.data["result"]["total"] == 1
    assert folders().get(pk=child["id"]).path == "archive/help/billing"
    a["dataset"].refresh_from_db()
    assert str(a["dataset"].folder_id) == child["id"]
    assert listing(client, folder_path="support")["datasets"] == []
    assert listing(client, folder_path="archive")["total_count"] == 1
    # Cycles and collisions must leave the entire tree untouched.
    before = list(folders().order_by("path").values_list("id", "path"))
    for path in ("archive/help/billing/nested", "archive", "archive/help"):
        response = client.patch(f"{ROOT}{parent.id}/", {"path": path}, format="json")
        assert response.status_code == (200 if path == "archive/help" else 400)
        assert list(folders().order_by("path").values_list("id", "path")) == before


@pytest.mark.django_db
def test_cross_org_and_workspace_ids_are_refused_atomically(actors):
    a, b, (client, other) = actors
    fa, fb = create(client, "private-a"), create(other, "private-b")
    assert move(client, [a["dataset"]], fa["id"]).status_code == 200
    assert move(other, [b["dataset"]], fb["id"]).status_code == 200
    for method in (other.get, other.delete):
        assert method(f"{ROOT}{fa['id']}/").status_code == 404
    assert (
        other.patch(f"{ROOT}{fa['id']}/", {"path": "stolen"}, format="json").status_code
        == 404
    )
    assert move(other, [b["dataset"]], fa["id"]).status_code == 404
    assert move(client, [a["dataset"], b["dataset"]], None).status_code == 404
    assert [f["path"] for f in other.get(ROOT).data["result"]["folders"]] == [
        "private-b"
    ]
    clear_workspace_context()
    sibling = Workspace.objects.create(
        name="Sibling folder scope",
        organization=a["org"],
        created_by=a["user"],
        is_default=False,
        is_active=True,
    )
    alien = folders().create(organization=a["org"], workspace=sibling, path="sibling")
    assert client.get(f"{ROOT}{alien.id}/").status_code == 404
    assert move(client, [a["dataset"]], str(alien.id)).status_code == 404
    for actor, folder in ((a, fa), (b, fb)):
        actor["dataset"].refresh_from_db()
        assert str(actor["dataset"].folder_id) == folder["id"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path", ["", "/root", "a//b", "a/../b", "a/./b", " a", "a/ b", "a\\b", "x" * 81]
)
def test_invalid_path_has_no_partial_ancestors(actors, path):
    _a, _b, (client, _) = actors
    response = client.post(ROOT, {"path": path}, format="json")
    assert response.status_code == 400, response.content
    assert not folders().exists()


@pytest.mark.django_db
def test_move_rejects_deleted_dataset_and_unknown_fields(actors):
    a, _b, (client, _) = actors
    folder = create(client, "safe")
    Dataset.no_workspace_objects.filter(pk=a["dataset"].id).update(deleted=True)
    assert move(client, [a["dataset"]], folder["id"]).status_code == 404
    response = client.post(
        ROOT, {"path": "unsafe", "organization": "foreign"}, format="json"
    )
    assert response.status_code == 400
    assert not folders().filter(path="unsafe").exists()
