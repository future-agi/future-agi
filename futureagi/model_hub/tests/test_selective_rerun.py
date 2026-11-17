"""Selective reruns preserve non-selected outputs on the real dataset writers."""

import json
from unittest.mock import patch

import pytest
from django.test import TestCase

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context


@pytest.fixture
def datasets(db):
    a, b = _tenant("rerun-a"), _tenant("rerun-b")
    for tenant in (a, b):
        for i, cell in enumerate(tenant["cells"]):
            Cell.objects.filter(id=cell.id).update(
                value=json.dumps({"answer": f"initial-{i}"})
            )
        tenant["output"] = Column.objects.create(
            dataset=tenant["dataset"],
            name="answer",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXTRACTED_JSON.value,
            status=StatusType.COMPLETED.value,
            metadata={
                "column_id": str(tenant["column"].id),
                "json_key": "answer",
                "concurrency": 1,
            },
        )
        Cell.objects.bulk_create(
            [
                Cell(
                    dataset=tenant["dataset"],
                    column=tenant["output"],
                    row=row,
                    value=f"original-{i}",
                    status=CellStatus.PASS.value,
                )
                for i, row in enumerate(tenant["rows"])
            ]
        )
    clients = {"a": _client(a), "b": _client(b)}
    yield a, b, clients
    for client in clients.values():
        client.stop_workspace_injection()
    clear_workspace_context()


def state(tenant):
    return list(
        Cell.objects.filter(column=tenant["output"])
        .order_by("row__order")
        .values("id", "row_id", "value", "value_infos", "status", "updated_at")
    )


def rerun(client, tenant, scope):
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(
            f"/model-hub/columns/{tenant['output'].id}/rerun-operation/",
            {"operation_type": "extract_json", "scope": scope},
            format="json",
        )


@pytest.mark.django_db(transaction=True)
class TestSelectiveRerun:
    def test_failed_dispatch_and_clear_are_selected_only(self, datasets):
        a, _, clients = datasets
        Cell.objects.filter(column=a["output"], row=a["rows"][0]).update(
            status=CellStatus.ERROR.value
        )
        before = state(a)
        with patch(
            "model_hub.views.dynamic_columns.extract_json_async.delay"
        ) as dispatch:
            response = rerun(clients["a"], a, "failed")
        assert response.status_code == 200, response.content
        assert dispatch.call_args.kwargs["row_ids"] == [str(a["rows"][0].id)]
        assert state(a)[1] == before[1]

    def test_changed_inputs_are_persisted_and_unrelated_cells_are_not_rewritten(
        self, datasets
    ):
        from model_hub.views.dynamic_columns import extract_json_async

        a, _, clients = datasets
        with patch.object(extract_json_async, "delay", side_effect=extract_json_async):
            response = rerun(clients["a"], a, "all")
            assert response.status_code == 200, response.content
            before = state(a)
            Cell.objects.filter(id=a["cells"][0].id).update(
                value='{"answer":"changed"}'
            )
            response = rerun(clients["a"], a, "changed")
            assert response.status_code == 200, response.content
        after = state(a)
        assert after[0]["value"] == "changed"
        assert after[1] == before[1]
        with patch.object(extract_json_async, "delay") as dispatch:
            response = rerun(clients["a"], a, "changed")
        assert response.status_code == 200, response.content
        dispatch.assert_not_called()
        assert state(a) == after

    @pytest.mark.parametrize("scope", ["all", "changed", "failed"])
    def test_foreign_column_is_refused_without_writes_or_dispatch(
        self, datasets, scope
    ):
        _, b, clients = datasets
        before = state(b)
        with patch(
            "model_hub.views.dynamic_columns.extract_json_async.delay"
        ) as dispatch:
            response = rerun(clients["a"], b, scope)
        assert response.status_code == 404, response.content
        dispatch.assert_not_called()
        assert state(b) == before

    def test_unknown_scope_is_refused_without_writes(self, datasets):
        a, _, clients = datasets
        before = state(a)
        response = rerun(clients["a"], a, "picked")
        assert response.status_code == 400, response.content
        assert state(a) == before

    def test_empty_failed_set_does_not_dispatch_or_clear(self, datasets):
        a, _, clients = datasets
        before = state(a)
        with patch(
            "model_hub.views.dynamic_columns.extract_json_async.delay"
        ) as dispatch:
            response = rerun(clients["a"], a, "failed")
        assert response.status_code == 200, response.content
        dispatch.assert_not_called()
        assert state(a) == before

    def test_failed_rerun_removes_the_selected_error_details(self, datasets):
        from model_hub.views.dynamic_columns import extract_json_async

        a, _, clients = datasets
        Cell.objects.filter(column=a["output"], row=a["rows"][0]).update(
            status=CellStatus.ERROR.value,
            value_infos=json.dumps({"reason": "old error"}),
        )
        before = state(a)
        with patch.object(extract_json_async, "delay", side_effect=extract_json_async):
            response = rerun(clients["a"], a, "failed")
        assert response.status_code == 200, response.content
        after = state(a)
        assert after[0]["value"] == "initial-0"
        assert after[0]["status"] == "pass"
        assert json.loads(after[0]["value_infos"]) == {}
        assert after[1] == before[1]

    def test_changed_rerun_creates_a_missing_output(self, datasets):
        from model_hub.views.dynamic_columns import extract_json_async

        a, _, clients = datasets
        Cell.objects.filter(column=a["output"], row=a["rows"][0]).delete()
        with patch.object(extract_json_async, "delay", side_effect=extract_json_async):
            response = rerun(clients["a"], a, "changed")
        assert response.status_code == 200, response.content
        assert [cell["value"] for cell in state(a)] == ["initial-0", "initial-1"]

    def test_removed_input_finishes_as_error_instead_of_staying_running(self, datasets):
        from model_hub.views.dynamic_columns import extract_json_async

        a, _, clients = datasets
        with patch.object(extract_json_async, "delay", side_effect=extract_json_async):
            assert rerun(clients["a"], a, "all").status_code == 200
            before = state(a)
            Cell.objects.filter(id=a["cells"][0].id).update(deleted=True)
            response = rerun(clients["a"], a, "changed")
        assert response.status_code == 200, response.content
        after = state(a)
        assert after[0]["status"] == "error"
        assert after[1] == before[1]
