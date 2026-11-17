"""WT-18 (the section 9.1 matrix, each person with their own API key, over FA's real authentication) and WT-19 (two
tenants: 404, never 403; events never cross). WT-18b (offboarding) is in ``test_api_offboarding.py``; the cells for
endpoints that carry admin-only bodies are in ``test_api_admin_bodies.py``.
"""

from __future__ import annotations

import time

import pytest
from django.db import transaction

from accounts.models.organization import Organization
from tfc.constants.levels import Level
from webhooks.models import Delivery, Endpoint, Policy
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests.api_support import client_for, person, workspace_in
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys

EP = "/webhooks/endpoints/"


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


def _event(org, ws, key):
    with transaction.atomic():
        return publish(
            type="prompt.label.changed",
            organization_id=org.id,
            workspace_id=None if ws is None else ws.id,
            data=label_event_data(),
            source_key=key,
            source_hash="h",
            producer="test",
            occurred_at=T0,
        ).event_id


@pytest.fixture
def world(organization, workspace, user, keys):
    """One org, workspace A (the default) and B; a workspace endpoint in A, a full-mode one in A, an org-wide one;
    one delivery on each; and one person per role, with a key and a client in A."""
    org, ws_a = organization, workspace
    ws_b = workspace_in(org, "B", user)
    ep = make_endpoint(org, ws_a, name="a-thin")
    full = make_endpoint(org, ws_a, name="a-full", payload_mode="full")
    org_wide = make_endpoint(org, None, name="org-wide")
    in_b = make_endpoint(org, ws_b, name="b-thin")
    _event(org, ws_a, "e1")
    _event(org, ws_b, "e2")
    OrmStore().fan_out(int(time.time() * 1000), 10)
    people = {
        "viewer": person(org, ws_a, Level.VIEWER, Level.WORKSPACE_VIEWER, "viewer"),
        "member": person(org, ws_a, Level.MEMBER, Level.WORKSPACE_MEMBER, "member"),
        "ws_admin": person(org, ws_a, Level.MEMBER, Level.WORKSPACE_ADMIN, "wsadmin"),
        "org_admin": person(org, ws_a, Level.ADMIN, None, "orgadmin"),
        "owner": user,
    }
    clients = {k: client_for(u, org, ws_a) for k, u in people.items()}
    return {
        "org": org,
        "a": ws_a,
        "b": ws_b,
        "ep": ep,
        "full": full,
        "org_wide": org_wide,
        "in_b": in_b,
        "people": people,
        "c": clients,
        "dlv": Delivery.objects.get(endpoint=ep),
        "dlv_full": Delivery.objects.get(endpoint=full),
        "dlv_org": Delivery.objects.filter(endpoint=org_wide).first(),
    }


def _status(r):
    return r.status_code


def _create(c, **kw):
    b = {
        "name": f"n{time.time_ns()}",
        "url": "https://hooks.acme.ai/x",
        "subscriptions": [{"event_type": "prompt.*"}],
    }
    b.update(kw)
    return c.post(EP, b, format="json")


ROLES = ("viewer", "member", "ws_admin", "org_admin", "owner")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mcm", [False, True], ids=["members_cannot_manage", "members_can_manage"]
)
def test_wt18_rbac_matrix(world, mcm):
    w = world
    Policy.objects.create(organization=w["org"], members_can_manage=mcm)
    ep, _full, org_wide = w["ep"], w["full"], w["org_wide"]
    got = {}
    for role in ROLES:
        c = w["c"][role]
        row = {}
        lst = c.get(EP, {"workspace": str(w["a"].id)})
        row["list"] = _status(lst)
        names = (
            {e["name"] for e in lst.json().get("result", {}).get("results", [])}
            if row["list"] == 200
            else set()
        )
        row["sees_org_wide_row"] = "org-wide" in names
        det = c.get(f"{EP}{ep.id}/")
        row["detail"] = _status(det)
        row["sees_url"] = (
            row["detail"] == 200 and det.json()["result"]["url"] is not None
        )
        row["log"] = _status(c.get("/webhooks/deliveries/", {"endpoint": ep.id}))
        dfull = c.get(f"/webhooks/deliveries/{w['dlv_full'].id}/")
        row["full_body"] = (
            _status(dfull) == 200 and dfull.json()["result"]["body"] is not None
        )
        row["org_wide_detail"] = _status(c.get(f"{EP}{org_wide.id}/"))
        row["org_wide_log"] = _status(
            c.get("/webhooks/deliveries/", {"endpoint": org_wide.id})
        )
        row["create"] = _status(_create(c))
        row["create_org_wide"] = _status(_create(c, workspace_id=None))
        row["run_targeting"] = _status(_create(c, accepts_run_targeting=True))
        # Test: the permission is checked first, so an unknown type answers 400 only to someone allowed to test.
        row["test"] = _status(
            c.post(f"{EP}{ep.id}/test/", {"event_type": "nope"}, format="json")
        )
        row["redeliver"] = _status(
            c.post(f"/webhooks/deliveries/{w['dlv'].id}/redeliver/")
        )
        row["bulk"] = _status(
            c.post(
                "/webhooks/deliveries/redeliver/",
                {"endpoint": ep.id, "status": "failed", "dry_run": True},
                format="json",
            )
        )
        row["resume"] = _status(
            c.post(f"{EP}{ep.id}/resume/", {"mode": "send"}, format="json")
        )
        row["ws_policy"] = _status(
            c.put(
                f"/webhooks/policy/workspace/{w['a'].id}/",
                {"allowed_hosts": []},
                format="json",
            )
        )
        row["org_policy"] = _status(
            c.put("/webhooks/policy/", {"members_can_manage": mcm}, format="json")
        )
        got[role] = row

    member_write = 201 if mcm else 403
    member_one = 202 if mcm else 403
    expected = {
        "viewer": {
            "list": 403,
            "sees_org_wide_row": False,
            "detail": 403,
            "sees_url": False,
            "log": 403,
            "full_body": False,
            "org_wide_detail": 403,
            "org_wide_log": 403,
            "create": 403,
            "create_org_wide": 403,
            "run_targeting": 403,
            "test": 403,
            "redeliver": 403,
            "bulk": 403,
            "resume": 403,
            "ws_policy": 403,
            "org_policy": 403,
        },
        "member": {
            "list": 200,
            "sees_org_wide_row": True,
            "detail": 200,
            "sees_url": False,
            "log": 200,
            "full_body": False,
            "org_wide_detail": 200,
            "org_wide_log": 403,
            "create": member_write,
            "create_org_wide": 403,
            "run_targeting": 403,
            "test": 400 if mcm else 403,
            "redeliver": member_one,
            "bulk": 403,
            "resume": 403,
            "ws_policy": 403,
            "org_policy": 403,
        },
        "ws_admin": {
            "list": 200,
            "sees_org_wide_row": True,
            "detail": 200,
            "sees_url": True,
            "log": 200,
            "full_body": True,
            "org_wide_detail": 200,
            "org_wide_log": 403,
            "create": 201,
            "create_org_wide": 403,
            "run_targeting": 201,
            "test": 400,
            "redeliver": 202,
            "bulk": 200,
            "resume": 200,
            "ws_policy": 200,
            "org_policy": 403,
        },
        "org_admin": {
            "list": 200,
            "sees_org_wide_row": True,
            "detail": 200,
            "sees_url": True,
            "log": 200,
            "full_body": True,
            "org_wide_detail": 200,
            "org_wide_log": 200,
            "create": 201,
            "create_org_wide": 201,
            "run_targeting": 201,
            "test": 400,
            "redeliver": 202,
            "bulk": 200,
            "resume": 200,
            "ws_policy": 200,
            "org_policy": 200,
        },
    }
    expected["owner"] = expected["org_admin"]
    assert got == expected


@pytest.mark.django_db
def test_workspace_scoping_and_pinned_keys(world):
    w = world
    # A Workspace Member of A cannot open B at all: 404, never 403.
    assert w["c"]["member"].get(f"{EP}{w['in_b'].id}/").status_code == 404
    rows = w["c"]["member"].get(EP).json()["result"]["results"]
    assert "b-thin" not in {r["name"] for r in rows}
    # An owner's key pinned to A is confined to A and manages nothing org-wide.
    pinned = client_for(w["people"]["owner"], w["org"], w["a"], pinned=True)
    assert pinned.get(f"{EP}{w['in_b'].id}/").status_code == 404
    assert pinned.get(f"{EP}{w['ep'].id}/").status_code == 200
    od = pinned.get(f"{EP}{w['org_wide'].id}/")
    assert od.status_code == 200 and od.json()["result"]["can_manage"] is False
    assert _create(pinned, workspace_id=None).status_code == 403
    assert (
        pinned.put(
            "/webhooks/policy/", {"members_can_manage": True}, format="json"
        ).status_code
        == 403
    )


@pytest.mark.django_db
def test_wt19_two_tenants(world):
    w = world
    org_b = Organization.objects.create(name="Tenant B")
    owner_b = person(org_b, None, Level.OWNER, None, "ownerb")
    ws_b = workspace_in(org_b, "B main", owner_b)
    cb = client_for(owner_b, org_b, ws_b)
    same_url = make_endpoint(
        org_b, ws_b, url="https://hooks.acme.ai/omega", name="same-url"
    )
    for path in (
        f"{EP}{w['ep'].id}/",
        f"{EP}{w['org_wide'].id}/",
        f"/webhooks/deliveries/{w['dlv'].id}/",
        f"{EP}{w['ep'].id}/deliveries/",
    ):
        assert cb.get(path).status_code == 404, path
    for path, payload in (
        (f"{EP}{w['ep'].id}/disable/", {}),
        (f"{EP}{w['ep'].id}/rotate-secret/", {}),
        (f"/webhooks/deliveries/{w['dlv'].id}/redeliver/", None),
        (
            "/webhooks/deliveries/redeliver/",
            {"endpoint": w["ep"].id, "status": "failed"},
        ),
    ):
        assert cb.post(path, payload, format="json").status_code == 404, path
    assert cb.delete(f"{EP}{w['ep'].id}/").status_code == 404
    assert (
        cb.get("/webhooks/deliveries/", {"event": w["dlv"].event_id}).json()["result"][
            "results"
        ]
        == []
    )
    assert cb.get("/webhooks/events/").json()["result"]["results"] == []
    # Org A's event never reaches org B's endpoint at the same URL.
    _event(w["org"], w["a"], "e3")
    OrmStore().fan_out(int(time.time() * 1000), 10)
    assert not Delivery.objects.filter(endpoint=same_url).exists()
    assert Endpoint.objects.get(id=w["ep"].id).status == "enabled"
