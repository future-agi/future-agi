"""Retention, org deletion and member erasure on Postgres (DESIGN.md sections 7.5, 13.4 and 17: WT-33; P1, P4, R8,
R12). Ages are made by moving ``created_at`` back; every purge runs in chunks (made small here to prove it)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import transaction

from accounts.models import Organization
from webhooks import health_pass, lifecycle, retention
from webhooks.core.attempt import AttemptRecord, next_action_for
from webhooks.models import (
    ApiAction,
    Attempt,
    Endpoint,
    EndpointState,
    Event,
    OpsAlert,
    Payload,
    Policy,
    Secret,
    Subscription,
)
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(retention, "CHUNK", 2)
    monkeypatch.setattr(retention, "EVENT_CHUNK", 2)
    monkeypatch.setattr(lifecycle, "CHUNK", 2)


def now():
    return datetime.now(UTC)


def pub(org, ws, keys_, data=None):
    with transaction.atomic():
        for k in keys_:
            publish(
                type="prompt.label.changed",
                organization_id=org.id,
                workspace_id=None if ws is None else ws.id,
                data=data or label_event_data(),
                source_key=k,
                source_hash="h",
                producer="test",
                occurred_at=T0,
            )


def settle_all(answer_status=503, excerpt="echo"):
    """Lease every due delivery and settle one attempt on each, as the dispatcher would."""
    store = OrmStore()
    t = int(time.time() * 1000)
    for c in store.claim_due(t, "normal", 48, 48):
        rec = AttemptRecord(
            n=c.attempt_n,
            trigger="auto",
            started_at_ms=t,
            settled_at_ms=t,
            outcome="http_error" if answer_status >= 300 else "success",
            response_status=answer_status,
            response_body_excerpt=excerpt,
            duration_ms=5,
        )
        store.settle(c, rec.__dict__, next_action_for(c, rec))


def age(qs, days):
    qs.update(created_at=now() - timedelta(days=days))


@pytest.mark.django_db(transaction=True)
def test_retention_windows(organization, workspace, keys, small_chunks):
    ep = make_endpoint(organization, workspace, rate_limit_per_s=100, max_in_flight=16)
    # Subscribed to run events only: it keeps the org "live" for publish() and matches no prompt event.
    make_endpoint(organization, workspace, event_types=("deployment.run.*",))
    pub(organization, workspace, ["old", "body", "fresh"])
    OrmStore().fan_out(int(time.time() * 1000), 500)
    settle_all()
    age(Event.objects.filter(source_key="old"), 91)
    age(Event.objects.filter(source_key="body"), 31)
    # An event that reached no endpoint: deleted after 7 days, not before.
    Endpoint.objects.filter(id=ep.id).update(deleted=True)
    pub(organization, workspace, ["empty-old", "empty-new"])
    Endpoint.objects.filter(id=ep.id).update(deleted=False)
    OrmStore().fan_out(int(time.time() * 1000), 500)
    Event.objects.filter(source_key__startswith="empty").update(delivery_count=0)
    age(Event.objects.filter(source_key="empty-old"), 8)
    age(Event.objects.filter(source_key="empty-new"), 6)

    out = retention.run(now())
    keys_left = set(Event.objects.values_list("source_key", flat=True))
    assert keys_left == {"body", "fresh", "empty-new"}
    body_ev = Event.objects.get(source_key="body")
    p = Payload.objects.get(event=body_ev)
    assert p.body is None and len(p.body_sha256) == 64 and p.body_bytes > 0
    assert Payload.objects.get(event__source_key="fresh").body is not None
    assert out["log"]["events"] == 1 and out["log"]["attempts"] == 1
    assert out["zero_delivery_events"] == 1
    assert not Attempt.objects.filter(delivery__event__source_key="old").exists()


@pytest.mark.django_db(transaction=True)
def test_retention_follows_each_orgs_policy_and_gives_back_backlog_places(
    organization, workspace, keys, small_chunks
):
    ep = make_endpoint(organization, workspace, status="disabled_manual")
    Policy.objects.create(organization=organization, log_retention_days=7)
    org_b = Organization.objects.create(name="Org B")
    make_endpoint(org_b, None)
    pub(organization, workspace, [f"a{i}" for i in range(5)])
    pub(org_b, None, ["b1"])
    OrmStore().fan_out(int(time.time() * 1000), 500)
    assert EndpointState.objects.get(endpoint=ep).paused_count == 5
    age(Event.objects.all(), 8)
    policies = list(
        Policy.objects.values(
            "id", "organization_id", "workspace_id", "log_retention_days"
        )
    )
    found = Policy.objects.filter(
        organization_id=organization.id, workspace__isnull=True
    ).first()
    assert retention.org_log_days(organization.id) == 7, (
        organization.id,
        policies,
        found and found.log_retention_days,
    )
    assert retention.org_log_days(org_b.id) == 90
    out = retention.run(now())
    left = list(
        Event.objects.filter(organization=organization).values_list(
            "created_at", flat=True
        )
    )
    assert not left, (out, left, now())
    assert out["log"]["events"] == 5 and out["log"]["deliveries"] == 5
    assert Event.objects.filter(organization=org_b).count() == 1  # 90 days by default
    assert EndpointState.objects.get(endpoint=ep).paused_count == 0


@pytest.mark.django_db(transaction=True)
def test_secrets_purge_and_the_attempt_quota_wa9(
    organization, workspace, keys, small_chunks, caplog
):
    ep = make_endpoint(organization, workspace, rate_limit_per_s=100, max_in_flight=16)
    gone = make_endpoint(organization, workspace)
    Endpoint.objects.filter(id=gone.id).update(deleted=True)
    Secret.objects.filter(endpoint=ep).update(state="revoked")
    pub(organization, workspace, [f"k{i}" for i in range(4)])
    OrmStore().fan_out(int(time.time() * 1000), 500)
    settle_all(excerpt="receiver said hi")
    caplog.set_level(logging.INFO)
    out = retention.run(now(), quota=2)
    assert out["secrets"] == 2
    assert not Secret.objects.exists()
    assert out["orgs_over_attempt_quota"] == [str(organization.id)]
    kept = list(
        Attempt.objects.order_by("settled_at", "id").values_list(
            "response_body_excerpt", flat=True
        )
    )
    assert kept[:2] == [None, None] and kept[2:] == ["receiver said hi"] * 2
    assert any(getattr(r, "alert", None) == "WA9" for r in caplog.records)
    health_pass.run(now())
    assert OpsAlert.objects.get(id="WA9").active


@pytest.mark.django_db(transaction=True)
def test_wt33_deleting_an_org_leaves_no_webhooks_rows(
    organization, workspace, user, keys, small_chunks
):
    def populate(org, ws):
        ep = make_endpoint(org, ws, rate_limit_per_s=100, max_in_flight=16)
        Policy.objects.get_or_create(organization=org, workspace=None)
        pub(org, ws, ["k1", "k2", "k3"])
        OrmStore().fan_out(int(time.time() * 1000), 500)
        settle_all()
        for kind, d in (
            ("write", None),
            ("redeliver", ep.deliveries.first()),
        ):  # WH-2's ledger
            ApiAction.objects.create(
                organization=org, kind=kind, endpoint=ep, delivery=d
            )
        return ep

    populate(organization, workspace)
    org_b = Organization.objects.create(name="Org B")
    populate(org_b, None)
    tenant_models = [
        m
        for m in apps.get_app_config("webhooks").get_models()
        if lifecycle.PURGE_RULES[m._meta.db_table] != lifecycle.INSTALL_WIDE
    ]
    for m in tenant_models:
        assert m.objects.filter(organization=organization).exists(), m._meta.db_table
    before_b = {
        m._meta.db_table: m.objects.filter(organization=org_b).count()
        for m in tenant_models
    }

    out = lifecycle.purge_organization(organization.id)
    left_a = {
        m._meta.db_table: m.objects.filter(organization=organization).count()
        for m in tenant_models
    }
    after_b = {
        m._meta.db_table: m.objects.filter(organization=org_b).count()
        for m in tenant_models
    }
    Organization.objects.filter(id=org_b.id).delete()
    left_b = {
        m._meta.db_table: m.objects.filter(organization_id=org_b.id).count()
        for m in tenant_models
    }
    evidence.write(
        "WT-33",
        purge_organization_deleted=out,
        org_a_rows_left_per_table=left_a,
        org_b_rows_before_and_after_org_a_purge={"before": before_b, "after": after_b},
        org_b_rows_left_after_its_organization_row_was_deleted=left_b,
    )
    assert out["webhooks_delivery"] == 3 and out["webhooks_attempt"] == 3
    assert set(left_a.values()) == {0}, left_a
    assert after_b == before_b
    # Deleting the Organization row itself cascades over every webhooks table too.
    assert set(left_b.values()) == {0}, left_b


@pytest.mark.django_db(transaction=True)
def test_wt33_erasing_a_member_nulls_their_name_in_bodies_events_and_excerpts(
    organization, workspace, user, keys, small_chunks
):
    make_endpoint(organization, workspace, rate_limit_per_s=100, max_in_flight=16)
    person = {"id": str(user.id), "name": "Asha Rao"}
    other = {"id": "u-other", "name": "Kim Lee"}
    for i, who in enumerate((person, other)):
        pub(
            organization,
            workspace,
            [f"k{i}"],
            data={**label_event_data(), "changed_by": who},
        )
    OrmStore().fan_out(int(time.time() * 1000), 500)
    for i, who in enumerate((person, other)):
        # What a producer keeps for the variant builder names people too.
        Event.objects.filter(source_key=f"k{i}").update(
            builder_input={"changed_by": who}
        )
    settle_all(excerpt="thanks Asha Rao and Kim Lee")
    assert b"Asha Rao" in bytes(Payload.objects.get(event__source_key="k0").body)

    out = lifecycle.erase_member(organization.id, user.id)
    p0 = Payload.objects.get(event__source_key="k0")
    evidence.write(
        "WT-33",
        erase_member_changed=out,
        kept_body_after_erasure=bytes(p0.body).decode("utf-8"),
        other_person_name_kept=b"Kim Lee"
        in bytes(Payload.objects.get(event__source_key="k1").body),
    )
    assert out == {"payloads": 1, "events": 1, "attempts": 1}
    assert b"Asha Rao" not in bytes(p0.body)
    assert b'"changed_by":{"id":"%s","name":null}' % str(user.id).encode() in bytes(
        p0.body
    )
    import hashlib

    assert p0.body_sha256 == hashlib.sha256(bytes(p0.body)).hexdigest()
    ev0 = Event.objects.get(source_key="k0")
    assert ev0.data["changed_by"]["name"] is None
    assert ev0.builder_input["changed_by"]["name"] is None
    a0 = Attempt.objects.get(delivery__event=ev0)
    assert (
        "Asha Rao" not in a0.response_body_excerpt
        and "Kim Lee" in a0.response_body_excerpt
    )
    # The other person is untouched.
    assert b"Kim Lee" in bytes(Payload.objects.get(event__source_key="k1").body)
    assert Event.objects.get(source_key="k1").data["changed_by"]["name"] == "Kim Lee"
    assert lifecycle.erase_member(organization.id, user.id) == {
        "payloads": 0,
        "events": 0,
        "attempts": 0,
    }


def test_wt33_every_webhooks_table_has_a_purge_rule_and_a_planted_one_fails():
    models = list(apps.get_app_config("webhooks").get_models())
    planted = SimpleNamespace(_meta=SimpleNamespace(db_table="webhooks_planted"))
    evidence.write(
        "WT-33",
        purge_rules={t: str(r) for t, r in sorted(lifecycle.PURGE_RULES.items())},
        uncovered_tables=lifecycle.uncovered_tables(),
        uncovered_with_a_planted_table=lifecycle.uncovered_tables([*models, planted]),
    )
    assert lifecycle.uncovered_tables() == []
    assert len(models) == len(lifecycle.PURGE_RULES)
    assert lifecycle.uncovered_tables([*models, planted]) == ["webhooks_planted"]
    tenant = {
        t for t, r in lifecycle.PURGE_RULES.items() if r != lifecycle.INSTALL_WIDE
    }
    assert tenant == set(lifecycle.ORDER)
    for m in models:
        if m._meta.db_table in tenant:
            fk = m._meta.get_field("organization")
            assert fk.remote_field.on_delete.__name__ == "CASCADE", m._meta.db_table
    assert Subscription._meta.db_table in tenant


@pytest.mark.django_db(transaction=True)
def test_the_attempt_quota_keeps_the_newest_excerpts_on_every_later_run(
    organization, workspace, keys, small_chunks
):
    """Section 7.8: the org's newest ``quota`` rows keep their excerpts and the older ones lose them, oldest first. The
    daily pass runs again with nothing new (no change), then after two more rows settle (the cut moves by two)."""
    make_endpoint(organization, workspace, rate_limit_per_s=100, max_in_flight=16)
    pub(organization, workspace, [f"q{i}" for i in range(4)])
    OrmStore().fan_out(int(time.time() * 1000), 500)
    settle_all(200, excerpt="receiver said hi")  # succeeded: no retry joins later

    def kept():
        return list(
            Attempt.objects.order_by("settled_at", "id").values_list(
                "response_body_excerpt", flat=True
            )
        )

    hi = "receiver said hi"
    assert retention.attempt_quota(2) == [str(organization.id)]
    assert kept() == [None, None, hi, hi]
    assert retention.attempt_quota(2) == [str(organization.id)]  # the next day
    assert kept() == [None, None, hi, hi]
    time.sleep(0.01)  # the new rows settle strictly later
    pub(organization, workspace, ["q4", "q5"])
    OrmStore().fan_out(int(time.time() * 1000), 500)
    settle_all(200, excerpt="newer")
    retention.attempt_quota(2)
    assert kept() == [None, None, None, None, "newer", "newer"]
    assert retention.attempt_quota(6) == []
    assert kept() == [None, None, None, None, "newer", "newer"]


@pytest.mark.django_db(transaction=True)
def test_wt33_erasure_scrubs_excerpts_after_the_body_was_dropped(
    organization, workspace, user, keys, small_chunks
):
    """Bodies are dropped at 30 days and excerpts kept for the org's log retention (90 by default): erasing the member
    on day 31 still removes their name from the excerpt, through the event that named them."""
    make_endpoint(organization, workspace, rate_limit_per_s=100, max_in_flight=16)
    for key, who in (
        ("e1", (str(user.id), "Asha Rao")),
        ("e2", ("u-other", "Kim Lee")),
    ):
        data = {**label_event_data(), "changed_by": {"id": who[0], "name": who[1]}}
        pub(organization, workspace, [key], data=data)
    OrmStore().fan_out(int(time.time() * 1000), 500)
    settle_all(excerpt="thanks Asha Rao and Kim Lee")
    age(Event.objects.all(), 31)
    assert retention.drop_bodies(now() - timedelta(days=30)) == 2
    assert not Payload.objects.filter(body__isnull=False).exists()

    out = lifecycle.erase_member(organization.id, user.id)
    a1 = Attempt.objects.get(delivery__event__source_key="e1")
    ev1 = Event.objects.get(source_key="e1")
    evidence.write(
        "WT-33",
        body_dropped_before_erasure=True,
        erase_member_changed=out,
        excerpt_after_erasure=a1.response_body_excerpt,
    )
    assert out == {"payloads": 0, "events": 1, "attempts": 1}
    assert ev1.data["changed_by"]["name"] is None
    assert "Asha Rao" not in a1.response_body_excerpt
    assert "Kim Lee" in a1.response_body_excerpt
    assert Event.objects.get(source_key="e2").data["changed_by"]["name"] == "Kim Lee"
    assert lifecycle.erase_member(organization.id, user.id) == {
        "payloads": 0,
        "events": 0,
        "attempts": 0,
    }
