from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection
from django.urls import reverse

from accounts.models.organization import Organization
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Dataset
from model_hub.tests.test_dataset_version_api import version_api_dataset  # noqa: F401
from model_hub.tests.test_experiment_run_creation import (
    _create,  # noqa: F401
    run_inputs,  # noqa: F401
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def version(version_api_dataset):  # noqa: F811
    return DatasetVersion.objects.create(dataset=version_api_dataset)


def _url(version):
    kwargs = {"dataset_id": version.dataset_id, "version_id": version.id}
    return reverse("dataset-version-detail", kwargs=kwargs)


def _patch(client, version, body, tags, moved=None):
    response = client.patch(_url(version), body, format="json")
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["tag_moved_from"] == (str(moved) if moved else None)
    assert result["version"]["tags"] == tags
    assert result["version"]["id"] == str(version.id)
    return result["version"]


def test_tag_set_move_reset_clear_and_description(auth_client, version):
    url = reverse("dataset-version-list", kwargs={"dataset_id": version.dataset_id})
    assert auth_client.get(url).json()["result"]["versions"][0]["tags"] == []
    body = {"tag": " Golden.V4 ", "note": "n" * 200}
    _patch(auth_client, version, body, ["golden.v4"])
    tag = version.tags.get()
    assert tag.note == "n" * 200
    assert tag.created_by_id == version.dataset.user_id
    _patch(auth_client, version, {"tag": "golden.v4"}, ["golden.v4"])
    assert version.tags.get().pk == tag.pk
    _patch(auth_client, version, {"tag": "alpha"}, ["alpha", "golden.v4"])
    assert auth_client.get(_url(version)).json()["result"]["version"]["tags"] == [
        "alpha",
        "golden.v4",
    ]
    assert auth_client.get(url).json()["result"]["versions"][0]["tags"] == [
        "alpha",
        "golden.v4",
    ]
    other = DatasetVersion.objects.create(dataset=version.dataset)
    _patch(auth_client, other, {"tag": "golden.v4"}, ["golden.v4"], version.id)
    tag.refresh_from_db()
    assert tag.version_id == other.id
    assert list(version.tags.values_list("name", flat=True)) == ["alpha"]
    _patch(auth_client, other, {"tag": "second"}, ["golden.v4", "second"])
    _patch(auth_client, other, {"tag": None}, [])
    assert other.tags.count() == 0
    result = _patch(auth_client, version, {"description": " New "}, ["alpha"])
    version.refresh_from_db()
    assert result["description"] == version.description == " New "


@pytest.mark.parametrize("tag", ["", "bad tag", "a/b", ".a", "a" * 31, "a\nb"])
def test_tag_format_refused(auth_client, version, tag):
    response = auth_client.patch(_url(version), {"tag": tag}, format="json")
    assert response.status_code == 400
    assert response.json()["details"] == {
        "tag": ["Use lowercase letters, numbers, dots, dashes or underscores (max 30)."]
    }


@pytest.mark.parametrize(
    "body,details",
    [
        ({"unknown": True}, {"unknown": ["Unknown field."]}),
        ({"tag": 1}, {"tag": ["Not a valid string."]}),
        (
            {"tag": "ok", "note": "n" * 201},
            {"note": ["Ensure this field has no more than 200 characters."]},
        ),
    ],
)
def test_invalid_patch_refused(auth_client, version, body, details):
    response = auth_client.patch(_url(version), body, format="json")
    assert response.status_code == 400
    assert response.json()["details"] == details
    assert version.tags.count() == 0


def test_tag_other_tenant_refused(auth_client, version):
    Dataset.no_workspace_objects.filter(id=version.dataset_id).update(
        organization=Organization.objects.create(name="Other tag organization"),
        workspace=None,
    )
    response = auth_client.patch(_url(version), {"tag": "prod"}, format="json")
    assert response.status_code == 404
    assert version.tags.count() == 0


def test_tag_workspace_write_required(auth_client, version, monkeypatch):
    monkeypatch.setattr(
        type(version.dataset.user), "can_write_to_workspace", lambda *_: False
    )
    response = auth_client.patch(_url(version), {"tag": "prod"}, format="json")
    assert response.status_code == 403
    assert version.tags.count() == 0


@pytest.mark.django_db(transaction=True)
def test_tag_update_and_run_creation_lock_order(auth_client, run_inputs):  # noqa: F811
    if connection.vendor != "postgresql":
        pytest.skip("Row locking requires PostgreSQL")
    _, version, items, user = run_inputs
    barrier, locks = Barrier(2), []

    def run_lock(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if "FOR UPDATE" in sql and f'FROM "{Dataset._meta.db_table}"' in sql:
            barrier.wait(timeout=10)
        return result

    def create_run():
        try:
            with connection.execute_wrapper(run_lock):
                return _create(version, items, user).dataset_version_id
        finally:
            connection.close()

    def tag_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql:
            locks.append(sql.rsplit("FOR UPDATE OF ", 1)[-1])
            if len(locks) == 1:
                barrier.wait(timeout=10)
        return execute(sql, params, many, context)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(create_run)
        with connection.execute_wrapper(tag_lock):
            _patch(auth_client, version, {"tag": "prod"}, ["prod"])
        assert future.result(timeout=30) == version.id
    assert locks == [f'"{m._meta.db_table}"' for m in (Dataset, DatasetVersion)]
