"""Dataset list metadata and update ordering use the authorized stored rows."""

import json
from datetime import timedelta

import pytest
from django.conf import UserSettingsHolder, settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.serializers.develop_dataset_contracts import DatasetListItemSerializer
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context

FLAG = "DATASET_LIST_COLUMNS_ENABLED"
LIST = "/model-hub/develops/get-datasets/"
METADATA = {"created_by", "created_by_name", "source", "updated_at"}


@pytest.fixture(autouse=True)
def columns_default(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    # Model the raw default without creating an explicit settings override.
    parsed_settings = settings._wrapped
    while isinstance(parsed_settings, UserSettingsHolder):
        parsed_settings = parsed_settings.default_settings
    monkeypatch.setattr(parsed_settings, FLAG, False)


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
@pytest.mark.parametrize("value", [None, "", " \t "])
def test_list_metadata_is_on_without_an_environment_value(actors, monkeypatch, value):
    a, _b, (client, _) = actors
    if value is not None:
        monkeypatch.setenv(FLAG, value)
    row = listed(client)["datasets"][0]
    assert row["created_by"] == str(a["user"].id)
    assert row["created_by_name"] == a["user"].name
    assert row["source"] == a["dataset"].source
    assert row["updated_at"] == a["dataset"].updated_at.isoformat()


@pytest.mark.django_db
@pytest.mark.parametrize("value", [None, "", " \t "])
def test_default_serializer_keeps_optional_metadata_and_validation(
    actors, monkeypatch, value
):
    _a, _b, (client, _) = actors
    if value is not None:
        monkeypatch.setenv(FLAG, value)
    fields = DatasetListItemSerializer().fields
    assert METADATA.issubset(fields)
    assert all(not fields[name].required for name in METADATA)
    row = listed(client)["datasets"][0]
    serializer = DatasetListItemSerializer(data=row)
    assert serializer.is_valid(), serializer.errors
    nullable = DatasetListItemSerializer(
        data={**row, "created_by": None, "created_by_name": None}
    )
    assert nullable.is_valid(), nullable.errors
    invalid = DatasetListItemSerializer(data={**row, "created_by": "not-a-uuid"})
    assert not invalid.is_valid()
    assert set(invalid.errors) == {"created_by"}


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


@pytest.mark.django_db
def test_list_query_count_stays_constant_from_five_to_five_hundred_datasets(actors):
    a, b, (client, _) = actors

    def add_datasets(start, stop):
        return Dataset.no_workspace_objects.bulk_create(
            [
                Dataset(
                    name=f"columns-scale-{index}",
                    organization=a["org"],
                    workspace=a["workspace"],
                    user=a["user"],
                    source=DatasetSourceChoices.BUILD.value,
                )
                for index in range(start, stop)
            ]
        )

    datasets = [a["dataset"], *add_datasets(1, 5)]
    # Warm request initialization before measuring the full authenticated route.
    listed(client)
    with CaptureQueriesContext(connection) as small_queries:
        small = listed(client)
    assert small["total_count"] == 5
    assert len(small["datasets"]) == 5
    assert all(METADATA.issubset(row) for row in small["datasets"])
    baseline_queries = len(small_queries)
    assert baseline_queries > 0

    datasets.extend(add_datasets(5, 500))
    expected_ids = {str(dataset.pk) for dataset in datasets}
    assert len(expected_ids) == 500
    seen = set()
    # The public endpoint accepts at most 100 rows per page.
    for page in range(5):
        with CaptureQueriesContext(connection) as large_queries:
            result = listed(client, page=page)
        assert len(large_queries) == baseline_queries
        assert result["total_count"] == 500
        assert result["total_pages"] == 5
        assert len(result["datasets"]) == 100
        page_ids = {row["id"] for row in result["datasets"]}
        assert not seen.intersection(page_ids)
        seen.update(page_ids)
        for row in result["datasets"]:
            assert row["created_by"] == str(a["user"].id)
            assert row["created_by_name"] == a["user"].name
            assert row["source"] == DatasetSourceChoices.BUILD.value
            assert "updated_at" in row
    assert seen == expected_ids
    assert str(b["dataset"].pk) not in seen
