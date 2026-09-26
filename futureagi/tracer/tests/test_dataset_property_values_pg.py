"""The dataset dashboard's metadata pickers answer from PostgreSQL.

``filter_values`` with ``source=datasets`` suggests dataset names and column
names / sources for the dataset widget filters. Column names used to be read
as ``DISTINCT dictGet('column_dict', 'name', c.column_id)`` over the
``model_hub_cell`` ClickHouse mirror. PeerDB creates it ``ORDER BY id``, so
every call read the whole table (dev, 2026-09-25: 14.4M rows / 675 MiB,
0.64-1.4 s per call), and the mirror is incomplete: for dev workspace
f7f5533e it answered 384 names while PostgreSQL holds 561 live ones, 518 live
columns (186 names) having no mirrored column or cell at all; its dataset
name picker saw 51 of the workspace's 109 live datasets. ``lagging_mirror``
stands in for that mirror: it is enabled and has not received any of these
rows.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

URL = "/tracer/dashboard/filter_values/"


@pytest.fixture
def lagging_mirror():
    empty = MagicMock()
    empty.return_value.execute_ch_query.return_value = MagicMock(data=[])
    with (
        patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
        patch("tracer.views.dashboard.AnalyticsQueryService", empty),
    ):
        yield


@pytest.fixture
def dataset(organization, workspace):
    from model_hub.models.develop_dataset import Dataset

    return Dataset.objects.create(
        name="qa-dataset", organization=organization, workspace=workspace
    )


def _column(dataset, name, *, source="OTHERS"):
    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Column

    return Column.objects.create(
        name=name,
        data_type="text",
        source=source,
        status=StatusType.COMPLETED.value,
        dataset=dataset,
    )


def _cell(column, value="v"):
    from model_hub.models.develop_dataset import Cell, Row

    row = Row.objects.create(dataset=column.dataset, order=Row.objects.count())
    return Cell.objects.create(
        dataset=column.dataset, column=column, row=row, value=value
    )


def _suggestions(client, metric_name, **params):
    response = client.get(
        URL,
        {
            "source": "datasets",
            "metric_name": metric_name,
            "metric_type": "system_metric",
            **params,
        },
    )
    assert response.status_code == 200, response.content
    return [option["value"] for option in response.json()["result"]["values"]]


def _other_tenants(organization, user):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from model_hub.models.develop_dataset import Dataset

    sibling = Workspace.objects.create(
        name=f"sibling-{uuid.uuid4().hex[:6]}",
        organization=organization,
        created_by=user,
    )
    other_org = Organization.objects.create(name="Other Organization")
    foreign = Workspace.objects.create(
        name="foreign", organization=other_org, created_by=user
    )
    return [
        Dataset.objects.create(
            name="sibling-dataset", organization=organization, workspace=sibling
        ),
        Dataset.objects.create(
            name="foreign-dataset", organization=other_org, workspace=foreign
        ),
    ]


@pytest.mark.django_db
def test_a_fresh_column_is_suggested_immediately(auth_client, dataset, lagging_mirror):
    column = _column(dataset, "qa_answer", source="evaluation")
    _cell(column)

    assert _suggestions(auth_client, "column_name") == ["qa_answer"]
    # eval_template filters the same column-name expression.
    assert _suggestions(auth_client, "eval_template") == ["qa_answer"]
    assert _suggestions(auth_client, "column_source") == ["evaluation"]


@pytest.mark.django_db
def test_a_fresh_dataset_is_suggested_immediately(auth_client, dataset, lagging_mirror):
    assert _suggestions(auth_client, "dataset") == ["qa-dataset"]


@pytest.mark.django_db
def test_only_live_columns_with_live_cells_are_suggested(
    auth_client, dataset, lagging_mirror
):
    from model_hub.models.develop_dataset import Dataset

    _cell(_column(dataset, "kept"))
    # The widgets' exact reads join only live columns.
    deleted_column = _column(dataset, "deleted-column")
    _cell(deleted_column)
    deleted_column.delete()
    # Deleting a row soft-deletes its cells; the old reader read cells, so a
    # column without any live cell was never suggested.
    _cell(_column(dataset, "row-deleted")).delete()
    _column(dataset, "no-cells")
    deleted_dataset = Dataset.objects.create(
        name="deleted-dataset",
        organization=dataset.organization,
        workspace=dataset.workspace,
    )
    _cell(_column(deleted_dataset, "in-deleted-dataset"))
    deleted_dataset.delete()

    assert _suggestions(auth_client, "column_name") == ["kept"]
    assert _suggestions(auth_client, "dataset") == ["qa-dataset"]


@pytest.mark.django_db
def test_other_workspaces_and_organizations_are_not_suggested(
    auth_client, dataset, organization, user, lagging_mirror
):
    from model_hub.models.develop_dataset import Dataset

    _cell(_column(dataset, "mine"))
    for other in _other_tenants(organization, user):
        _cell(_column(other, f"{other.name}-column", source="evaluation"))
    # The dashboard widgets match the workspace exactly, so a dataset without
    # a workspace is not this (default) workspace's, as before.
    unassigned = Dataset.objects.create(
        name="unassigned", organization=organization, workspace=dataset.workspace
    )
    Dataset.all_objects.filter(pk=unassigned.pk).update(workspace=None)
    _cell(_column(unassigned, "unassigned-column"))

    assert _suggestions(auth_client, "column_name") == ["mine"]
    assert _suggestions(auth_client, "column_source") == ["OTHERS"]
    assert _suggestions(auth_client, "dataset") == ["qa-dataset"]


@pytest.mark.django_db
def test_search_narrows_case_insensitively_in_byte_order(
    auth_client, dataset, lagging_mirror
):
    for name in ("Spanish", "english", "French", "English"):
        _cell(_column(dataset, name))

    assert _suggestions(auth_client, "column_name", search="ISH") == [
        "English",
        "Spanish",
        "english",
    ]
    assert _suggestions(auth_client, "column_name", search="ish", page_size=50) == [
        "English",
        "Spanish",
        "english",
    ]


@pytest.mark.django_db
def test_an_inventory_over_the_cap_is_refused_not_sampled(
    auth_client, dataset, lagging_mirror
):
    for name in ("a", "b", "c"):
        _cell(_column(dataset, name))

    with patch("tracer.views.dashboard._LEGACY_NATIVE_FILTER_VALUE_MAX", 2):
        response = auth_client.get(
            URL,
            {
                "source": "datasets",
                "metric_name": "column_name",
                "metric_type": "system_metric",
            },
        )

    assert response.status_code == 422, response.content
    assert response.json()["code"] == "filter_value_inventory_too_broad"


@pytest.mark.django_db
def test_an_answer_over_the_byte_cap_is_refused(auth_client, dataset, lagging_mirror):
    for name in ("alpha", "beta"):
        _cell(_column(dataset, name))

    with patch(
        "tracer.views.dashboard._FINITE_NATIVE_FILTER_VALUE_MAX_RESULT_BYTES", 8
    ):
        response = auth_client.get(
            URL,
            {
                "source": "datasets",
                "metric_name": "column_name",
                "metric_type": "system_metric",
            },
        )

    assert response.status_code == 503, response.content
    assert response.json()["code"] == "service_unavailable"
