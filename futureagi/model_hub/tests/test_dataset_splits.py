"""Dataset split writes, projections and exports use the authorized live rows."""

import json
import uuid
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import PermissionDenied

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.dataset_splits import DatasetSplit
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.dataset_splits import project_split_membership
from model_hub.tests.test_dataset_saved_views import scopes as _access_scopes
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
    workspace_context,
)
from tfc.permissions.utils import get_effective_workspace_level

SCHEMA = {
    "declared": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
    },
    "enforce": "check",
    "preset": "inferred",
}


def path(tenant, suffix=""):
    return f"/model-hub/datasets/{tenant['dataset'].id}/splits/{suffix}"


def schema_path(tenant):
    return f"/model-hub/datasets/{tenant['dataset'].id}/schema/"


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


def _org_member(org, workspace, level, role, tag):
    """An organization Member whose one workspace membership has ``level``."""
    set_workspace_context(workspace=workspace, organization=org)
    user = User.objects.create_user(
        email=f"splits-{tag}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="synthetic-split-password",
        name=f"{tag} user",
        organization=org,
        organization_role="Member",
    )
    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=org,
        defaults={"role": "Member", "level": Level.MEMBER, "is_active": True},
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": role,
            "level": level,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    clear_workspace_context()
    return user


@pytest.fixture
def no_workspace_scopes(access_scopes):
    """Tenant A's dataset is a legacy dataset with no workspace, like every
    seeded demo dataset.

    Adds a member of a second, non-default workspace in the same organization,
    and a default-workspace user whose workspace role cannot edit rows.
    """
    a, b, clients = access_scopes
    Dataset._base_manager.filter(pk=a["dataset"].pk).update(workspace=None)
    a["dataset"].refresh_from_db()
    set_workspace_context(organization=a["org"])
    other = Workspace.objects.create(
        name=f"splits-other-{uuid.uuid4().hex[:6]}",
        organization=a["org"],
        is_default=False,
        is_active=True,
        created_by=a["user"],
    )
    a["other_workspace"] = other
    member = _org_member(a["org"], other, Level.MEMBER, "workspace_member", "other")
    a["reader"] = _org_member(
        a["org"],
        a["workspace"],
        Level.WORKSPACE_VIEWER,
        "workspace_viewer",
        "reader",
    )
    extra = {
        "other_workspace": _client({**a, "user": member, "workspace": other}),
        "reader": _client({**a, "user": a["reader"]}),
    }
    yield a, b, {**clients, **extra}
    for client in extra.values():
        client.stop_workspace_injection()
    clear_workspace_context()


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


@pytest.mark.django_db
class TestDatasetWithNoWorkspace:
    """A dataset with no workspace is part of its organization's default
    workspace, as the grid already shows it, so its splits and schema open
    there too. Other workspaces and organizations still cannot reach it."""

    def test_default_workspace_member_lists_no_splits(self, no_workspace_scopes):
        a, _, clients = no_workspace_scopes
        assert result(clients["member"].get(path(a))) == {
            "splits": [],
            "no_split_count": 2,
        }

    def test_default_workspace_member_writes_and_reads_splits(
        self, no_workspace_scopes
    ):
        a, _, clients = no_workspace_scopes
        member = clients["member"]
        # Creating a split locks the dataset row.
        created = split(member, a)
        assert [s["id"] for s in result(member.get(path(a)))["splits"]] == [
            created["id"]
        ]
        assert result(assign(member, a, a["rows"][:1], [created["id"]])) == {
            "updated": 1
        }
        assert result(
            member.get(path(a, "membership/"), {"row_id": str(a["rows"][0].id)})
        ) == {
            "row_id": str(a["rows"][0].id),
            "split_ids": [created["id"]],
        }
        exported = result(member.get(path(a, "export/"), {"split_id": created["id"]}))
        assert [row["row_id"] for row in exported["rows"]] == [str(a["rows"][0].id)]
        renamed = result(
            member.patch(
                path(a, f"{created['id']}/"), {"name": "training"}, format="json"
            )
        )
        assert renamed["name"] == "training"
        result(member.delete(path(a, f"{created['id']}/")))
        assert result(member.get(path(a))) == {"splits": [], "no_split_count": 2}
        a["dataset"].refresh_from_db()
        assert a["dataset"].workspace_id is None

    def test_grid_filters_and_shows_splits(self, no_workspace_scopes):
        a, _, clients = no_workspace_scopes
        member = clients["member"]
        unsplit = result(table(member, a, ["__none__"]))["table"]
        assert {row["row_id"] for row in unsplit} == {str(row.id) for row in a["rows"]}
        created = split(member, a)
        result(assign(member, a, a["rows"][:1], [created["id"]]))
        rows = result(table(member, a, [created["id"]]))["table"]
        assert [row["row_id"] for row in rows] == [str(a["rows"][0].id)]
        assert rows[0]["row_meta"]["split_ids"] == [created["id"]]
        rest = result(table(member, a, ["__none__"]))["table"]
        assert [row["row_id"] for row in rest] == [str(a["rows"][1].id)]
        assert rest[0]["row_meta"]["split_ids"] == []

    def test_default_workspace_member_reads_and_saves_the_schema(
        self, no_workspace_scopes
    ):
        a, _, clients = no_workspace_scopes
        member = clients["member"]
        assert result(member.get(schema_path(a))) == {
            "declared": {},
            "enforce": "off",
            "preset": "",
        }
        response = member.put(schema_path(a), SCHEMA, format="json")
        assert response.status_code == 200, response.content
        assert result(member.get(schema_path(a))) == SCHEMA
        a["dataset"].refresh_from_db()
        assert a["dataset"].dataset_config["schema"] == SCHEMA
        assert a["dataset"].workspace_id is None

    @pytest.mark.parametrize("actor", ["other_workspace", "foreign"])
    def test_other_workspaces_and_organizations_still_get_not_found(
        self, no_workspace_scopes, actor
    ):
        a, _, clients = no_workspace_scopes
        own = split(clients["member"], a)
        client = clients[actor]
        for response in (
            client.get(path(a)),
            client.post(path(a), {"name": "intrusion"}, format="json"),
            client.patch(path(a, f"{own['id']}/"), {"name": "taken"}, format="json"),
            client.delete(path(a, f"{own['id']}/")),
            assign(client, a, a["rows"][:1], [own["id"]]),
            client.get(path(a, "membership/"), {"row_id": str(a["rows"][0].id)}),
            client.get(path(a, "export/"), {"split_id": own["id"]}),
            client.get(schema_path(a)),
            client.put(schema_path(a), SCHEMA, format="json"),
            table(client, a, ["__none__"]),
        ):
            assert response.status_code == 404, response.content
        a["dataset"].refresh_from_db()
        assert "schema" not in (a["dataset"].dataset_config or {})
        assert [
            (s["id"], s["name"], s["row_count"])
            for s in result(clients["member"].get(path(a)))["splits"]
        ] == [(own["id"], "train", 0)]

    def test_default_workspace_user_who_cannot_edit_rows_cannot_change_splits(
        self, no_workspace_scopes
    ):
        a, _, clients = no_workspace_scopes
        # An organization Member whose default-workspace role is viewer passes
        # the workspace level check but not the row and cell edit check.
        assert not a["reader"].can_write_to_workspace(a["workspace"])
        level = get_effective_workspace_level(a["reader"], a["workspace"].id)
        assert level >= Level.MEMBER
        reader = clients["reader"]
        assert result(reader.get(path(a))) == {"splits": [], "no_split_count": 2}
        assert result(reader.get(schema_path(a)))["enforce"] == "off"
        own = split(clients["member"], a)
        for response in (
            reader.post(path(a), {"name": "blocked"}, format="json"),
            reader.patch(path(a, f"{own['id']}/"), {"name": "blocked"}, format="json"),
            reader.delete(path(a, f"{own['id']}/")),
            assign(reader, a, a["rows"][:1], [own["id"]]),
            reader.put(schema_path(a), SCHEMA, format="json"),
        ):
            assert response.status_code == 403, response.content
        assert [
            (s["id"], s["name"], s["row_count"])
            for s in result(reader.get(path(a)))["splits"]
        ] == [(own["id"], "train", 0)]
        a["dataset"].refresh_from_db()
        assert "schema" not in (a["dataset"].dataset_config or {})

    @pytest.mark.parametrize(
        "actor,expected",
        [("member", 200), ("other_workspace", 404), ("foreign", 404)],
    )
    def test_split_and_schema_writes_reach_whoever_may_change_dataset_settings(
        self, no_workspace_scopes, actor, expected
    ):
        a, _, clients = no_workspace_scopes
        client = clients[actor]
        # An existing write on the same dataset, unchanged by this work.
        settings = client.put(
            f"/model-hub/develops/{a['dataset'].id}/edit_dataset_behavior/",
            {"dataset_config": {"dismiss_banner": True}},
            format="json",
        )
        created = client.post(path(a), {"name": "parity"}, format="json")
        saved = client.put(schema_path(a), SCHEMA, format="json")
        statuses = (settings.status_code, created.status_code, saved.status_code)
        assert statuses == (expected, expected, expected), statuses

    def test_no_workspace_rows_are_never_admitted_from_a_non_default_workspace(
        self, no_workspace_scopes
    ):
        from model_hub.utils.workspace_scope import legacy_dataset_filter

        a, _, clients = no_workspace_scopes
        default, other = a["workspace"], a["other_workspace"]
        datasets = Dataset.no_workspace_objects.filter(pk=a["dataset"].pk)
        rows = Row.no_workspace_objects.filter(dataset=a["dataset"])
        # The rule every changed lookup uses, by dataset and by row.
        by_row = "dataset__workspace"
        assert datasets.filter(legacy_dataset_filter(default)).count() == 1
        assert rows.filter(legacy_dataset_filter(default, by_row)).count() == 2
        assert not datasets.filter(legacy_dataset_filter(other)).exists()
        assert not rows.filter(legacy_dataset_filter(other, by_row)).exists()
        assert not datasets.filter(legacy_dataset_filter(None)).exists()
        # The grid's split projection, which no request can reach from another
        # workspace because the grid's own lookup refuses first.
        own = split(clients["member"], a)
        result(assign(clients["member"], a, a["rows"][:1], [own["id"]]))
        projected = [{"row_id": str(a["rows"][0].id)}]
        project_split_membership(
            projected,
            a["dataset"],
            SimpleNamespace(organization=a["org"], workspace=default),
            lambda **_: None,
        )
        assert projected[0]["row_meta"]["split_ids"] == [own["id"]]
        with pytest.raises(PermissionDenied):
            project_split_membership(
                [{"row_id": str(a["rows"][0].id)}],
                a["dataset"],
                SimpleNamespace(organization=a["org"], workspace=other),
                lambda **_: None,
            )

    def test_default_workspace_viewer_reads_but_cannot_write(self, no_workspace_scopes):
        a, _, clients = no_workspace_scopes
        viewer = clients["viewer"]
        assert result(viewer.get(path(a))) == {"splits": [], "no_split_count": 2}
        assert result(viewer.get(schema_path(a)))["enforce"] == "off"
        own = split(clients["member"], a)
        assert [s["id"] for s in result(viewer.get(path(a)))["splits"]] == [own["id"]]
        for response in (
            viewer.post(path(a), {"name": "blocked"}, format="json"),
            viewer.patch(path(a, f"{own['id']}/"), {"name": "blocked"}, format="json"),
            viewer.delete(path(a, f"{own['id']}/")),
            assign(viewer, a, a["rows"], [own["id"]]),
            viewer.put(schema_path(a), SCHEMA, format="json"),
        ):
            assert response.status_code == 403, response.content
        assert result(viewer.get(path(a)))["splits"][0]["row_count"] == 0
        a["dataset"].refresh_from_db()
        assert "schema" not in (a["dataset"].dataset_config or {})

    def test_snapshot_of_a_dataset_with_no_workspace_opens_in_the_default_workspace(
        self, no_workspace_scopes
    ):
        a, _, clients = no_workspace_scopes
        # An experiment snapshot keeps its source's tenant, including no
        # workspace. Create it outside a request, as background runs do.
        with workspace_context(None, organization=a["org"], user=a["user"]):
            experiment = ExperimentsTable.objects.create(
                dataset=a["dataset"], name="Snapshot source", user=a["user"]
            )
            snapshot, _, _ = create_dataset_snapshot(a["dataset"], experiment)
        assert snapshot.workspace_id is None
        assert snapshot.organization_id == a["dataset"].organization_id
        shot = {**a, "dataset": snapshot}
        member = clients["member"]
        assert result(member.get(path(shot))) == {"splits": [], "no_split_count": 2}
        assert result(member.get(schema_path(shot)))["enforce"] == "off"
        for actor in ("other_workspace", "foreign"):
            for response in (
                clients[actor].get(path(shot)),
                clients[actor].post(path(shot), {"name": "blocked"}, format="json"),
                clients[actor].get(schema_path(shot)),
                clients[actor].put(schema_path(shot), SCHEMA, format="json"),
            ):
                assert response.status_code == 404, response.content
