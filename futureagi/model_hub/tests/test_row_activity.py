"""Row activity acceptance tests. Require migrated PostgreSQL; never mock writes."""

import uuid

import pytest
from django.db import connection, transaction
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell, Dataset, Row
from model_hub.models.row_activity import RowActivity
from model_hub.serializers.row_activity import RowActivityResponseSerializer
from model_hub.services.row_activity_writes import (
    file_import_activity,
    row_activity_actor,
    row_activity_write,
)
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context, workspace_context


@pytest.fixture
def tenants(db):
    a = _tenant("activity-a")
    b = _tenant("activity-b")
    clients = {"a": _client(a), "b": _client(b)}
    yield a, b, clients
    for client in clients.values():
        client.stop_workspace_injection()
    clear_workspace_context()


def _url(tenant, row=None):
    return (
        f"/model-hub/datasets/{tenant['dataset'].id}/rows/"
        f"{(row or tenant['rows'][0]).id}/activity/"
    )


def _events(row):
    return RowActivity.objects.filter(row=row).order_by("-at", "-id")


def _edit(client, tenant, value):
    return client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/update_cell_value/",
        {
            "row_id": str(tenant["rows"][0].id),
            "column_id": str(tenant["column"].id),
            "new_value": value,
        },
        format="json",
    )


def test_existing_edit_path_exposes_actor_cell_and_diff(tenants):
    a, _, clients = tenants
    previous = a["cells"][0].value
    assert _edit(clients["a"], a, "Changed").status_code == 200
    response = clients["a"].get(_url(a))
    assert response.status_code == 200, response.content
    body = response.json()
    serializer = RowActivityResponseSerializer(data=body)
    assert serializer.is_valid(), serializer.errors
    assert body["status"] is True
    event = body["result"]["events"][0]
    assert event["type"] == "edit"
    assert event["action"] == "edit"
    assert event["actor"] == a["user"].name
    assert event["actor_id"] == str(a["user"].pk)
    assert event["column"] == "question"
    assert event["column_id"] == str(a["column"].id)
    assert event["cell_id"] == str(a["cells"][0].id)
    assert event["before"] == previous
    assert event["after"] == "Changed"
    assert event["at"]


def test_activity_is_scoped_to_organization_workspace_and_row(tenants):
    a, b, clients = tenants
    assert clients["a"].get(_url(a)).status_code == 200
    assert clients["b"].get(_url(a)).status_code == 404
    assert clients["a"].get(_url(a, b["rows"][0])).status_code == 404
    other_workspace = Workspace.objects.create(
        name="Other activity workspace", organization=a["org"],
        is_default=False, is_active=True, created_by=a["user"],
    )
    Dataset.no_workspace_objects.filter(pk=a["dataset"].pk).update(
        workspace=other_workspace
    )
    refused = clients["a"].get(_url(a))
    assert refused.status_code == 404
    assert refused.json()["status"] is False
    assert refused.json()["code"] == "not_found"


def test_unauthenticated_activity_read_is_refused(tenants):
    a, _, _ = tenants
    assert APIClient().get(_url(a)).status_code in (401, 403)


def test_deleted_row_is_hidden_and_history_survives_restore(tenants):
    a, _, clients = tenants
    row = a["rows"][0]
    response = clients["a"].delete(
        f"/model-hub/develops/{a['dataset'].id}/delete_row/",
        {"row_ids": [str(row.id)]}, format="json",
    )
    assert response.status_code == 200, response.content
    assert clients["a"].get(_url(a)).status_code == 404
    assert _events(row).filter(subject="row", action="delete").count() == 1
    with workspace_context(a["workspace"], a["org"], a["user"]):
        Row.all_objects.filter(pk=row.pk).update(deleted=False, deleted_at=None)
        Cell.all_objects.filter(row=row).update(deleted=False, deleted_at=None)
    response = clients["a"].get(_url(a))
    assert response.status_code == 200
    events = response.json()["result"]["events"]
    assert any(e["type"] == "restored" for e in events)
    assert any(e["type"] == "deleted" for e in events)
    a["dataset"].delete()
    assert clients["a"].get(_url(a)).status_code == 404


def test_cursor_pagination_has_no_overlap_when_new_events_arrive(tenants):
    a, _, clients = tenants
    row = a["rows"][0]
    for value in ("one", "two", "three"):
        assert _edit(clients["a"], a, value).status_code == 200
    initial_ids = list(_events(row).values_list("id", flat=True))
    first = clients["a"].get(_url(a), {"page_size": 2}).json()["result"]
    assert first["has_more"] is True
    assert [int(e["id"]) for e in first["events"]] == initial_ids[:2]
    assert _edit(clients["a"], a, "arrived later").status_code == 200
    seen = [int(e["id"]) for e in first["events"]]
    cursor = first["next_cursor"]
    while cursor:
        response = clients["a"].get(_url(a), {"page_size": 2, "cursor": cursor})
        assert response.status_code == 200, response.content
        page = response.json()["result"]
        seen.extend(int(e["id"]) for e in page["events"])
        assert page["has_more"] == (page["next_cursor"] is not None)
        cursor = page["next_cursor"]
    assert seen == initial_ids
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("params", [
    {"page_size": 0}, {"page_size": 101}, {"page_size": "bad"},
    {"cursor": "not-a-cursor"}, {"unexpected": "field"},
])
def test_invalid_activity_query_is_a_typed_400(tenants, params):
    a, _, clients = tenants
    response = clients["a"].get(_url(a), params)
    assert response.status_code == 400, response.content
    assert response.json()["status"] is False
    assert response.json()["code"]


def test_cursor_is_bound_to_its_row(tenants):
    a, _, clients = tenants
    cursor = clients["a"].get(_url(a), {"page_size": 1}).json()["result"]["next_cursor"]
    assert cursor
    response = clients["a"].get(_url(a, a["rows"][1]), {"cursor": cursor})
    assert response.status_code == 400


def test_empty_history_is_not_invented_for_old_rows(tenants):
    a, _, clients = tenants
    _events(a["rows"][0]).delete()
    response = clients["a"].get(_url(a))
    assert response.status_code == 200
    assert response.json()["result"] == {
        "events": [], "has_more": False, "next_cursor": None,
    }


def test_noop_status_and_metadata_writes_do_not_make_cell_edits(tenants):
    a, _, _ = tenants
    cell = a["cells"][0]
    before = _events(cell.row).count()
    cell.save(update_fields=["value"])
    Cell.objects.filter(pk=cell.pk).update(value=F("value"))
    Cell.objects.filter(pk=cell.pk).update(feedback_info={"note": "metadata only"})
    Row.objects.filter(pk=cell.row_id).update(order=20)
    assert _events(cell.row).count() == before


def test_bulk_create_and_bulk_update_are_set_based_and_exact(tenants):
    a, _, _ = tenants
    rows = [Row(dataset=a["dataset"], order=100 + i) for i in range(120)]
    with workspace_context(a["workspace"], a["org"], a["user"]):
        with CaptureQueriesContext(connection) as queries:
            Row.objects.bulk_create(rows, batch_size=200)
            cells = [
                Cell(dataset=a["dataset"], row=row, column=a["column"], value="old")
                for row in rows
            ]
            Cell.objects.bulk_create(cells, batch_size=200)
            for cell in cells:
                cell.value = "new"
            Cell.objects.bulk_update(cells, ["value"], batch_size=200)
    # Transactions and actor setup are fixed per batch, not per row/cell.
    assert len(queries) <= 25
    for action, subject in (("create", "row"), ("create", "cell"), ("edit", "cell")):
        events = RowActivity.objects.filter(row__in=rows, action=action, subject=subject)
        assert events.count() == 120
        assert set(events.values_list("actor_id", flat=True)) == {str(a["user"].pk)}
    assert set(RowActivity.objects.filter(
        row__in=rows, action="edit"
    ).values_list("before_value", "after_value")) == {("old", "new")}


def test_ignored_conflict_is_not_recorded_as_an_insert(tenants):
    a, _, _ = tenants
    row = a["rows"][0]
    before = _events(row).count()
    Row.objects.bulk_create(
        [Row(id=row.id, dataset=a["dataset"], order=row.order)], ignore_conflicts=True
    )
    assert _events(row).count() == before


def test_transaction_rollback_removes_activity_and_actor_context(tenants):
    a, _, _ = tenants
    cell = a["cells"][0]
    before = _events(cell.row).count()
    with pytest.raises(RuntimeError), transaction.atomic():
        with workspace_context(a["workspace"], a["org"], a["user"]):
            Cell.objects.filter(pk=cell.pk).update(value="rolled back")
        raise RuntimeError("abort")
    assert _events(cell.row).count() == before
    cell.refresh_from_db()
    assert cell.value != "rolled back"
    Cell.objects.filter(pk=cell.pk).update(value="background")
    latest = _events(cell.row).first()
    assert latest.actor_id is None
    assert latest.actor == "System"


def test_file_import_records_the_uploader_without_worker_request_context(tenants):
    a, _, _ = tenants

    @file_import_activity
    def import_rows(dataset_id, file_url, original_filename):
        assert str(dataset_id) == str(a["dataset"].pk)
        assert file_url == "unused" and original_filename == "rows.csv"
        row = Row.objects.create(dataset=a["dataset"], order=99)
        Cell.objects.bulk_create([
            Cell(dataset=a["dataset"], row=row, column=a["column"], value="imported")
        ])
        return row

    clear_workspace_context()
    row = import_rows(str(a["dataset"].pk), "unused", "rows.csv")
    events = list(_events(row))
    assert len(events) == 2
    assert {e.actor_id for e in events} == {str(a["user"].pk)}
    assert {e.origin for e in events} == {"import"}
    assert {e.action for e in events} == {"create"}
    Cell.objects.filter(row=row).update(value="background")
    assert _events(row).first().actor_id is None


def test_large_values_are_bounded_without_missing_suffix_only_edits(tenants):
    a, _, clients = tenants
    cell = a["cells"][0]
    Cell.objects.filter(pk=cell.pk).update(value="x" * 4096 + "A")
    Cell.objects.filter(pk=cell.pk).update(value="x" * 4096 + "B")
    event = clients["a"].get(_url(a)).json()["result"]["events"][0]
    assert event["before"] == "x" * 2048
    assert event["after"] == "x" * 2048
    assert event["before_truncated"] is True
    assert event["after_truncated"] is True
    assert event["action"] == "edit"


def test_cell_delete_and_restore_are_recorded_once(tenants):
    a, _, _ = tenants
    cell = a["cells"][0]
    cell.delete()
    assert _events(cell.row).first().action == "delete"
    Cell.all_objects.filter(pk=cell.pk).update(deleted=False, deleted_at=None)
    assert _events(cell.row).first().action == "restore"
    Cell.objects.filter(pk=cell.pk).delete()
    assert _events(cell.row).filter(subject="cell", action="delete").count() == 2


def test_raw_bulk_insert_is_audited_and_actor_scope_is_restored(tenants):
    a, b, _ = tenants
    row = a["rows"][0]
    with transaction.atomic(), row_activity_actor(a["user"]):
        with row_activity_write("default"):
            with row_activity_actor(b["user"]), row_activity_write("default"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('futureagi.row_activity_actor')")
                    assert str(b["user"].pk) in cursor.fetchone()[0]
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO model_hub_cell
                    (id, dataset_id, row_id, column_id, value, created_at, updated_at,
                     deleted, status, value_infos, feedback_info, column_metadata)
                    VALUES (%s, %s, %s, %s, %s, now(), now(), false, 'pass',
                            '[]'::jsonb, '{}'::jsonb, '{}'::jsonb)""",
                    [uuid.uuid4(), a["dataset"].id, row.id, a["column"].id, "raw"],
                )
    event = _events(row).first()
    assert event.actor_id == str(a["user"].pk)
    assert event.after_value == "raw"
    assert event.action == "create"


def test_read_is_read_only_and_route_has_a_serializer_contract(tenants):
    from django.urls import resolve

    from model_hub.serializers.row_activity import RowActivityQuerySerializer
    from model_hub.views.row_activity import RowActivityView

    a, _, clients = tenants
    match = resolve(_url(a))
    assert match.func.view_class is RowActivityView
    schema = RowActivityView.get._swagger_auto_schema
    assert schema["query_serializer"] is RowActivityQuerySerializer
    assert schema["responses"][200] is RowActivityResponseSerializer
    count = RowActivity.objects.count()
    assert clients["a"].get(_url(a)).status_code == 200
    assert clients["a"].post(_url(a), {}, format="json").status_code == 405
    assert RowActivity.objects.count() == count
