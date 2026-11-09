"""The ops alerts and the ops stats on Postgres (DESIGN.md sections 13.2, 13.3 and 17: WT-25's alerts half, WA10, the
sending-off hold-back and the staff-only stats view).

WT-25 runs the real dispatcher loop against a real receiver with FI_WEBHOOKS_TIME_SCALE semantics (the scale is handed
to the store, the dispatcher and the health pass, as the activity does when the guard allows it). The alerts are error
log lines with their ids (the error pipeline's input), captured here. WT-25's restart half is ``test_ops_temporal``.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from django.db import close_old_connections, transaction
from django.db.models import F

from webhooks import config, gauges, health_pass
from webhooks.beats import record as record_beat
from webhooks.models import (
    Attempt,
    Delivery,
    DispatcherBeat,
    Event,
    OpsAlert,
    OpsSnapshot,
)
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.receiver import Receiver, real_dispatcher


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def now():
    return datetime.now(UTC)


def pub(org, ws, keys_):
    with transaction.atomic():
        for k in keys_:
            publish(
                type="prompt.label.changed",
                organization_id=org.id,
                workspace_id=ws.id,
                data=label_event_data(),
                source_key=k,
                source_hash="h",
                producer="test",
                occurred_at=T0,
            )


def _loop(d, stop, seconds):
    try:
        d.run(stop, run_for_s=seconds)
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
def test_wt25_dispatcher_down_fires_wa1_and_wa2_then_both_clear_when_it_runs(
    organization, workspace, keys, receiver, caplog, monkeypatch
):
    """Scale 0.01 (60 s = 0.6 s, 2 min = 1.2 s, 5 min = 3 s). A loop that beat once and stopped, while events queue:
    WA2 fires as soon as its beat is older than "2 min", WA1 once the oldest due work has been over "60 s" behind for
    "5 min". The loop runs again (what webhooks-health's ensure step starts): the backlog drains and both clear. The
    alerts are error log lines with their ids (the error pipeline's input), captured here."""
    scale = 0.01
    monkeypatch.setattr(
        config, "is_hosted", lambda: False
    )  # sending is allowed on this install
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    caplog.set_level(logging.INFO, logger="webhooks.health")
    ep = make_endpoint(
        organization, workspace, url=receiver.url("/ok"), rate_limit_per_s=100
    )
    record_beat("webhooks-dispatcher-1", {"normal": 0, "slow": 0}, now())
    pub(organization, workspace, [f"k{i}" for i in range(20)])

    def fired(alert_id):
        return [
            r
            for r in caplog.records
            if getattr(r, "alert", None) == alert_id
            and getattr(r, "alert_state", None) == "fired"
            and r.levelno == logging.ERROR
        ]

    t_end = time.monotonic() + 8
    while time.monotonic() < t_end and not (fired("WA1") and fired("WA2")):
        health_pass.run(now(), scale)
        time.sleep(0.2)
    assert fired("WA2") and fired("WA1"), [r.getMessage() for r in caplog.records]
    assert (
        OpsAlert.objects.get(id="WA1").active and OpsAlert.objects.get(id="WA2").active
    )
    snap = OpsSnapshot.objects.get(id="stats").data
    assert snap["due"] >= 20 and snap["alerts"]["WA2"]["active"]

    d = real_dispatcher(receiver, scale=scale)
    d.heartbeat = lambda: record_beat("webhooks-dispatcher-1", d.busy())
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(d, stop, 4.0), daemon=True)
    t.start()
    t.join(30)
    assert Delivery.objects.filter(endpoint=ep, status="succeeded").count() == 20
    record_beat("webhooks-dispatcher-1", {"normal": 0, "slow": 0}, now())
    health_pass.run(now(), scale)
    cleared = {
        r.alert for r in caplog.records if getattr(r, "alert_state", None) == "cleared"
    }
    evidence.write(
        "WT-25",
        scale=scale,
        alert_lines=[
            {
                "alert": r.alert,
                "state": r.alert_state,
                "level": r.levelname,
                "message": r.getMessage(),
            }
            for r in caplog.records
            if getattr(r, "metric", None) == "webhooks.alert"
        ],
        snapshot_while_down={
            "due": snap["due"],
            "oldest_due_seconds": snap["oldest_due_seconds"],
            "last_beat_age_seconds": snap["last_beat_age_seconds"],
        },
        db={
            "alerts_after": {
                a.id: a.active for a in OpsAlert.objects.filter(id__in=["WA1", "WA2"])
            },
            "deliveries": evidence.by_status(Delivery.objects.filter(endpoint=ep)),
        },
        receiver=evidence.receiver_lines(receiver, limit=20),
    )
    assert {"WA1", "WA2"} <= cleared
    assert not OpsAlert.objects.get(id="WA1").active
    assert not OpsAlert.objects.get(id="WA2").active
    assert DispatcherBeat.objects.get(loop="webhooks-dispatcher-1").beat_at is not None


@pytest.mark.django_db(transaction=True)
def test_backlog_alerts_hold_back_while_sending_is_off(
    organization, workspace, keys, monkeypatch
):
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    monkeypatch.setenv("FI_WEBHOOKS_ENABLED", "false")
    make_endpoint(organization, workspace)
    pub(organization, workspace, ["k1"])
    t = now()
    health_pass.run(t, 0.01)
    health_pass.run(t + timedelta(minutes=10), 0.01)
    assert not OpsAlert.objects.filter(id__in=["WA1", "WA2"], active=True).exists()
    snap = OpsSnapshot.objects.get(id="stats").data
    assert snap["sending_blocked"] and snap["outbox_pending"] == 1


@pytest.mark.django_db(transaction=True)
def test_ops_stats_are_staff_only(organization, workspace, user, auth_client, keys):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    auth_client.force_authenticate(user=user)
    # Before the first health pass: 503 in FA's common error envelope (FA's
    # test_api_error_response_shapes guard), with computed_at still there.
    before = auth_client.get("/webhooks/ops/stats/")
    assert before.status_code == 503
    body = before.json()
    assert body["status"] is False
    assert (body["type"], body["code"]) == (
        "service_unavailable",
        "service_unavailable",
    )
    assert body["detail"] == body["message"] == "The health pass has not run yet."
    assert body["computed_at"] is None
    user.is_staff = False
    user.save(update_fields=["is_staff"])
    auth_client.force_authenticate(user=user)
    health_pass.run(now())
    assert auth_client.get("/webhooks/ops/stats/").status_code == 403
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    auth_client.force_authenticate(user=user)
    r = auth_client.get("/webhooks/ops/stats/")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "backlog_due",
        "oldest_due_seconds",
        "endpoints",
        "slots_busy",
        "alerts",
    ):
        assert key in body


@pytest.mark.django_db(transaction=True)
def test_wa10_a_failed_fan_out_fires_and_clears_ten_minutes_later(
    organization, workspace, keys, caplog
):
    """WA10 (added to section 13.3 here): one event whose fan-out failed (no ``prompt`` in its data) fires the alert
    at the next health pass, as an error line with its id; the lag gauge (WA3) counts done events only; the alert
    clears once the failure is older than 10 minutes."""
    make_endpoint(organization, workspace)
    no_prompt = label_event_data()
    del no_prompt["prompt"]
    with transaction.atomic():
        publish(
            type="prompt.label.changed",
            organization_id=organization.id,
            workspace_id=workspace.id,
            data=no_prompt,
            source_key="bad",
            source_hash="h",
            producer="test",
            occurred_at=T0,
        )
    t = now()
    store = OrmStore()
    assert store.fan_out(int(t.timestamp() * 1000), 500) == 1
    bad = Event.objects.get(source_key="bad")
    assert bad.fanout_state == "failed" and bad.fanned_out_at is not None
    caplog.set_level(logging.INFO, logger="webhooks.health")

    assert "WA10:fired" in health_pass.run(t + timedelta(seconds=5))["alerts_changed"]
    fired = [r for r in caplog.records if getattr(r, "alert", None) == "WA10"]
    assert [(r.levelno, r.alert_state, r.alert_events) for r in fired] == [
        (logging.ERROR, "fired", 1)
    ]
    snap = OpsSnapshot.objects.get(id="stats").data
    assert snap["fanout_failed_10m"] == 1 and snap["alerts"]["WA10"]["active"]
    assert snap["fanout_lag_p95_seconds"] is None  # a failed fan-out is not a lag

    pub(organization, workspace, ["good"])
    assert store.fan_out(int(now().timestamp() * 1000), 500) == 1
    health_pass.run(t + timedelta(seconds=10))
    snap = OpsSnapshot.objects.get(id="stats").data
    assert snap["fanout_lag_p95_seconds"] is not None and snap["fanout_failed_10m"] == 1
    assert len([r for r in caplog.records if getattr(r, "alert", None) == "WA10"]) == 1

    assert (
        "WA10:cleared" in health_pass.run(t + timedelta(minutes=11))["alerts_changed"]
    )
    assert not OpsAlert.objects.get(id="WA10").active


@pytest.mark.django_db(transaction=True)
def test_wa2_fires_when_one_of_two_loops_is_silent_and_names_it(
    organization, workspace, keys, monkeypatch, caplog
):
    """Hosted runs 2 loops: loop 2 silent for 10 minutes while loop 1 beats fires WA2 with loop 2's id; a beat from
    a loop past the configured count is ignored."""
    from webhooks import beats
    from webhooks.temporal.workflows import dispatcher_workflow_id

    assert beats.loop_ids(3) == [dispatcher_workflow_id(n) for n in (1, 2, 3)]
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    monkeypatch.setenv("WEBHOOKS_DISPATCHER_LOOPS", "2")
    caplog.set_level(logging.INFO, logger="webhooks.health")
    make_endpoint(organization, workspace)
    pub(organization, workspace, ["w1"])
    t = now()
    record_beat("webhooks-dispatcher-1", {"normal": 0, "slow": 0}, t)
    record_beat(
        "webhooks-dispatcher-2", {"normal": 0, "slow": 0}, t - timedelta(minutes=10)
    )
    record_beat(
        "webhooks-dispatcher-3", {"normal": 0, "slow": 0}, t - timedelta(hours=1)
    )
    assert "WA2:fired" in health_pass.run(t)["alerts_changed"]
    snap = OpsSnapshot.objects.get(id="stats").data
    assert set(snap["loop_beat_age_seconds"]) == {
        "webhooks-dispatcher-1",
        "webhooks-dispatcher-2",
    }
    assert OpsAlert.objects.get(id="WA2").detail["stale_loops"] == [
        "webhooks-dispatcher-2"
    ]
    (line,) = [r for r in caplog.records if getattr(r, "alert", None) == "WA2"]
    assert line.levelno == logging.ERROR and line.alert_stale_loops == [
        "webhooks-dispatcher-2"
    ]
    record_beat("webhooks-dispatcher-2", {"normal": 0, "slow": 0}, t)
    assert "WA2:cleared" in health_pass.run(t)["alerts_changed"]


@pytest.mark.django_db(transaction=True)
def test_fan_out_lag_counts_from_a_deliberate_not_before(organization, workspace, keys):
    """B8 events wait ``not_before`` (+5 s by contract); fanned out 0.1 s after it, the lag is 0.1 s, not the wait."""
    make_endpoint(organization, workspace)
    pub(organization, workspace, ["nb1", "nb2"])
    t = now()
    Event.objects.filter(source_key="nb1").update(
        created_at=t - timedelta(seconds=12), not_before=t - timedelta(milliseconds=100)
    )
    # Still waiting for its not_before: due work only from then, so neither the outbox age nor WA1 counts the wait.
    Event.objects.filter(source_key="nb2").update(
        created_at=t - timedelta(seconds=90), not_before=t - timedelta(seconds=2)
    )
    snap = gauges.snapshot(t)
    assert 1.5 < snap["outbox_oldest_seconds"] < 10
    assert OrmStore().fan_out(int(t.timestamp() * 1000), 500) == 2
    snap = gauges.snapshot(now())
    assert snap["fanout_lag_p95_seconds"] < 5, snap["fanout_lag_p95_seconds"]


@pytest.mark.django_db(transaction=True)
def test_wa1_leaves_out_backlog_held_by_an_endpoints_own_limits(
    organization, workspace, keys, monkeypatch
):
    """A 1/s endpoint sent at its rate for the last minute, and a 2-in-flight endpoint whose attempts kept both slots
    busy, each with deliveries due for 8 minutes: that backlog is the customers' own limit, so WA1 ("sending is
    behind") stays quiet while the gauges still show it. Once the attempts are a minute old the same backlog is ours,
    and WA1 fires after its sustain (scale 0.01: 60 s is 0.6 s, 5 min is 3 s; the 60 s window is real time)."""
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    rate_bound = make_endpoint(organization, workspace, rate_limit_per_s=1)
    busy_bound = make_endpoint(organization, workspace, max_in_flight=2)
    pub(organization, workspace, [f"k{i}" for i in range(60)])
    assert OrmStore().fan_out(int(now().timestamp() * 1000), 500) == 60
    t = now()
    sent = {}
    for ep, n, ms in ((rate_bound, 55, 50), (busy_bound, 12, 12_000)):
        ds = list(Delivery.objects.filter(endpoint=ep).order_by("seq"))
        sent[ep.id] = ds[:n]
        Delivery.objects.filter(id__in=[d.id for d in ds[:n]]).update(
            status="succeeded", next_attempt_at=None
        )
        Delivery.objects.filter(id__in=[d.id for d in ds[n:]]).update(
            next_attempt_at=t - timedelta(minutes=8)
        )
        Attempt.objects.bulk_create(
            Attempt(
                organization=organization,
                delivery=d,
                n=1,
                trigger="auto",
                outcome="success",
                response_status=200,
                duration_ms=ms,
                started_at=t - timedelta(seconds=58 - i),
                settled_at=t - timedelta(seconds=58 - i),
            )
            for i, d in enumerate(ds[:n])
        )
    snap = gauges.snapshot(t)
    assert snap["backlog_due"]["normal"] == 5 + 48
    assert snap["backlog_oldest_due_seconds"]["normal"] > 470
    assert snap["backlog_held_by_endpoint_limits"] == 5 + 48
    assert snap["oldest_due_seconds"] is None
    for dt in (0, 2, 4):
        health_pass.run(t + timedelta(seconds=dt), 0.01)
    assert not OpsAlert.objects.get(id="WA1").active

    # A minute later nothing was sent: the same backlog is behind on our side.
    Attempt.objects.update(settled_at=F("settled_at") - timedelta(minutes=2))
    later = [health_pass.run(t + timedelta(seconds=dt), 0.01) for dt in (6, 8, 10)]
    assert any("WA1:fired" in o["alerts_changed"] for o in later), later
    assert OpsSnapshot.objects.get(id="stats").data["oldest_due_seconds"] > 470
