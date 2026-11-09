"""WH-2 endpoint management over FA's real authentication, continued (DESIGN.md sections 4, 7.8, 8.2, 9.2, 10 and
14): limits and rates, the full-payload policy gate, update and delete (WT-27: the secret rows are gone), event
types, preview and egress, no secret in a log line, and org-wide rows that keep a null workspace.
"""

from __future__ import annotations

import json

import pytest

from accounts.models.audit_log import AuditLog
from webhooks.models import ApiAction, Delivery, Endpoint, Policy, Secret
from webhooks.tests.api_support import client_for
from webhooks.tests.factories import use_test_keys

pytestmark = pytest.mark.django_db
URL = "/webhooks/endpoints/"
# Canaries are assembled at run time, so no credential-shaped literal sits in the source.
SK = "sk" + "-live-"


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.fixture
def owner(user, organization, workspace):
    return client_for(user, organization, workspace)


def body(**kw):
    out = {
        "name": "release hook",
        "url": "https://hooks.acme.ai/omega?token=canary2canary2",
        "subscriptions": [{"event_type": "prompt.*"}],
    }
    out.update(kw)
    return out


def code_of(r):
    return r.json().get("code")


def test_limits_and_the_write_rate(owner, keys, organization, workspace, user):
    Policy.objects.create(organization=organization, max_endpoints_per_workspace=2)
    for i in range(2):
        assert owner.post(URL, body(name=f"h{i}"), format="json").status_code == 201
    r = owner.post(URL, body(name="h3"), format="json")
    assert (r.status_code, code_of(r)) == (400, "WEBHOOK_ENDPOINT_LIMIT")
    ApiAction.objects.bulk_create(
        [
            ApiAction(organization=organization, kind="write", actor=user)
            for _ in range(120)
        ]
    )
    r = owner.patch(
        f"{URL}{Endpoint.objects.first().id}/", {"description": "x"}, format="json"
    )
    assert (r.status_code, code_of(r)) == (429, "WEBHOOK_RATE_LIMITED")
    assert r["Retry-After"] == "60"


def test_full_payload_needs_the_org_policy(owner, keys, organization):
    r = owner.post(URL, body(payload_mode="full"), format="json")
    assert (r.status_code, code_of(r)) == (400, "WEBHOOK_PAYLOAD_FULL_NOT_ALLOWED")
    Policy.objects.create(organization=organization, full_payload_allowed=True)
    r = owner.post(
        URL, body(payload_mode="full", accepts_run_targeting=True), format="json"
    )
    assert r.status_code == 201, r.content
    actions = set(
        AuditLog.objects.filter(scope="webhooks").values_list("action", flat=True)
    )
    assert {
        "webhook.endpoint.payload_full_enabled",
        "webhook.endpoint.run_targeting_enabled",
    } <= actions


def test_update_revalidates_and_audits_only_display_forms(owner, keys, organization):
    ep_id = owner.post(URL, body(), format="json").json()["result"]["endpoint"]["id"]
    r = owner.patch(f"{URL}{ep_id}/", {"url": "https://10.0.0.5/x"}, format="json")
    assert (r.status_code, code_of(r)) == (400, "WEBHOOK_URL_UNSAFE_ADDRESS")
    r = owner.patch(
        f"{URL}{ep_id}/",
        {
            "url": "https://other.acme.ai/new?sig=canary5canary5",
            "headers": {"X-Team": "canary6canary6"},
            "timeout_ms": 9000,
        },
        format="json",
    )
    assert r.status_code == 200, r.content
    res = r.json()["result"]
    assert (res["url"], res["header_names"], res["timeout_ms"]) == (
        "https://other.acme.ai/new?sig=…",
        ["X-Team"],
        9000,
    )
    upd = AuditLog.objects.get(scope="webhooks", action="webhook.endpoint.updated")
    assert set(upd.changes) >= {
        "url_display",
        "url_sha256",
        "header_names",
        "timeout_ms",
    }
    assert "canary5" not in json.dumps(upd.changes) and "canary6" not in json.dumps(
        upd.changes
    )
    r = owner.patch(f"{URL}{ep_id}/", {"timeout_ms": 100}, format="json")
    assert r.status_code == 400


def test_delete_shreds_secrets_and_cancels_waiting_deliveries(
    owner, keys, organization, workspace
):
    from django.db import transaction

    from webhooks.publish import publish
    from webhooks.store import OrmStore
    from webhooks.tests.factories import T0, label_event_data

    ep_id = owner.post(URL, body(), format="json").json()["result"]["endpoint"]["id"]
    with transaction.atomic():
        publish(
            type="prompt.label.changed",
            organization_id=organization.id,
            workspace_id=workspace.id,
            data=label_event_data(),
            source_key="d1",
            source_hash="h",
            producer="test",
            occurred_at=T0,
        )
    import time

    OrmStore().fan_out(int(time.time() * 1000), 10)
    assert Delivery.objects.get(endpoint_id=ep_id).status == "pending"
    r = owner.delete(f"{URL}{ep_id}/")
    assert r.status_code == 200 and r.json()["result"]["cancelled_deliveries"] == 1
    ep = Endpoint.objects.get(id=ep_id)
    assert ep.deleted and ep.url_encrypted is None and ep.headers_encrypted is None
    assert not Secret.objects.filter(endpoint_id=ep_id).exists()
    d = Delivery.objects.get(endpoint_id=ep_id)
    assert (d.status, d.final_outcome) == ("cancelled", "endpoint_deleted")
    assert owner.get(f"{URL}{ep_id}/").status_code == 404
    assert (
        AuditLog.objects.filter(
            scope="webhooks", action="webhook.endpoint.deleted"
        ).count()
        == 1
    )
    # The name is free again among live endpoints.
    assert owner.post(URL, body(), format="json").status_code == 201


def test_event_types_preview_and_egress(owner, keys):
    r = owner.get("/webhooks/event-types/")
    assert r.status_code == 200
    types = {t["type"]: t for t in r.json()["result"]["results"]}
    assert set(types) == {
        "prompt.version.created",
        "prompt.label.changed",
        "deployment.run.completed",
        "deployment.run.failed",
    }
    assert types["prompt.label.changed"]["schema"]["$schema"]
    assert "actions" in types["prompt.label.changed"]["filters"]
    # Every $ref resolves inside the response: a client validates a body with the catalog alone.
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    res = r.json()["result"]
    docs = [t["schema"] for t in res["results"]] + list(res["shared_schemas"].values())
    registry = Registry().with_resources(
        (d["$id"], Resource.from_contents(d, default_specification=DRAFT202012))
        for d in docs
    )
    sample = owner.post(
        "/webhooks/preview/", {"event_type": "deployment.run.failed"}, format="json"
    ).json()["result"]["body"]
    errors = list(
        Draft202012Validator(
            types["deployment.run.failed"]["schema"], registry=registry
        ).iter_errors(json.loads(sample))
    )
    assert errors == [], [e.message for e in errors]
    r = owner.post(
        "/webhooks/preview/",
        {
            "event_type": "prompt.label.changed",
            "headers": {"Authorization": "Bearer " + SK + "canary7"},
        },
        format="json",
    )
    assert r.status_code == 200, r.content
    res = r.json()["result"]
    hdrs = dict(res["headers"])
    assert (
        hdrs["webhook-signature"] == "v1,<signature>"
        and hdrs["Authorization"] == "••••ary7"
    )
    assert "X-FutureAGI-Sequence" in hdrs and "X-FutureAGI-Test" not in hdrs
    env = json.loads(res["body"])
    assert (
        env["type"] == "prompt.label.changed"
        and "test" not in env
        and env["data"]["action"] == "moved"
    )
    assert "canary7" not in json.dumps(res)
    assert (
        owner.post(
            "/webhooks/preview/", {"event_type": "nope"}, format="json"
        ).status_code
        == 400
    )
    r = owner.get("/webhooks/egress-ips/")
    assert r.status_code == 200 and r.json()["result"] == {
        "hosted": False,
        "regions": [],
    }


def test_org_test_rate_and_workspace_bulk_rate(
    owner, keys, organization, workspace, user
):
    from webhooks.tests.factories import make_endpoint

    ep = make_endpoint(organization, workspace)
    ApiAction.objects.bulk_create(
        [ApiAction(organization=organization, kind="test") for _ in range(30)]
    )
    r = owner.post(
        f"{URL}{ep.id}/test/", {"event_type": "prompt.label.changed"}, format="json"
    )
    assert (r.status_code, code_of(r)) == (429, "WEBHOOK_TEST_RATE_LIMITED")
    ApiAction.objects.bulk_create(
        [
            ApiAction(
                organization=organization, workspace=workspace, kind="bulk_redeliver"
            )
            for _ in range(10)
        ]
    )
    q = {"endpoint": ep.id, "status": "failed"}
    dry = owner.post(
        "/webhooks/deliveries/redeliver/", {**q, "dry_run": True}, format="json"
    )
    assert dry.status_code == 200  # a dry run only counts
    r = owner.post("/webhooks/deliveries/redeliver/", q, format="json")
    assert (r.status_code, code_of(r)) == (429, "WEBHOOK_RATE_LIMITED")


def test_no_secret_reaches_a_log_line(owner, keys, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    res = owner.post(URL, body(), format="json").json()["result"]
    ep_id, first = res["endpoint"]["id"], res["secret"]
    second = owner.post(f"{URL}{ep_id}/rotate-secret/", {}, format="json").json()[
        "result"
    ]["secret"]
    owner.post(f"{URL}{ep_id}/revoke-previous-secret/")
    owner.get(f"{URL}{ep_id}/")
    text = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for needle in (first, second, "canary2canary2"):
        assert needle not in text


def test_org_wide_rows_keep_a_null_workspace_under_a_request_context(
    owner, keys, organization, workspace
):
    """FA's pre_save/post_save signals (tfc/utils/signals.py) fill a null ``workspace`` from the thread-local context
    that authentication sets on every request. For webhooks a null workspace means org-wide, so the models opt out."""
    from tfc.middleware.workspace_context import set_workspace_context
    from webhooks.tests.factories import make_endpoint

    set_workspace_context(workspace=workspace, organization=organization)
    ep = make_endpoint(organization, None, name="org-wide")
    pol = Policy.objects.create(organization=organization)
    act = ApiAction.objects.create(organization=organization, kind="write")
    for row in (ep, pol, act):
        row.refresh_from_db()
        assert row.workspace_id is None, type(row).__name__
    # And through the API, where authentication sets the context.
    r = owner.post(URL, body(name="ow2", workspace_id=None), format="json")
    assert r.status_code == 201 and r.json()["result"]["endpoint"]["org_wide"] is True
    assert (
        Endpoint.objects.get(id=r.json()["result"]["endpoint"]["id"]).workspace_id
        is None
    )
    assert (
        owner.put(
            "/webhooks/policy/", {"members_can_manage": True}, format="json"
        ).status_code
        == 200
    )
    assert (
        Policy.objects.filter(organization=organization, workspace__isnull=True).count()
        == 1
    )


@pytest.mark.parametrize("verified", [True, False])
def test_system_key_refused_and_personal_owner_can_manage(
    keys, organization, workspace, user, verified
):
    """Retired keys cannot manage webhooks; a live personal Owner key still can.

    Only a matching stored hash permits retirement guidance in the refusal.
    """
    from accounts.models.user import OrgApiKey
    from tfc.middleware.workspace_context import clear_workspace_context
    from webhooks.tests.api_support import KeyClient

    clear_workspace_context()
    key = OrgApiKey.no_workspace_objects.create(
        organization=organization, type="system"
    )
    c = KeyClient()
    c.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key if verified else "wrong-synthetic-secret",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    for r in (
        c.post(URL, body(), format="json"),
        c.put("/webhooks/policy/", {"members_can_manage": True}, format="json"),
    ):
        assert r.status_code == 401, r.content
        if verified:
            assert code_of(r) == "api_key_retired"
            assert r.json()["create_url"] == "/dashboard/keys"
        else:
            assert code_of(r) != "api_key_retired"
            assert "create_url" not in r.json()
    assert not Endpoint.objects.filter(organization=organization).exists()
    assert not Policy.objects.filter(organization=organization).exists()

    c = client_for(user, organization, workspace)
    r = c.post(URL, body(), format="json")
    assert r.status_code == 201, r.content
    assert (
        Endpoint.objects.get(id=r.json()["result"]["endpoint"]["id"]).owner_id
        == user.id
    )
    assert (
        c.put(
            "/webhooks/policy/", {"members_can_manage": True}, format="json"
        ).status_code
        == 200
    )


def test_the_auto_disabled_audit_hook(keys, organization, workspace):
    from webhooks.audit import record_auto_disabled, target_uuid
    from webhooks.tests.factories import make_endpoint

    ep = make_endpoint(organization, workspace)
    record_auto_disabled(ep.id, reason="gone_410", details={"last_status": 410})
    e = AuditLog.objects.get(scope="webhooks", action="webhook.endpoint.auto_disabled")
    assert e.target_id == target_uuid(ep.id) and e.actor_id is None
    assert e.metadata == {
        "target": ep.id,
        "reason": "gone_410",
        "actor": "system",
        "last_status": 410,
    }


def test_full_payload_cells_of_the_rbac_table(keys, organization, workspace):
    """Section 9.1, ``payload_mode=full``: a Workspace Admin may when the org allows it; a Member may not, even with
    members_can_manage on (a thin endpoint is fine for them)."""
    from tfc.constants.levels import Level
    from webhooks.tests.api_support import person

    Policy.objects.create(
        organization=organization, full_payload_allowed=True, members_can_manage=True
    )
    lead = person(organization, workspace, Level.MEMBER, Level.WORKSPACE_ADMIN, "lead")
    member = person(
        organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "mem"
    )
    cl, cm = (
        client_for(lead, organization, workspace),
        client_for(member, organization, workspace),
    )
    assert (
        cl.post(URL, body(name="a", payload_mode="full"), format="json").status_code
        == 201
    )
    r = cm.post(URL, body(name="b", payload_mode="full"), format="json")
    assert (r.status_code, code_of(r)) == (400, "WEBHOOK_PAYLOAD_FULL_NOT_ALLOWED")
    assert cm.post(URL, body(name="c"), format="json").status_code == 201
