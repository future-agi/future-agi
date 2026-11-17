"""Failed evaluation filters must agree across table and row navigation reads."""

import json
import uuid

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import tenants  # noqa: F401


def failed_filter(column_ids=None):
    return {
        "column_id": "system:row_failed"
        if column_ids is None
        else "system:row_failed_in",
        "filter_config": {
            "filter_type": "boolean" if column_ids is None else "categorical",
            "filter_op": "equals" if column_ids is None else "in",
            "filter_value": True if column_ids is None else column_ids,
        },
    }


@pytest.fixture
def failed_rows(tenants):  # noqa: F811
    a, b, clients = tenants
    dataset = a["dataset"]
    rows = a["rows"] + [Row.no_workspace_objects.create(dataset=dataset, order=2)]
    evals = [
        Column.no_workspace_objects.create(
            dataset=dataset,
            name=name,
            source=SourceChoices.EVALUATION.value,
            data_type=DataTypeChoices.BOOLEAN.value,
        )
        for name in ("Quality", "Safety")
    ]
    for column, values in zip(
        evals, [("Failed", "Passed", "false"), ("true", "false", "true")], strict=True
    ):
        for row, value in zip(rows, values, strict=True):
            Cell.no_workspace_objects.create(
                dataset=dataset, row=row, column=column, value=value, status="pass"
            )
    return a, b, clients, rows, evals


def read_table(client, dataset_id, filters):
    return client.get(
        f"/model-hub/develops/{dataset_id}/get-dataset-table/",
        {"filters": json.dumps(filters), "page_size": 10, "current_page_index": 0},
    )


def read_row(client, dataset_id, row_id, filters, sort=None):
    return client.post(
        f"/model-hub/develops/{dataset_id}/get-row-data/",
        {"row_id": str(row_id), "filters": filters, "sort": sort or []},
        format="json",
    )


@pytest.mark.django_db
class TestFailedFilters:
    def test_table_and_navigation_agree_on_selected_evals(self, failed_rows):
        a, _b, clients, rows, evals = failed_rows
        for selected, expected in [
            (None, rows),
            ([str(evals[0].id)], [rows[0], rows[2]]),
        ]:
            filters = [failed_filter(selected)]
            table = read_table(clients["a"], a["dataset"].id, filters)
            assert table.status_code == 200, table.content
            assert [r["row_id"] for r in table.json()["result"]["table"]] == [
                str(r.id) for r in expected
            ]
            detail = read_row(clients["a"], a["dataset"].id, expected[0].id, filters)
            assert detail.status_code == 200, detail.content
            assert detail.json()["result"]["current"]["row_id"] == str(expected[0].id)
            assert detail.json()["result"]["next"]["row_id"] == [
                str(r.id) for r in expected[1:]
            ]
        excluded = read_row(
            clients["a"],
            a["dataset"].id,
            rows[1].id,
            [failed_filter([str(evals[0].id)])],
        )
        assert excluded.status_code == 404

    def test_table_reports_unfiltered_distinct_failure_counts(self, failed_rows):
        a, _b, clients, _rows, evals = failed_rows
        response = read_table(
            clients["a"], a["dataset"].id, [failed_filter([str(evals[1].id)])]
        )
        assert response.status_code == 200, response.content
        counts = response.json()["result"]["metadata"]["counts"]
        assert counts["failed_rows"] == 3
        assert counts["failed_by_eval"] == [
            {"column_id": str(evals[0].id), "name": "Quality", "count": 2},
            {"column_id": str(evals[1].id), "name": "Safety", "count": 1},
        ]

    def test_empty_error_deleted_and_input_values_are_not_eval_failures(
        self, failed_rows
    ):
        a, _b, clients, rows, evals = failed_rows
        Cell.no_workspace_objects.filter(column__in=evals).update(value="Passed")
        Cell.no_workspace_objects.filter(column=evals[0], row=rows[0]).update(
            value="false", status="failed"
        )
        Cell.no_workspace_objects.filter(column=evals[0], row=rows[1]).update(
            value="false", deleted=True
        )
        Cell.no_workspace_objects.filter(column=evals[0], row=rows[2]).update(value="")
        Cell.no_workspace_objects.filter(column=a["column"]).update(value="Failed")
        response = read_table(clients["a"], a["dataset"].id, [failed_filter()])
        assert response.status_code == 200, response.content
        assert response.json()["result"]["table"] == []
        assert response.json()["result"]["metadata"]["counts"]["failed_rows"] == 0

    @pytest.mark.parametrize(
        "kind", ["foreign", "unknown", "input", "hidden", "deleted"]
    )
    def test_invalid_eval_ids_fail_closed_in_both_readers(self, failed_rows, kind):
        a, b, clients, rows, evals = failed_rows
        hidden = Column.no_workspace_objects.create(
            dataset=a["dataset"],
            name="Hidden",
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            data_type=DataTypeChoices.BOOLEAN.value,
        )
        Column.no_workspace_objects.filter(id=evals[1].id).update(deleted=True)
        column_id = {
            "foreign": b["column"].id,
            "unknown": uuid.uuid4(),
            "input": a["column"].id,
            "hidden": hidden.id,
            "deleted": evals[1].id,
        }[kind]
        filters = [failed_filter([str(column_id)])]
        assert read_table(clients["a"], a["dataset"].id, filters).status_code == 400
        assert (
            read_row(clients["a"], a["dataset"].id, rows[0].id, filters).status_code
            == 400
        )

    @pytest.mark.parametrize(
        "column_id", ["system:unknown", "system:splits", "not-a-column"]
    )
    def test_unknown_physical_and_system_columns_are_rejected(
        self, failed_rows, column_id
    ):
        a, _b, clients, rows, _evals = failed_rows
        filters = [failed_filter() | {"column_id": column_id}]
        assert read_table(clients["a"], a["dataset"].id, filters).status_code == 400
        assert (
            read_row(clients["a"], a["dataset"].id, rows[0].id, filters).status_code
            == 400
        )

    def test_system_filter_is_not_a_sort_column(self, failed_rows):
        a, _b, clients, rows, _evals = failed_rows
        response = read_row(
            clients["a"],
            a["dataset"].id,
            rows[0].id,
            [],
            [{"column_id": "system:row_failed", "type": "ascending"}],
        )
        assert response.status_code == 400

    def test_foreign_dataset_and_row_are_refused(self, failed_rows):
        a, b, clients, rows, _evals = failed_rows
        filters = [failed_filter()]
        assert read_table(clients["b"], a["dataset"].id, filters).status_code in (
            400,
            404,
        )
        assert (
            read_row(clients["b"], a["dataset"].id, rows[0].id, filters).status_code
            == 404
        )
        assert (
            read_row(
                clients["a"], a["dataset"].id, b["rows"][0].id, filters
            ).status_code
            == 404
        )
