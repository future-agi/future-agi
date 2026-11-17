"""Saved grid views persist only within their authorized dataset and workspace."""

import copy
import uuid

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

URL = "/model-hub/r3/saved-views/"


@pytest.fixture
def scopes(db):
    a, b = _tenant("views-a"), _tenant("views-b")
    actors = {"owner": a, "foreign": b}
    for name, level in (("member", Level.MEMBER), ("viewer", Level.VIEWER)):
        set_workspace_context(workspace=a["workspace"], organization=a["org"])
        user = User.objects.create_user(
            email=f"views-{name}-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="synthetic-view-password",
            name=name,
            organization=a["org"],
            organization_role=name.title(),
        )
        membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
            user=user,
            organization=a["org"],
            defaults={"role": name.title(), "level": level, "is_active": True},
        )
        WorkspaceMembership.no_workspace_objects.update_or_create(
            user=user,
            workspace=a["workspace"],
            defaults={
                "role": f"workspace_{name}",
                "level": level,
                "is_active": True,
                "organization_membership": membership,
            },
        )
        actors[name] = {**a, "user": user}
    clients = {key: _client(value) for key, value in actors.items()}
    yield a, b, clients
    for client in clients.values():
        client.stop_workspace_injection()
    clear_workspace_context()


def body(a, **extra):
    col = str(a["column"].id)
    return {
        "surface": "grid",
        "scope": str(a["dataset"].id),
        "name": "My view",
        "state": {
            "q": "value",
            "density": "compact",
            "failed": False,
            "splits": "",
            "expr": "",
            "statuses": [],
            "tags": [],
            "filters": [
                {
                    "columnId": col,
                    "filterConfig": {
                        "filterType": "text",
                        "filterOp": "equals",
                        "filterValue": "a-value-0",
                    },
                }
            ],
            "sort": [{"colId": col, "sort": "desc"}],
            "layout": {"columns": {"visible": {col: True}, "widths": {col: 220}}},
        },
        **extra,
    }


def create(client, payload):
    response = client.post(URL, payload, format="json")
    assert response.status_code == 200, response.content
    return response.json()["result"]


def listed(client, a):
    response = client.get(URL, {"surface": "grid", "scope": str(a["dataset"].id)})
    assert response.status_code == 200, response.content
    return response.json()["result"]["results"]


@pytest.mark.django_db
class TestSavedGridViews:
    def test_round_trip_private_state_and_shared_visibility(self, scopes):
        a, _, clients = scopes
        payload = body(a)
        private = create(clients["owner"], payload)
        assert private["state"] == payload["state"]
        assert private["mine"] is True and private["can_delete"] is True
        assert listed(clients["member"], a) == []
        shared = create(clients["owner"], body(a, name="Shared", shared=True))
        visible = listed(clients["member"], a)
        assert [v["id"] for v in visible] == [shared["id"]]
        assert visible[0]["mine"] is False and visible[0]["can_delete"] is False
        assert {v["id"] for v in listed(clients["owner"], a)} == {
            private["id"],
            shared["id"],
        }

    def test_delete_restore_preserves_identity_author_and_state(self, scopes):
        a, _, clients = scopes
        saved = create(clients["owner"], body(a))
        response = clients["owner"].delete(f"{URL}{saved['id']}/")
        assert response.status_code == 200, response.content
        assert listed(clients["owner"], a) == []
        restored = create(
            clients["owner"],
            {
                "restore_id": saved["id"],
                "surface": "grid",
                "scope": str(a["dataset"].id),
            },
        )
        assert restored == saved
        assert [v["id"] for v in listed(clients["owner"], a)] == [saved["id"]]

    def test_foreign_dataset_and_view_operations_are_refused(self, scopes):
        a, b, clients = scopes
        saved = create(clients["owner"], body(a, shared=True))
        own = create(clients["foreign"], body(b))
        assert listed(clients["foreign"], b)[0]["id"] == own["id"]
        attempts = [
            clients["foreign"].get(
                URL, {"surface": "grid", "scope": str(a["dataset"].id)}
            ),
            clients["foreign"].post(URL, body(a), format="json"),
            clients["foreign"].delete(f"{URL}{saved['id']}/"),
            clients["foreign"].post(
                URL,
                {
                    "restore_id": saved["id"],
                    "surface": "grid",
                    "scope": str(b["dataset"].id),
                },
                format="json",
            ),
        ]
        assert all(r.status_code in (403, 404) for r in attempts)
        assert listed(clients["owner"], a)[0] == saved

    def test_viewer_can_save_private_but_cannot_share(self, scopes):
        a, _, clients = scopes
        saved = create(clients["viewer"], body(a))
        denied = clients["viewer"].post(
            URL, body(a, name="Not shared", shared=True), format="json"
        )
        assert denied.status_code == 403, denied.content
        assert [v["id"] for v in listed(clients["viewer"], a)] == [saved["id"]]
        assert listed(clients["owner"], a) == []

    def test_shared_delete_requires_author_or_admin(self, scopes):
        a, _, clients = scopes
        saved = create(clients["member"], body(a, shared=True))
        denied = clients["viewer"].delete(f"{URL}{saved['id']}/")
        assert denied.status_code == 403, denied.content
        assert clients["owner"].delete(f"{URL}{saved['id']}/").status_code == 200
        assert clients["viewer"].post(
            URL,
            {
                "restore_id": saved["id"],
                "surface": "grid",
                "scope": str(a["dataset"].id),
            },
            format="json",
        ).status_code in (403, 404)
        assert (
            create(
                clients["owner"],
                {
                    "restore_id": saved["id"],
                    "surface": "grid",
                    "scope": str(a["dataset"].id),
                },
            )["id"]
            == saved["id"]
        )

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda p, col: p["state"].update(sort=[{"colId": col, "sort": "asc"}]),
            lambda p, col: p["state"]["layout"].update(
                columns={"visible": {col: False}}
            ),
            lambda p, col: p["state"]["filters"][0].update(columnId=col),
            lambda p, col: p["state"].update(density="invalid"),
            lambda p, col: p.update(surface="results"),
            lambda p, col: p["state"].update(expr="unbacked expression"),
            lambda p, col: p["state"].update(fevals=[col]),
            lambda p, col: p["state"].update(density=[]),
            lambda p, col: p["state"]["filters"][0].update(columnId=[]),
            lambda p, col: p["state"]["filters"][0]["filterConfig"].update(
                filterOp="unsupported"
            ),
        ],
    )
    def test_invalid_or_inaccessible_state_is_not_saved(self, scopes, mutate):
        a, b, clients = scopes
        payload = copy.deepcopy(body(a))
        mutate(payload, str(b["column"].id))
        response = clients["owner"].post(URL, payload, format="json")
        assert response.status_code == 400, response.content
        assert listed(clients["owner"], a) == []

    def test_duplicate_name_does_not_overwrite_and_restore_conflict_is_atomic(
        self, scopes
    ):
        a, _, clients = scopes
        first = create(clients["owner"], body(a))
        assert clients["owner"].post(URL, body(a), format="json").status_code == 409
        assert clients["owner"].delete(f"{URL}{first['id']}/").status_code == 200
        second = create(clients["owner"], body(a))
        response = clients["owner"].post(
            URL,
            {
                "restore_id": first["id"],
                "surface": "grid",
                "scope": str(a["dataset"].id),
            },
            format="json",
        )
        assert response.status_code == 409, response.content
        assert [v["id"] for v in listed(clients["owner"], a)] == [second["id"]]

    def test_keyset_pages_preserve_exact_visible_set_and_refuse_foreign_cursors(
        self, scopes
    ):
        a, b, clients = scopes
        first = create(clients["owner"], body(a, name="First"))
        second = create(clients["owner"], body(a, name="Second"))
        foreign = create(clients["foreign"], body(b))
        params = {"surface": "grid", "scope": str(a["dataset"].id), "page_size": 1}
        page = clients["owner"].get(URL, params).json()["result"]
        assert [v["id"] for v in page["results"]] == [first["id"]]
        assert page["next"] == first["id"]
        last = (
            clients["owner"]
            .get(URL, {**params, "after": page["next"]})
            .json()["result"]
        )
        assert [v["id"] for v in last["results"]] == [second["id"]]
        assert last["next"] is None
        assert (
            clients["owner"].get(URL, {**params, "after": foreign["id"]}).status_code
            == 404
        )
        assert (
            clients["owner"].get(URL, {**params, "page_size": 101}).status_code == 400
        )

    def test_legacy_default_workspace_is_scoped_to_its_organization(self, scopes):
        from model_hub.models.develop_dataset import Dataset

        a, _, clients = scopes
        Dataset.no_workspace_objects.filter(id=a["dataset"].id).update(workspace=None)
        saved = create(clients["owner"], body(a))
        assert listed(clients["owner"], a)[0]["id"] == saved["id"]
        denied = clients["foreign"].get(
            URL, {"surface": "grid", "scope": str(a["dataset"].id)}
        )
        assert denied.status_code == 404

    def test_no_value_filter_round_trips_without_inventing_a_value(self, scopes):
        a, _, clients = scopes
        payload = body(a)
        payload["state"]["filters"][0]["filterConfig"].update(
            filterOp="is_null", filterValue=""
        )
        saved = create(clients["owner"], payload)
        assert saved["state"]["filters"] == payload["state"]["filters"]
