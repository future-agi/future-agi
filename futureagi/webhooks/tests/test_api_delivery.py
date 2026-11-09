"""WH-2 test delivery and redaction against the real worker: the API writes, the production dispatcher (on a
thread, ``api_support.Worker``) fans out, signs and sends to a local receiver, and the API reads the log back.

Rows: WT-16 (a test of every type, sent by the worker; 200 and 202; a failing receiver; sending off), WT-16b (test
content: reserved ids, names only from the endpoint's own filters) and WT-15 / WT-15b (no secret, header value, URL
token or echoed credential in any API response or stored row; header values arrive byte for byte).
"""

from __future__ import annotations

import json

import pytest
from django.db import connection

from accounts.models.audit_log import AuditLog
from webhooks import api_test
from webhooks.core.payloads import NIL_UUID, TEST_DEPLOYMENT_ID
from webhooks.core.tests.test_conformance_schemas_events import validator
from webhooks.models import Delivery, Endpoint
from webhooks.tests.api_support import EP, Receiver, Worker, make, wait_for

pytestmark = pytest.mark.django_db(transaction=True)
# Canaries are assembled at run time, so no credential-shaped literal sits in the source.
C1 = "sk" + "-live-" + "canary1" * 2
C3 = "whsec" + "_" + "canary3" * 3
C4 = "sk" + "-ant-" + "canary4" * 2
TYPES = (
    "prompt.version.created",
    "prompt.label.changed",
    "deployment.run.completed",
    "deployment.run.failed",
)


def test_wt16_a_test_of_every_type_is_signed_and_sent_by_the_worker(
    env, owner, receiver, organization
):
    ep_id, secret = make(
        owner, receiver.url, subscriptions=[{"event_type": "prompt.*"}]
    )
    with Worker() as wk:
        results = {}
        for t in TYPES:
            r = owner.post(f"{EP}{ep_id}/test/", {"event_type": t}, format="json")
            assert r.status_code == 200, r.content
            results[t] = r.json()["result"]
    assert wk.errors == []
    for t, res in results.items():
        assert (res["status_code"], res["outcome"]) == (200, "success"), (t, res)
        assert res["latency_ms"] is not None and res["event_id"].startswith("evt_test_")
    assert len(receiver.seen) == 4
    assert receiver.verified_by_both(secret) == [True] * 4
    for req in receiver.seen:
        env_ = json.loads(req["body"])
        h = req["headers"]
        assert env_["test"] is True and h["x-futureagi-test"] == "1"
        assert "x-futureagi-sequence" not in h and h["webhook-id"] == env_["id"]
        assert h["x-futureagi-event"] == env_["type"]
        errors = list(
            validator(f"urn:futureagi:webhooks:v1:{env_['type']}").iter_errors(env_)
        )
        assert errors == [], [e.message for e in errors]
        data = env_["data"]
        if env_["type"].startswith("prompt."):
            assert data["prompt"]["id"] == NIL_UUID and data["prompt"][
                "name"
            ].startswith("[Test] ")
        else:
            assert data["deployment"]["id"] == TEST_DEPLOYMENT_ID and data["run"][
                "id"
            ].endswith("0" * 26)
    for d in Delivery.objects.filter(endpoint_id=ep_id):
        assert (d.is_test, d.seq, d.attempts_count, d.status) == (
            True,
            None,
            1,
            "succeeded",
        )
    detail = owner.get(f"/webhooks/deliveries/{d.id}/").json()["result"]
    assert detail["attempts"][0]["trigger"] == "test" and detail["is_test"] is True
    sent = AuditLog.objects.filter(scope="webhooks", action="webhook.test.sent")
    assert sorted(e.metadata["status_code"] for e in sent) == [200] * 4
    # 5 per person per minute (section 7.8): the 5th is allowed, the 6th is not.
    with Worker():
        assert (
            owner.post(
                f"{EP}{ep_id}/test/", {"event_type": TYPES[0]}, format="json"
            ).status_code
            == 200
        )
        r = owner.post(f"{EP}{ep_id}/test/", {"event_type": TYPES[0]}, format="json")
    assert (r.status_code, r.json()["code"]) == (429, "WEBHOOK_TEST_RATE_LIMITED")


def test_wt16_202_then_the_result_and_a_failing_receiver(
    env, owner, organization, monkeypatch
):
    monkeypatch.setattr(api_test, "TEST_WAIT_S", 1.0)
    rcv = Receiver(answers=((500, b"receiver down"),))
    try:
        ep_id, _secret = make(owner, rcv.url)
        # No worker running: the API answers 202 with the event id; nothing was sent by the web tier.
        r = owner.post(
            f"{EP}{ep_id}/test/", {"event_type": "prompt.label.changed"}, format="json"
        )
        assert r.status_code == 202, r.content
        ev = r.json()["result"]["event_id"]
        assert rcv.seen == []
        with Worker():
            row = wait_for(
                lambda: owner.get("/webhooks/deliveries/", {"event": ev}).json()[
                    "result"
                ]["results"],
            )
            assert wait_for(
                lambda: Delivery.objects.filter(event_id=ev, status="failed").exists()
            )
            # The 500 is shown, the test is not retried, and the endpoint's health is untouched.
            monkeypatch.setattr(api_test, "TEST_WAIT_S", 20.0)
            r2 = owner.post(
                f"{EP}{ep_id}/test/",
                {"event_type": "prompt.version.created"},
                format="json",
            )
        assert row[0]["is_test"] is True
        res = r2.json()["result"]
        assert (r2.status_code, res["status_code"], res["outcome"]) == (
            200,
            500,
            "http_error",
        )
        assert res["response_excerpt"] == "receiver down"
        d = Delivery.objects.get(id=res["delivery_id"])
        assert (d.status, d.final_outcome, d.attempts_count) == (
            "failed",
            "test_not_retried",
            1,
        )
        assert len(rcv.seen) == 2
        assert Endpoint.objects.get(id=ep_id).status == "enabled"
    finally:
        rcv.close()


def test_wt16_sending_off_refuses_a_test(env, owner, receiver, monkeypatch):
    ep_id, _ = make(owner, receiver.url)
    monkeypatch.setenv("FI_WEBHOOKS_ENABLED", "false")
    r = owner.post(f"{EP}{ep_id}/test/", {"event_type": TYPES[0]}, format="json")
    assert (r.status_code, r.json()["code"]) == (503, "WEBHOOK_SENDING_OFF")


def test_wt16b_test_content_uses_only_the_endpoints_own_filters(
    env, owner, receiver, organization, workspace, user
):
    from model_hub.models.run_prompt import PromptTemplate

    mine = PromptTemplate.no_workspace_objects.create(
        name="billing-bot",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    PromptTemplate.no_workspace_objects.create(
        name="secret-project",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    subs = [
        {
            "event_type": "prompt.label.changed",
            "filters": {"prompt_ids": [str(mine.id)], "label_names": ["Staging"]},
        }
    ]
    ep_id, _ = make(owner, receiver.url, subscriptions=subs)
    with Worker():
        r = owner.post(
            f"{EP}{ep_id}/test/", {"event_type": "prompt.label.changed"}, format="json"
        )
    assert r.status_code == 200, r.content
    body = receiver.seen[0]["body"].decode()
    data = json.loads(body)["data"]
    assert data["prompt"] == {"id": NIL_UUID, "name": "[Test] billing-bot"}
    assert data["label"]["name"] == "[Test] Staging" and data["label"]["id"] == NIL_UUID
    assert (
        data["to_version"]["id"] == NIL_UUID and data["from_version"]["id"] == NIL_UUID
    )
    assert "secret-project" not in body and str(mine.id) not in body
    # The reserved ids fetch nothing. Dev's prompt-templates route answers a missing id with 400, not 404.
    got = owner.get(f"/model-hub/prompt-templates/{NIL_UUID}/")
    assert got.status_code in (400, 404), got.content


def test_wt15_no_secret_header_value_token_or_echo_in_any_response_or_row(
    env, owner, organization
):
    rcv = Receiver(answers=((200, f"echo {C3} {C4} <b>x</b>".encode()),))
    try:
        ep_id, secret = make(
            owner,
            rcv.url + "?token=canary2canary2",
            headers={
                "Authorization": "Bearer " + C1,
                "X-Lit": "${INTEGRATION_ENCRYPTION_KEY}",
            },
        )
        with Worker():
            r = owner.post(
                f"{EP}{ep_id}/test/",
                {"event_type": "prompt.label.changed"},
                format="json",
            )
        assert r.status_code == 200, r.content
        # WT-15b: values are literal, byte for byte.
        h = rcv.seen[0]["headers"]
        assert h["authorization"] == "Bearer " + C1
        assert h["x-lit"] == "${INTEGRATION_ENCRYPTION_KEY}"
        assert rcv.seen[0]["path"] == "/hook?token=canary2canary2"
        d_id = r.json()["result"]["delivery_id"]
        outputs = [
            r.json(),
            owner.get(f"{EP}{ep_id}/").json(),
            owner.get(EP).json(),
            owner.get("/webhooks/deliveries/", {"include_body": True}).json(),
            owner.get(f"/webhooks/deliveries/{d_id}/").json(),
            owner.get("/webhooks/events/").json(),
        ]
        rows = []
        with connection.cursor() as cur:
            for table in (
                "webhooks_endpoint",
                "webhooks_secret",
                "webhooks_subscription",
                "webhooks_event",
                "webhooks_payload",
                "webhooks_delivery",
                "webhooks_attempt",
                "webhooks_api_action",
                "accounts_audit_log",
            ):
                cur.execute(f"SELECT row_to_json(wh_row)::text FROM {table} wh_row")  # noqa: S608 - fixed table names
                rows += [x[0] for x in cur.fetchall()]
        blob = json.dumps(outputs) + "\n".join(rows)
        for needle in (
            "canary1canary1",
            "canary2canary2",
            "canary3canary3",
            "canary4canary4",
            secret,
        ):
            assert needle not in blob, needle
        excerpt = owner.get(f"/webhooks/deliveries/{d_id}/").json()["result"][
            "attempts"
        ][0]["response_body_excerpt"]
        assert (
            "<b>x</b>" in excerpt
        )  # stored and returned as text; the clients render it inert
    finally:
        rcv.close()


def test_wt16_a_slow_receiver_gets_202_and_then_the_result(env, owner, monkeypatch):
    """WT-16's slow receiver, with the wait shortened from 20 s to 2 s (the receiver takes 4 s)."""
    monkeypatch.setattr(api_test, "TEST_WAIT_S", 2.0)
    rcv = Receiver(answers=((200, b"slow ok", 4.0),))
    try:
        ep_id, _ = make(owner, rcv.url)
        with Worker() as wk:
            r = owner.post(
                f"{EP}{ep_id}/test/",
                {"event_type": "prompt.version.created"},
                format="json",
            )
            assert r.status_code == 202, r.content
            d_id = r.json()["result"]["delivery_id"]
            assert d_id  # the worker had already taken it
            assert wait_for(
                lambda: (
                    owner.get(f"/webhooks/deliveries/{d_id}/").json()["result"][
                        "status"
                    ]
                    == "succeeded"
                ),
                20,
            )
        assert wk.errors == []
        a = owner.get(f"/webhooks/deliveries/{d_id}/").json()["result"]["attempts"]
        assert (
            len(a) == 1
            and a[0]["response_status"] == 200
            and a[0]["duration_ms"] >= 4000
        )
        assert a[0]["response_body_excerpt"] == "slow ok" and len(rcv.seen) == 1
    finally:
        rcv.close()
