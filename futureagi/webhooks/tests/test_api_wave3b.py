"""The integrate step's API findings from WH-3, over FA's real authentication and the real worker:

- F2: the two body-less POSTs (redeliver one, stop the previous secret) take an explicit empty object: ``{}`` and no
  body pass, any field is refused.
- F5: ``Redeliver failed (N)`` acts on exactly the log's filtered view: ``subject``, ``seq_from`` and ``seq_to`` as
  well as the event type and the time window.
- F3: ``GET /webhooks/endpoints/{id}/activity/``, the endpoint page's Activity card: this endpoint's audit entries,
  newest first and cursor-paged; the details (URL, header names, secret tails) only for those who see the URL; 403
  for a Workspace Viewer, 404 for another tenant.
- F6: the ``(endpoint_id, created_at)`` index exists and answers the destination registry's 24 h count.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.tests.hard_erase_fixture import migration_fixture_cursor
from tfc.constants.levels import Level
from webhooks.models import Delivery, Event, Payload
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
    workspace_in,
)

pytestmark = pytest.mark.django_db(transaction=True)
BULK = "/webhooks/deliveries/redeliver/"


def code_of(r):
    return r.json().get("code")


@pytest.fixture
def failed_six(env, owner, organization, workspace):
    """Six failed deliveries of one endpoint: prompts pA (seq 1-3) and pB (seq 4-6); the receiver answers 200 after."""
    rcv = Receiver(answers=[(413, b"too large")] * 6 + [(200, b"ok")])
    ep_id, _ = make(
        owner, rcv.url, subscriptions=[{"event_type": "prompt.label.changed"}]
    )
    for i, prompt in enumerate(["pA"] * 3 + ["pB"] * 3):
        pub(organization, workspace, f"k{i}", prompt_id=prompt)
    with Worker() as wk:
        assert wait_for(lambda: statuses(ep_id).count("failed") == 6, 30)
    assert wk.errors == []
    yield ep_id, rcv
    rcv.close()


def _seqs(ep_id, **kw):
    return sorted(
        Delivery.objects.filter(endpoint_id=ep_id, **kw).values_list("seq", flat=True)
    )


def test_f5_bulk_redelivery_takes_the_logs_subject_and_sequence_filters(
    owner, failed_six
):
    ep_id, rcv = failed_six
    assert _seqs(ep_id) == [1, 2, 3, 4, 5, 6]
    q = {"endpoint": ep_id, "status": "failed", "dry_run": True}

    def dry(**kw):
        r = owner.post(BULK, {**q, **kw}, format="json")
        assert r.status_code == 200, r.content
        return r.json()["result"]["count"]

    assert dry() == 6
    assert dry(subject="prompt:pA") == 3
    assert dry(seq_from=2, seq_to=4) == 3
    assert dry(subject="prompt:pB", seq_from=2, seq_to=5) == 2
    assert dry(subject="prompt:nope") == 0
    for bad in ({"subject": "prompt:"}, {"subject": "dataset:x"}, {"seq_from": 0}):
        r = owner.post(BULK, {**q, **bad}, format="json")
        assert r.status_code == 400, (bad, r.content)
    # The real run queues exactly the rows the log shows under the same filters.
    view = owner.get(
        f"{EP}{ep_id}/deliveries/",
        {"status": "failed", "subject": "prompt:pB", "seq_from": 2, "seq_to": 5},
    ).json()["result"]["results"]
    shown = sorted(d["id"] for d in view)
    assert len(shown) == 2
    with Worker() as wk:
        r = owner.post(
            BULK,
            {
                "endpoint": ep_id,
                "status": "failed",
                "subject": "prompt:pB",
                "seq_from": 2,
                "seq_to": 5,
            },
            format="json",
        )
        assert r.json()["result"] == {
            "count": 2,
            "queued": 2,
            "expired": 0,
            "dry_run": False,
        }
        assert wait_for(lambda: statuses(ep_id).count("succeeded") == 2, 30)
    assert wk.errors == []
    done = sorted(
        Delivery.objects.filter(endpoint_id=ep_id, status="succeeded").values_list(
            "id", flat=True
        )
    )
    assert done == shown
    assert _seqs(ep_id, status="succeeded") == [4, 5]
    assert _seqs(ep_id, status="failed") == [1, 2, 3, 6]
    # The audit entry records the filter it acted on.
    entry = owner.get(f"{EP}{ep_id}/activity/").json()["result"]["results"][0]
    assert entry["action"] == "webhook.delivery.redelivered"
    assert entry["metadata"]["filter"] == {
        "endpoint": ep_id,
        "status": "failed",
        "subject": "prompt:pB",
        "seq_from": 2,
        "seq_to": 5,
    }


def test_f2_the_body_less_posts_take_an_empty_object_and_refuse_fields(
    owner, failed_six
):
    ep_id, _ = failed_six
    d1, d2 = Delivery.objects.filter(endpoint_id=ep_id).order_by("seq")[:2]
    r = owner.post(
        f"/webhooks/deliveries/{d1.id}/redeliver/", {"attempt": 9}, format="json"
    )
    assert r.status_code == 400, r.content
    assert (
        owner.post(
            f"/webhooks/deliveries/{d1.id}/redeliver/", {}, format="json"
        ).status_code
        == 202
    )
    assert owner.post(f"/webhooks/deliveries/{d2.id}/redeliver/").status_code == 202
    assert (
        owner.post(f"{EP}{ep_id}/rotate-secret/", {}, format="json").status_code == 200
    )
    r = owner.post(
        f"{EP}{ep_id}/revoke-previous-secret/", {"secret_id": "x"}, format="json"
    )
    assert r.status_code == 400, r.content
    r = owner.post(f"{EP}{ep_id}/revoke-previous-secret/", {}, format="json")
    assert r.status_code == 200, r.content


@pytest.fixture
def audited(env, owner, organization, workspace):
    """One endpoint with created, secret created, updated, disabled, enabled, rotated and stopped-previous entries."""
    ep_id, secret = make(
        owner,
        "https://hooks.acme.ai/omega?token=abc",
        headers={"X-Literal": "literal-value-9z"},
    )
    assert (
        owner.patch(f"{EP}{ep_id}/", {"timeout_ms": 9000}, format="json").status_code
        == 200
    )
    assert owner.post(f"{EP}{ep_id}/disable/", {}, format="json").status_code == 200
    assert owner.post(f"{EP}{ep_id}/enable/", {}, format="json").status_code == 200
    rotated = owner.post(
        f"{EP}{ep_id}/rotate-secret/", {"overlap_seconds": 86400}, format="json"
    )
    assert rotated.status_code == 200, rotated.content
    assert (
        owner.post(
            f"{EP}{ep_id}/revoke-previous-secret/", {}, format="json"
        ).status_code
        == 200
    )
    return ep_id, [secret, rotated.json()["result"]["secret"]]


def test_f3_activity_lists_this_endpoints_entries_newest_first_and_pages(
    owner, audited
):
    ep_id, _ = audited
    r = owner.get(f"{EP}{ep_id}/activity/")
    assert r.status_code == 200, r.content
    rows = r.json()["result"]["results"]
    actions = [e["action"] for e in rows]
    assert actions[0] == "webhook.secret.previous_revoked"
    for a in (
        "webhook.endpoint.created",
        "webhook.endpoint.updated",
        "webhook.endpoint.disabled",
        "webhook.endpoint.enabled",
        "webhook.secret.rotated",
    ):
        assert a in actions, a
    ats = [e["at"] for e in rows]
    assert ats == sorted(ats, reverse=True)
    updated = next(e for e in rows if e["action"] == "webhook.endpoint.updated")
    assert "timeout_ms" in updated["changed"]
    assert updated["changes"]["timeout_ms"]["after"] == 9000
    assert updated["actor"]["name"] and updated["system"] is False
    # Pages of two, no overlap, the same entries in the same order.
    seen, cursor = [], None
    while True:
        page = owner.get(
            f"{EP}{ep_id}/activity/",
            {"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        body = page.json()["result"]
        seen += [e["id"] for e in body["results"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == [e["id"] for e in rows]
    other = owner.get(
        f"{EP}{ep_id}/activity/", {"cursor": "00000000-0000-0000-0000-000000000001"}
    )
    assert (other.status_code, code_of(other)) == (400, "WEBHOOK_FILTER_INVALID")
    assert owner.get(f"{EP}{ep_id}/activity/", {"page": 2}).status_code == 400


def test_f3_activity_rbac_members_see_what_happened_but_not_where_viewers_and_tenants_nothing(
    owner, audited, organization, workspace
):
    ep_id, secrets = audited
    member = client_for(
        person(organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "mem"),
        organization,
        workspace,
    )
    viewer = client_for(
        person(organization, workspace, Level.VIEWER, Level.WORKSPACE_VIEWER, "viewer"),
        organization,
        workspace,
    )
    full = owner.get(f"{EP}{ep_id}/activity/").json()["result"]["results"]
    r = member.get(f"{EP}{ep_id}/activity/")
    assert r.status_code == 200, r.content
    rows = r.json()["result"]["results"]
    assert [e["id"] for e in rows] == [e["id"] for e in full]
    assert all(e["changes"] is None for e in rows)
    text = json.dumps(r.json())
    # Field names may show ("url_display" changed); their values, header names and secret tails may not.
    for needle in (
        "hooks.acme.ai/omega",
        "X-Literal",
        "literal-value-9z",
        "tail",
        "whsec_",
        *secrets,
    ):
        assert needle not in text, needle
    assert next(e for e in rows if e["action"] == "webhook.endpoint.updated")["changed"]
    # The owner's view carries the masked URL and the header name (never the token, a value or a secret).
    owner_text = json.dumps(full)
    assert "X-Literal" in owner_text and "url_display" in owner_text
    for needle in ("token=abc", "literal-value-9z", *secrets):
        assert needle not in owner_text, needle
    r = viewer.get(f"{EP}{ep_id}/activity/")
    assert (r.status_code, code_of(r)) == (403, "WEBHOOK_FORBIDDEN")
    org_b = Organization.objects.create(name="Tenant B")
    owner_b = person(org_b, None, Level.OWNER, None, "ownerb")
    cb = client_for(owner_b, org_b, workspace_in(org_b, "B main", owner_b))
    r = cb.get(f"{EP}{ep_id}/activity/")
    assert (r.status_code, code_of(r)) == (404, "WEBHOOK_NOT_FOUND")


def test_f3_a_one_delivery_redelivery_is_in_its_endpoints_activity(owner, failed_six):
    ep_id, _ = failed_six
    d = Delivery.objects.filter(endpoint_id=ep_id).order_by("seq").first()
    assert (
        owner.post(
            f"/webhooks/deliveries/{d.id}/redeliver/", {}, format="json"
        ).status_code
        == 202
    )
    top = owner.get(f"{EP}{ep_id}/activity/").json()["result"]["results"][0]
    assert top["action"] == "webhook.delivery.redelivered"
    assert top["metadata"]["delivery_id"] == d.id
    assert top["metadata"]["attempt"] == 2
    assert top["changed"] == [] and top["actor"] is not None


def test_f6_the_endpoint_created_index_answers_the_registrys_24h_count(
    env, owner, organization, workspace
):
    with connection.cursor() as c:
        c.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'webhooks_delivery' AND indexname = %s",
            ["dlv_endpoint_created"],
        )
        (indexdef,) = c.fetchone()
    assert "(endpoint_id, created_at)" in indexdef
    ep_id, _ = make(owner, "https://hooks.acme.ai/omega")
    # A month of one endpoint's deliveries, one every 15 minutes, none on the 24 h boundary (2,880 rows, 96 in the last day), with statistics:
    # on an empty table any index on endpoint_id ties (an earlier run planned the FK index there).
    n = 2880
    events = Event.objects.bulk_create(
        Event(
            organization=organization,
            workspace=workspace,
            type="prompt.label.changed",
            occurred_at=timezone.now(),
            producer="test",
            source_key=f"f6-{i}",
            source_hash="h",
            data={},
        )
        for i in range(n)
    )
    payloads = Payload.objects.bulk_create(
        Payload(
            organization=organization,
            event=e,
            payload_mode="thin",
            body_sha256="0" * 64,
            body_bytes=0,
        )
        for e in events
    )
    Delivery.objects.bulk_create(
        Delivery(
            organization=organization,
            event=e,
            endpoint_id=ep_id,
            payload=pl,
            seq=i + 1,
            status="succeeded",
        )
        for i, (e, pl) in enumerate(zip(events, payloads, strict=True))
    )
    with migration_fixture_cursor(connection) as c:
        c.execute(
            "UPDATE webhooks_delivery SET created_at = now() - ((seq - 0.5) * interval '15 minutes') WHERE endpoint_id = %s",
            [ep_id],
        )
        c.execute("ANALYZE webhooks_delivery")
    since = timezone.now() - timedelta(hours=24)
    # The registry's query (integrations/services/destinations.py), planned with sequential scans off: of the
    # indexes that lead with endpoint_id, only this one bounds the day.
    qs = (
        Delivery.objects.filter(
            endpoint_id__in=[ep_id], is_test=False, created_at__gte=since
        )
        .values("endpoint_id")
        .annotate(total=Count("id"), failed=Count("id", filter=Q(status="failed")))
    )
    assert qs.get()["total"] == 96
    with connection.cursor() as c:
        c.execute("SET enable_seqscan = off")
        try:
            plan = qs.explain()
        finally:
            c.execute("RESET enable_seqscan")
    assert "dlv_endpoint_created" in plan, plan


def test_s1_org_wide_activity_shows_delivery_data_only_to_who_reads_its_log(
    env, owner, receiver, organization, workspace
):
    """Acceptance S1 (section 9.1: an org-wide endpoint's delivery log is Org Admin only). A workspace Member and a
    Workspace Admin who are org Members may view the org-wide endpoint's Activity: they see that a test was sent and
    who sent it, but not its delivery id, event id, attempt, status code or outcome. The Owner sees them. On a
    workspace endpoint a Member still sees them (``VIEW_LOG`` is Member there)."""
    log_keys = {"delivery_id", "event_id", "attempt", "status_code", "outcome"}

    org_ep, _ = make(owner, receiver.url, workspace_id=None)
    ws_ep, _ = make(owner, receiver.url)
    with Worker():
        for ep_id in (org_ep, ws_ep):
            r = owner.post(
                f"{EP}{ep_id}/test/",
                {"event_type": "prompt.label.changed"},
                format="json",
            )
            assert r.status_code in (200, 202), r.content
        assert wait_for(lambda: len(receiver.seen) == 2, 30)

    def test_entry(client, ep_id):
        r = client.get(f"{EP}{ep_id}/activity/")
        assert r.status_code == 200, r.content
        return next(
            e
            for e in r.json()["result"]["results"]
            if e["action"] == "webhook.test.sent"
        )

    full = test_entry(owner, org_ep)
    assert {"delivery_id", "status_code", "outcome"} <= set(full["metadata"]), full
    member = person(organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "m1")
    ws_admin = person(
        organization, workspace, Level.MEMBER, Level.WORKSPACE_ADMIN, "a1"
    )
    for who in (member, ws_admin):
        c = client_for(who, organization, workspace)
        seen = test_entry(c, org_ep)
        assert seen["actor"] is not None and seen["changes"] is None
        assert not set(seen["metadata"]) & log_keys, seen["metadata"]
        text = json.dumps(seen)
        for key in ("delivery_id", "event_id"):
            if key in full["metadata"]:
                assert str(full["metadata"][key]) not in text, key
        # The org-wide delivery log itself stays closed to them (the rule the Activity now follows).
        assert c.get("/webhooks/deliveries/", {"endpoint": org_ep}).status_code in (
            403,
            404,
        )
    # A workspace endpoint's Member reads its log, so the same entry keeps the delivery data.
    mine = test_entry(client_for(member, organization, workspace), ws_ep)
    assert {"delivery_id", "status_code", "outcome"} <= set(mine["metadata"]), mine
