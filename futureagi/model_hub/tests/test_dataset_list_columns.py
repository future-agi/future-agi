"""Dataset list metadata and update ordering use the authorized stored rows."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context

LIST = "/model-hub/develops/get-datasets/"


@pytest.fixture(autouse=True)
def columns_enabled(settings):
    settings.DSX_DATASET_LIST_COLUMNS_ENABLED = True


@pytest.fixture
def actors(db):
    a, b = _tenant("columns-a"), _tenant("columns-b")
    clients = [_client(a), _client(b)]
    yield a, b, clients
    for client in clients:
        client.stop_workspace_injection()
    clear_workspace_context()


def listed(client, **params):
    response = client.get(LIST, {"page_size": 100, **params})
    assert response.status_code == 200, response.content
    return response.data["result"]


@pytest.mark.django_db
def test_list_metadata_matches_each_tenants_stored_owner_source_and_update(actors):
    a, b, clients = actors
    for actor, client, source in zip(
        (a, b),
        clients,
        (DatasetSourceChoices.BUILD.value, DatasetSourceChoices.OBSERVE.value),
        strict=True,
    ):
        dataset = actor["dataset"]
        Dataset.no_workspace_objects.filter(pk=dataset.pk).update(source=source)
        dataset.refresh_from_db()
        rows = listed(client)["datasets"]
        assert [row["id"] for row in rows] == [str(dataset.id)]
        assert rows[0]["created_by"] == str(actor["user"].id)
        assert rows[0]["created_by_name"] == actor["user"].name
        assert rows[0]["source"] == source
        assert rows[0]["updated_at"] == dataset.updated_at.isoformat()


@pytest.mark.django_db
def test_missing_owner_remains_null_without_using_the_requesting_user(actors):
    a, _b, (client, _) = actors
    Dataset.no_workspace_objects.filter(pk=a["dataset"].pk).update(user=None)
    row = listed(client)["datasets"][0]
    assert row["created_by"] is None
    assert row["created_by_name"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("direction", ["ascending", "descending"])
def test_updated_sort_orders_before_pagination_and_survives_repeated_reads(
    actors, direction
):
    a, _b, (client, _) = actors
    first = a["dataset"]
    second = Dataset.no_workspace_objects.create(
        name="columns-newer-creation",
        organization=a["org"],
        workspace=a["workspace"],
        user=a["user"],
    )
    now = timezone.now()
    # Creation order is deliberately opposite to update order.
    Dataset.no_workspace_objects.filter(pk=first.pk).update(updated_at=now)
    Dataset.no_workspace_objects.filter(pk=second.pk).update(
        updated_at=now - timedelta(days=2)
    )
    expected = [second, first] if direction == "ascending" else [first, second]
    params = {
        "sort": json.dumps([{"column_id": "updated_at", "type": direction}]),
        "page_size": 1,
    }
    for _ in range(2):
        for page, dataset in enumerate(expected):
            result = listed(client, page=page, **params)
            assert [row["id"] for row in result["datasets"]] == [str(dataset.id)]
            assert result["total_count"] == result["total_pages"] == 2


@pytest.mark.django_db
def test_metadata_list_never_includes_foreign_or_sibling_workspace_rows(actors):
    a, b, (client, other) = actors
    sibling = Workspace.objects.create(
        name="Columns sibling",
        organization=a["org"],
        created_by=a["user"],
        is_default=False,
    )
    hidden = Dataset.no_workspace_objects.create(
        name="columns-hidden-sibling",
        organization=a["org"],
        workspace=sibling,
        user=a["user"],
    )
    for own, foreign, current in ((a, b, client), (b, a, other)):
        assert listed(current, search_text=foreign["dataset"].name)["datasets"] == []
        assert listed(current, search_text=hidden.name)["datasets"] == []
        rows = listed(
            current,
            sort=json.dumps([{"column_id": "updated_at", "type": "descending"}]),
        )["datasets"]
        assert [row["id"] for row in rows] == [str(own["dataset"].id)]
        assert rows[0]["created_by"] == str(own["user"].id)
