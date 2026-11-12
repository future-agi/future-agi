"""WH-2 delivery lifecycle against the real worker (``api_support.Worker``): redelivery, rotation, resume, policy
and the recovery reads.

Rows: WT-17 (redeliver one and a bulk of 25), WT-04 (rotation overlap, stop it now, rotate twice), WT-11 (410, then
resume with Send and with Skip), WT-26 (policy allowlist) and the WT-14 recovery reads (``seq_from = seq_to`` and the
events list).
"""

from __future__ import annotations

import json
import time

import pytest

from accounts.models.audit_log import AuditLog
from tfc.constants.levels import Level
from webhooks.core.signing import verify
from webhooks.models import Delivery, Endpoint, EndpointState, Payload
from webhooks.tests.api_support import (
    EP,
    Receiver,
    Worker,
    client_for,
    make,
    person,
    pub,
    statuses,
    wait_for,
)

pytestmark = pytest.mark.django_db(transaction=True)


def paused_count(ep_id: str) -> int:
    return EndpointState.objects.get(endpoint_id=ep_id).paused_count


def test_wt17_redeliver_one_then_a_bulk_of_25(
    env, owner, organization, workspace, user
):
    rcv = Receiver(answers=[(413, b"too large")] * 26 + [(200, b"ok")])
    try:
        ep_id, secret = make(
            owner, rcv.url, subscriptions=[{"event_type": "prompt.label.changed"}]
        )
        for i in range(26):
            pub(organization, workspace, f"k{i}")
        with Worker() as wk:
            assert wait_for(lambda: statuses(ep_id).count("failed") == 26, 30)
            first = Delivery.objects.filter(endpoint_id=ep_id).order_by("seq").first()
            r = owner.post(f"/webhooks/deliveries/{first.id}/redeliver/")
            assert r.status_code == 202, r.content
            assert r.json()["result"] == {
                "delivery_id": first.id,
                "queued": True,
                "attempt": 2,
            }
            assert wait_for(
                lambda: Delivery.objects.get(id=first.id).status == "succeeded"
            )
            q = {"endpoint": ep_id, "status": "failed"}
            dry = owner.post(
                "/webhooks/deliveries/redeliver/", {**q, "dry_run": True}, format="json"
            )
            assert dry.json()["result"] == {
                "count": 25,
                "queued": 0,
                "expired": 0,
                "dry_run": True,
            }
            bulk = owner.post("/webhooks/deliveries/redeliver/", q, format="json")
            assert bulk.json()["result"] == {
                "count": 25,
                "queued": 25,
                "expired": 0,
                "dry_run": False,
            }
            assert wait_for(lambda: statuses(ep_id).count("succeeded") == 26, 30)
        assert wk.errors == []
        # Same event id, delivery id and sequence; a fresh t; signed with the current secret.
        mine = [s for s in rcv.seen if s["headers"]["x-futureagi-delivery"] == first.id]
        assert len(mine) == 2
        a, b = mine
        for k in ("webhook-id", "x-futureagi-delivery", "x-futureagi-sequence"):
            assert a["headers"][k] == b["headers"][k]
        assert (
            a["headers"]["x-futureagi-attempt"],
            b["headers"]["x-futureagi-attempt"],
        ) == ("1", "2")
        assert int(b["headers"]["webhook-timestamp"]) >= int(
            a["headers"]["webhook-timestamp"]
        )
        assert verify(secret, b["headers"], b["body"], int(time.time())) == "ok"
        detail = owner.get(f"/webhooks/deliveries/{first.id}/").json()["result"]
        assert [(x["n"], x["trigger"]) for x in detail["attempts"]] == [
            (1, "auto"),
            (2, "redeliver"),
        ]
        assert detail["attempts"][1]["actor_id"] == str(user.id)
        assert detail["attempts"][0]["response_status"] == 413
        entries = AuditLog.objects.filter(
            scope="webhooks", action="webhook.delivery.redelivered"
        )
        bulk_entry = [e for e in entries if e.metadata.get("bulk")]
        assert len(entries) == 2 and bulk_entry[0].metadata["count"] == 25
        assert bulk_entry[0].metadata["filter"] == {
            "endpoint": ep_id,
            "status": "failed",
        }
        # A Member without members_can_manage cannot redeliver (403).
        member = person(
            organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "m"
        )
        r = client_for(member, organization, workspace).post(
            f"/webhooks/deliveries/{first.id}/redeliver/"
        )
        assert r.status_code == 403
        # A body no longer kept (30 days, section 7.5): 409.
        Payload.objects.filter(id=first.payload_id).update(body=None)
        r = owner.post(f"/webhooks/deliveries/{first.id}/redeliver/")
        assert (r.status_code, r.json()["code"]) == (409, "WEBHOOK_BODY_EXPIRED")
        # A turned-off endpoint: 409, "Turn this webhook on first."
        owner.post(f"{EP}{ep_id}/disable/", {"reason": "maintenance"}, format="json")
        second = Delivery.objects.filter(endpoint_id=ep_id).exclude(id=first.id).first()
        r = owner.post(f"/webhooks/deliveries/{second.id}/redeliver/")
        assert (r.status_code, r.json()["code"]) == (409, "WEBHOOK_ENDPOINT_DISABLED")
    finally:
        rcv.close()


def test_wt04_rotation_overlap_stop_now_and_rotate_twice(env, owner, receiver):
    ep_id, a = make(owner, receiver.url)

    def test_once():
        r = owner.post(
            f"{EP}{ep_id}/test/", {"event_type": "prompt.label.changed"}, format="json"
        )
        assert r.status_code == 200 and r.json()["result"]["outcome"] == "success", (
            r.content
        )
        last = receiver.seen[-1]
        return last, last["headers"]["webhook-signature"].split(" ")

    with Worker():
        rot = owner.post(
            f"{EP}{ep_id}/rotate-secret/", {"overlap_seconds": 3}, format="json"
        ).json()["result"]
        b = rot["secret"]
        assert rot["previous"]["tail"] == a[-4:] and rot["revoked_tails"] == []
        req, sigs = test_once()
        now = int(time.time())
        assert len(sigs) == 2
        assert (
            verify(a, req["headers"], req["body"], now)
            == "ok"
            == verify(b, req["headers"], req["body"], now)
        )
        time.sleep(3.5)
        req, sigs = test_once()
        assert len(sigs) == 1
        assert (
            verify(a, req["headers"], req["body"], int(time.time()))
            == "no_matching_signature"
        )
        assert verify(b, req["headers"], req["body"], int(time.time())) == "ok"
        # Rotating twice revokes the first secret at once (at most two live secrets).
        c = owner.post(f"{EP}{ep_id}/rotate-secret/", {}, format="json").json()[
            "result"
        ]["secret"]
        rot2 = owner.post(f"{EP}{ep_id}/rotate-secret/", {}, format="json").json()[
            "result"
        ]
        d = rot2["secret"]
        assert rot2["revoked_tails"] == [b[-4:]] and rot2["previous"]["tail"] == c[-4:]
        req, sigs = test_once()
        assert (
            len(sigs) == 2
            and verify(b, req["headers"], req["body"], int(time.time())) != "ok"
        )
        # "Stop it now" ends the overlap at once.
        stop = owner.post(f"{EP}{ep_id}/revoke-previous-secret/").json()["result"]
        assert stop == {"revoked_tails": [c[-4:]]}
        req, sigs = test_once()
    assert len(sigs) == 1
    assert (
        verify(c, req["headers"], req["body"], int(time.time()))
        == "no_matching_signature"
    )
    assert verify(d, req["headers"], req["body"], int(time.time())) == "ok"
    tails = [s["tail"] for s in owner.get(f"{EP}{ep_id}/").json()["result"]["secrets"]]
    assert tails == [d[-4:]]
    actions = list(
        AuditLog.objects.filter(scope="webhooks").values_list("action", flat=True)
    )
    assert (
        actions.count("webhook.secret.rotated") == 3
        and actions.count("webhook.secret.previous_revoked") == 1
    )


def test_wt11_410_then_resume_with_send_and_with_skip(
    env, owner, organization, workspace
):
    rcv = Receiver(answers=[(410, b"gone")] + [(200, b"ok")])
    try:
        ep_id, _ = make(
            owner, rcv.url, subscriptions=[{"event_type": "prompt.label.changed"}]
        )
        with Worker():
            pub(organization, workspace, "g0")
            assert wait_for(
                lambda: Endpoint.objects.get(id=ep_id).status == "disabled_auto"
            )
            for i in range(3):
                pub(organization, workspace, f"g{i + 1}")
            assert wait_for(lambda: statuses(ep_id).count("paused") == 4)
            seqs = sorted(
                Delivery.objects.filter(endpoint_id=ep_id).values_list("seq", flat=True)
            )
            assert seqs == [1, 2, 3, 4]
            ep = owner.get(f"{EP}{ep_id}/").json()["result"]
            assert (ep["status"], ep["disabled_reason"], ep["backlog"]["paused"]) == (
                "disabled_auto",
                "gone_410",
                4,
            )
            assert paused_count(ep_id) == 4  # the counter the 10,000 cap reads (WH-1b)
            r = owner.post(f"{EP}{ep_id}/resume/", {"mode": "send"}, format="json")
            assert (r.status_code, r.json()["code"]) == (
                409,
                "WEBHOOK_ENDPOINT_DISABLED",
            )
            assert (
                owner.post(f"{EP}{ep_id}/enable/", {}, format="json").status_code == 200
            )
            res = owner.post(
                f"{EP}{ep_id}/resume/", {"mode": "send"}, format="json"
            ).json()["result"]
            assert res == {
                "mode": "send",
                "sent": 4,
                "skipped": 0,
                "past_max_age": 0,
                "backlog_capped": 0,
            }
            assert paused_count(ep_id) == 0  # resume lowers it by as many as it moved
            assert wait_for(lambda: statuses(ep_id).count("succeeded") == 4, 20)
            # Skip: turned off by hand, 2 events wait, then skipped; none is delivered.
            owner.post(f"{EP}{ep_id}/disable/", {}, format="json")
            for i in range(2):
                pub(organization, workspace, f"s{i}")
            assert wait_for(lambda: statuses(ep_id).count("paused") == 2)
            assert paused_count(ep_id) == 2
            owner.post(f"{EP}{ep_id}/enable/", {}, format="json")
            res = owner.post(
                f"{EP}{ep_id}/resume/", {"mode": "skip"}, format="json"
            ).json()["result"]
            assert res["skipped"] == 2 and res["sent"] == 0
            assert paused_count(ep_id) == 0
            time.sleep(1.0)
        assert len(rcv.seen) == 5
        assert sorted(statuses(ep_id)) == ["skipped"] * 2 + ["succeeded"] * 4
        modes = [
            e.metadata["mode"]
            for e in AuditLog.objects.filter(action="webhook.endpoint.resumed")
        ]
        assert sorted(modes) == ["send", "skip"]
    finally:
        rcv.close()


def test_wt26_policy_allowlist_blocks_pauses_and_resumes(
    env, owner, receiver, organization, workspace
):
    ep_id, _ = make(
        owner, receiver.url, subscriptions=[{"event_type": "prompt.label.changed"}]
    )
    r = owner.put("/webhooks/policy/", {"allowed_hosts": ["*.acme.ai"]}, format="json")
    assert r.status_code == 200 and r.json()["result"]["blocked_endpoint_ids"] == []
    r = owner.post(
        EP,
        {
            "name": "x",
            "url": "https://hooks.other.ai/x",
            "subscriptions": [{"event_type": "prompt.*"}],
        },
        format="json",
    )
    assert (r.status_code, r.json()["code"]) == (400, "WEBHOOK_HOST_NOT_ALLOWED")
    assert "*.acme.ai" in r.json()["message"]
    r = owner.put(
        f"/webhooks/policy/workspace/{workspace.id}/",
        {"allowed_hosts": ["*.zzz.ai"]},
        format="json",
    )
    assert (r.status_code, r.json()["code"]) == (400, "WEBHOOK_POLICY_INVALID")
    # Tightening turns the existing endpoint blocked_by_policy and pauses its deliveries.
    r = owner.put("/webhooks/policy/", {"allowed_hosts": ["*.other.ai"]}, format="json")
    assert r.json()["result"]["blocked_endpoint_ids"] == [ep_id]
    assert Endpoint.objects.get(id=ep_id).status == "blocked_by_policy"
    with Worker():
        pub(organization, workspace, "p1")
        assert wait_for(lambda: statuses(ep_id) == ["paused"])
        r = owner.post(f"{EP}{ep_id}/enable/", {}, format="json")
        assert (r.status_code, r.json()["code"]) == (400, "WEBHOOK_HOST_NOT_ALLOWED")
        # Loosening, then turning it on and resuming, delivers what waited.
        owner.put("/webhooks/policy/", {"allowed_hosts": ["*.acme.ai"]}, format="json")
        assert owner.post(f"{EP}{ep_id}/enable/", {}, format="json").status_code == 200
        owner.post(f"{EP}{ep_id}/resume/", {"mode": "send"}, format="json")
        assert wait_for(lambda: statuses(ep_id) == ["succeeded"])
    assert len(receiver.seen) == 1
    assert (
        AuditLog.objects.filter(action="webhook.endpoint.blocked_by_policy").count()
        == 1
    )
    assert AuditLog.objects.filter(action="webhook.policy.updated").count() == 3


def test_wt14_sequence_recovery_and_the_events_list(
    env, owner, receiver, organization, workspace
):
    from webhooks.store import OrmStore

    ep_id, _ = make(
        owner, receiver.url, subscriptions=[{"event_type": "prompt.label.changed"}]
    )
    for i in range(3):
        pub(organization, workspace, f"r{i}", prompt_id="p1")
    OrmStore().fan_out(int(time.time() * 1000), 10)
    got = owner.get(f"{EP}{ep_id}/deliveries/", {"seq_from": 2, "seq_to": 2}).json()[
        "result"
    ]["results"]
    assert [d["seq"] for d in got] == [2] and json.loads(got[0]["body"])[
        "type"
    ] == "prompt.label.changed"
    page = owner.get(
        "/webhooks/events/", {"type": "prompt.label.changed", "limit": 2}
    ).json()["result"]
    assert len(page["results"]) == 2 and page["next_cursor"]
    rest = owner.get("/webhooks/events/", {"cursor": page["next_cursor"]}).json()[
        "result"
    ]
    assert len(rest["results"]) == 1 and rest["next_cursor"] is None
    assert (
        len(
            owner.get("/webhooks/events/", {"subject": "prompt:p1"}).json()["result"][
                "results"
            ]
        )
        == 3
    )
    assert owner.get("/webhooks/events/", {"subject": "nope"}).status_code == 400
