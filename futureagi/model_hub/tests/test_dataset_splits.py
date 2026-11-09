"""Dataset split writes, projections and exports use the authorized live rows."""

import json

import pytest

from model_hub.tests.test_dataset_saved_views import scopes as _access_scopes
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context


def path(tenant, suffix=""):
    return f"/model-hub/datasets/{tenant['dataset'].id}/splits/{suffix}"


def result(response):
    assert response.status_code == 200, response.content
    return response.json()["result"]


def split(client, tenant, name="train"):
    return result(client.post(path(tenant), {"name": name}, format="json"))


def assign(client, tenant, rows, add=(), remove=()):
    return client.post(
        path(tenant, "membership/"),
        {
            "row_ids": [str(row.id) for row in rows],
            "add": list(add),
            "remove": list(remove),
        },
        format="json",
    )


def table(client, tenant, ids=None):
    filters = (
        []
        if ids is None
        else [
            {
                "column_id": "system:splits",
                "filter_config": {
                    "filter_type": "categorical",
                    "filter_op": "in",
                    "filter_value": ids,
                },
            }
        ]
    )
    return client.get(
        f"/model-hub/develops/{tenant['dataset'].id}/get-dataset-table/",
        {"filters": json.dumps(filters), "page_size": 100},
    )


@pytest.fixture
def scopes(db):
    a, b = _tenant("splits-a"), _tenant("splits-b")
    ca, cb = _client(a), _client(b)
    yield a, b, ca, cb
    ca.stop_workspace_injection()
    cb.stop_workspace_injection()
    clear_workspace_context()


@pytest.fixture
def access_scopes(db):
    yield from _access_scopes.__wrapped__(db)


@pytest.mark.django_db
class TestDatasetSplits:
    def test_crud_persists_and_counts_live_memberships(self, scopes):
        a, _, ca, _ = scopes
        created = split(ca, a)
        assert created["name"] == "train"
        assert created["row_count"] == 0
        result(assign(ca, a, a["rows"][:1], [created["id"]]))
        listing = result(ca.get(path(a)))
        assert [(s["id"], s["row_count"]) for s in listing["splits"]] == [
            (created["id"], 1)
        ]
        assert listing["no_split_count"] == 1
        updated = result(
            ca.patch(
                path(a, f"{created['id']}/"),
                {
                    "name": "training",
                    "description": "Current training examples",
                    "color": "info",
                },
                format="json",
            )
        )
        assert updated["description"] == "Current training examples"
        assert result(ca.get(path(a)))["splits"][0]["name"] == "training"
        result(ca.delete(path(a, f"{created['id']}/")))
        assert result(ca.get(path(a))) == {"splits": [], "no_split_count": 2}
        assert all(
            row.get("row_meta", {}).get("split_ids", []) == []
            for row in result(table(ca, a))["table"]
        )

    def test_membership_is_idempotent_and_projected_into_filtered_rows(self, scopes):
        a, _, ca, _ = scopes
        train, test = split(ca, a), split(ca, a, "test")
        for _ in range(2):
            result(assign(ca, a, a["rows"][:1], [train["id"], test["id"]]))
        rows = result(table(ca, a, [train["id"]]))["table"]
        assert [row["row_id"] for row in rows] == [str(a["rows"][0].id)]
        assert set(rows[0]["row_meta"]["split_ids"]) == {train["id"], test["id"]}
        assert [r["row_id"] for r in result(table(ca, a, ["__none__"]))["table"]] == [
            str(a["rows"][1].id)
        ]
        assert len(result(table(ca, a, [train["id"], "__none__"]))["table"]) == 2
        result(assign(ca, a, a["rows"][:1], remove=[train["id"]]))
        assert result(table(ca, a, [train["id"]]))["table"] == []
        assert (
            result(ca.get(path(a)))["splits"][1]["row_count"] == 0
            or result(ca.get(path(a)))["splits"][0]["row_count"] == 0
        )

    def test_foreign_ids_and_mixed_batches_leave_membership_unchanged(self, scopes):
        a, b, ca, cb = scopes
        own, foreign = split(ca, a), split(cb, b)
        attempts = [
            cb.get(path(a)),
            cb.post(path(a), {"name": "intrusion"}, format="json"),
            cb.patch(path(a, f"{own['id']}/"), {"name": "intrusion"}, format="json"),
            cb.delete(path(a, f"{own['id']}/")),
            ca.patch(
                path(a, f"{foreign['id']}/"), {"name": "intrusion"}, format="json"
            ),
            assign(ca, a, [a["rows"][0], b["rows"][0]], [own["id"]]),
            assign(ca, a, a["rows"], [own["id"], foreign["id"]]),
            table(ca, a, [foreign["id"]]),
            cb.get(path(a, "export/"), {"split_id": own["id"]}),
            ca.get(path(a, "export/"), {"split_id": foreign["id"]}),
        ]
        assert all(r.status_code in (400, 403, 404) for r in attempts), [
            (r.status_code, r.content) for r in attempts
        ]
        assert result(ca.get(path(a)))["splits"][0]["row_count"] == 0
        assert result(cb.get(path(b)))["splits"][0]["row_count"] == 0

    def test_export_reads_only_selected_live_rows_and_columns(self, scopes):
        a, _, ca, _ = scopes
        train = split(ca, a)
        result(assign(ca, a, a["rows"][:1], [train["id"]]))
        exported = result(ca.get(path(a, "export/"), {"split_id": train["id"]}))
        assert exported["dataset_id"] == str(a["dataset"].id)
        assert exported["split"]["id"] == train["id"]
        assert exported["rows"] == [
            {"row_id": str(a["rows"][0].id), "fields": {"question": "splits-a-value-0"}}
        ]
        assert "version" not in exported
        a["rows"][0].deleted = True
        a["rows"][0].save(update_fields=["deleted"])
        assert (
            result(ca.get(path(a, "export/"), {"split_id": train["id"]}))["rows"] == []
        )

    def test_invalid_requests_fail_without_writes(self, scopes):
        a, _, ca, _ = scopes
        for body in (
            {"name": "Bad name"},
            {"name": "ok", "color": "not-a-color"},
            {"name": "ok", "version_id": "bogus"},
        ):
            assert ca.post(path(a), body, format="json").status_code == 400
        own = split(ca, a)
        assert ca.post(path(a), {"name": "train"}, format="json").status_code == 409
        assert assign(ca, a, a["rows"], [own["id"]], [own["id"]]).status_code == 400
        assert table(ca, a, ["not-a-uuid"]).status_code == 400
        assert result(ca.get(path(a)))["splits"][0]["row_count"] == 0

    def test_read_only_role_cannot_change_splits(self, access_scopes):
        a, _, clients = access_scopes
        own = split(clients["owner"], a)
        assert result(clients["viewer"].get(path(a)))["splits"][0]["id"] == own["id"]
        for response in (
            clients["viewer"].post(path(a), {"name": "blocked"}, format="json"),
            clients["viewer"].patch(
                path(a, f"{own['id']}/"), {"name": "blocked"}, format="json"
            ),
            clients["viewer"].delete(path(a, f"{own['id']}/")),
            assign(clients["viewer"], a, a["rows"], [own["id"]]),
        ):
            assert response.status_code == 403, response.content
        result(assign(clients["member"], a, a["rows"][:1], [own["id"]]))
        assert result(clients["owner"].get(path(a)))["splits"][0]["row_count"] == 1

    def test_row_membership_read_and_saved_view_resolve_scoped_splits(self, scopes):
        a, b, ca, cb = scopes
        own = split(ca, a)
        split(cb, b, "foreign")
        result(assign(ca, a, a["rows"][:1], [own["id"]]))
        assert result(
            ca.get(path(a, "membership/"), {"row_id": str(a["rows"][0].id)})
        ) == {
            "row_id": str(a["rows"][0].id),
            "split_ids": [own["id"]],
        }
        assert (
            ca.get(path(a, "membership/"), {"row_id": str(b["rows"][0].id)}).status_code
            == 404
        )
        body = {
            "surface": "grid",
            "scope": str(a["dataset"].id),
            "name": "Train rows",
            "state": {"splits": "train,none"},
        }
        assert (
            result(ca.post("/model-hub/r3/saved-views/", body, format="json"))["state"][
                "splits"
            ]
            == "train,none"
        )
        body["state"]["splits"] = "foreign"
        assert (
            ca.post("/model-hub/r3/saved-views/", body, format="json").status_code
            == 400
        )

    def test_empty_dataset_can_filter_its_own_split(self, scopes):
        a, _, ca, _ = scopes
        own = split(ca, a)
        for row in a["rows"]:
            row.deleted = True
            row.save(update_fields=["deleted"])
        assert result(table(ca, a, [own["id"]]))["table"] == []

    def test_split_changes_invalidate_exact_table_revision(self, scopes):
        from model_hub.services.dataset_table_snapshot import _read_revision_state

        a, _, ca, _ = scopes

        def revision():
            return _read_revision_state(str(a["dataset"].id))

        before = revision()
        own = split(ca, a)
        after_create = revision()
        assert after_create != before
        result(assign(ca, a, a["rows"][:1], [own["id"]]))
        after_assign = revision()
        assert after_assign != after_create
        result(ca.delete(path(a, f"{own['id']}/")))
        assert revision() != after_assign

    def test_row_navigation_respects_membership_filter(self, scopes):
        a, _, ca, _ = scopes
        own = split(ca, a)
        result(assign(ca, a, a["rows"][:1], [own["id"]]))
        url = f"/model-hub/develops/{a['dataset'].id}/get-row-data/"
        filters = [
            {
                "column_id": "system:splits",
                "filter_config": {
                    "filter_type": "categorical",
                    "filter_op": "in",
                    "filter_value": [own["id"]],
                },
            }
        ]
        response = ca.post(
            url, {"row_id": str(a["rows"][0].id), "filters": filters}, format="json"
        )
        assert result(response)["current"]["row_id"] == str(a["rows"][0].id)
        assert (
            ca.post(
                url, {"row_id": str(a["rows"][1].id), "filters": filters}, format="json"
            ).status_code
            == 404
        )

    def test_rename_preserves_saved_filter_and_referenced_split_is_not_deleted(
        self, scopes
    ):
        a, _, ca, _ = scopes
        own = split(ca, a)
        body = {
            "surface": "grid",
            "scope": str(a["dataset"].id),
            "name": "Train rows",
            "state": {"splits": "train,none"},
        }
        view = result(ca.post("/model-hub/r3/saved-views/", body, format="json"))
        result(ca.patch(path(a, f"{own['id']}/"), {"name": "training"}, format="json"))
        listed = result(
            ca.get(
                "/model-hub/r3/saved-views/",
                {"surface": "grid", "scope": str(a["dataset"].id)},
            )
        )
        assert listed["results"][0]["state"]["splits"] == "training,none"
        assert ca.delete(path(a, f"{own['id']}/")).status_code == 400
        result(ca.delete(f"/model-hub/r3/saved-views/{view['id']}/"))
        result(ca.delete(path(a, f"{own['id']}/")))
