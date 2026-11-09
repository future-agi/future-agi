"""``webhooks-health``, every 60 s (DESIGN.md sections 5.3, 7.7, 9.1 and 13.1-13.3). One pass:

0. runs G1's offboarding sweep (``api_offboarding.reconcile_org``) for every org with an enabled endpoint, so a
   member removal that fires no signal (FA's bulk ``update()`` paths) or an endpoint left with no owner is turned off
   within one pass, and its "Creator removed" email goes out in the same pass (step 5);
1. revokes previous secrets past their overlap (and removes their ciphertext);
2. re-checks the slow lane of endpoints whose 30 minutes are up;
3. turns off endpoints that have had nothing but failures for 24 h even when no attempt settles any more;
4. computes the gauges (``webhooks.gauges``), stores them for ``/webhooks/ops/stats/`` and logs them as metric lines,
   then evaluates the ops alerts (``webhooks.core.alerts``) and logs each that fires (an error, with its id) or clears;
5. last, sends the "Failing", "Turned off" and "Creator removed" emails (``webhooks.notices``; each send is bounded).

Each step is isolated: one that raises is logged as an error (``webhooks.health.step_failed``, with the step's name and
the error type) and the pass goes on, so a broken email step never skips the gauges and the alerts.

Making sure every dispatcher loop runs is the workflow's FIRST activity (``webhooks_ensure_dispatchers``), which
starts any ``webhooks-dispatcher-<n>`` that is not running, with "use existing"; it does not depend on this pass.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db import transaction

from webhooks import api_offboarding, config, gauges, notices
from webhooks.core import alerts, health
from webhooks.core.schedule import AUTO_DISABLE_AGE_S, AUTO_DISABLE_MIN_FAILURES, _ms
from webhooks.health_state import state_of
from webhooks.models import Endpoint, EndpointState, OpsAlert, OpsSnapshot, Secret
from webhooks.store_rows import dt_of, ms_of
from webhooks.store_settle import pause_backlog, turn_off

log = logging.getLogger("webhooks.health")

BATCH = 500


def sweep_offboarding() -> int:
    """The offboarding sweep for every org with an enabled endpoint; returns how many endpoints it turned off. An org
    whose sweep raises is logged (``webhooks.offboarding.failed``, the error type only) and the other orgs go on."""
    turned = 0
    orgs = (
        Endpoint.objects.filter(deleted=False, status="enabled")
        .order_by("organization_id")
        .values_list("organization_id", flat=True)
        .distinct()
    )
    for org_id in list(orgs):
        try:
            turned += len(api_offboarding.reconcile_org(org_id))
        except Exception as exc:  # noqa: BLE001 - one org never stops the others' sweep
            log.error(
                "webhooks offboarding sweep failed for an org",
                extra={
                    "metric": "webhooks.offboarding.failed",
                    "organization_id": str(org_id),
                    "error_type": type(exc).__name__,
                },
            )
    return turned


def expire_previous_secrets(now: datetime) -> int:
    """Previous secrets past ``expires_at`` become ``revoked``, and their ciphertext is removed (section 5.3)."""
    return Secret.objects.filter(state="previous", expires_at__lte=now).update(
        state="revoked", revoked_at=now, secret_encrypted=None
    )


def recheck_slow_lane(now: datetime, scale: float = 1.0) -> int:
    """Endpoints on the slow lane whose ``slow_until`` passed: back to normal unless the rules still say slow."""
    now_ms = ms_of(now)
    moved = 0
    with transaction.atomic():
        for st in (
            EndpointState.objects.select_for_update(skip_locked=True)
            .filter(lane="slow", slow_until__lte=now)
            .order_by("endpoint_id")[:BATCH]
        ):
            decision = health.decide(state_of(st), now_ms, scale)
            lane, until = health.lane_after(
                st.lane, ms_of(st.slow_until), decision, now_ms
            )
            st.lane, st.slow_until = lane, dt_of(until) if until else None
            st.save(update_fields=["lane", "slow_until"])
            moved += lane == "normal"
    return moved


def disable_idle_failing(now: datetime, scale: float = 1.0) -> int:
    """The 24-hour rule for an endpoint that no attempt settles on any more (its retries are hours apart, or its
    deliveries are exhausted): >= 10 consecutive failures, the first >= 24 h old (section 7.7)."""
    age = timedelta(milliseconds=_ms(AUTO_DISABLE_AGE_S, scale))
    turned: list[str] = []
    with transaction.atomic():
        for st in (
            EndpointState.objects.select_for_update(skip_locked=True)
            .filter(
                consecutive_failed_attempts__gte=AUTO_DISABLE_MIN_FAILURES,
                failing_since__lte=now - age,
                endpoint__status="enabled",
                endpoint__deleted=False,
            )
            .order_by("endpoint_id")[:BATCH]
        ):
            decision = health.decide(state_of(st), ms_of(now), scale)
            if decision["disable_endpoint"] and turn_off(
                st.endpoint_id, "disabled_auto", decision["disable_endpoint"], now
            ):
                turned.append(st.endpoint_id)
    for ep_id in turned:
        pause_backlog(ep_id)
    return len(turned)


def evaluate_alerts(snapshot: dict, now: datetime, scale: float = 1.0) -> list:
    prior = {
        a.id: alerts.AlertState(
            a.active, ms_of(a.since), ms_of(a.fired_at), a.detail or None
        )
        for a in OpsAlert.objects.all()
    }
    states, changes = alerts.evaluate(snapshot, prior, ms_of(now), scale)
    for alert_id, s in states.items():
        fields = {
            "active": s.active,
            "since": dt_of(s.since_ms) if s.since_ms is not None else None,
            "fired_at": dt_of(s.fired_at_ms) if s.fired_at_ms is not None else None,
            "detail": dict(s.detail or {}),
        }
        if any(c.id == alert_id and not c.fired for c in changes):
            fields["cleared_at"] = now
        OpsAlert.objects.update_or_create(id=alert_id, defaults=fields)
    for c in changes:
        extra = {
            "metric": "webhooks.alert",
            "alert": c.id,
            "alert_state": "fired" if c.fired else "cleared",
            **{f"alert_{k}": v for k, v in c.detail.items()},
        }
        if c.fired:
            log.error("webhooks ops alert %s fired: %s", c.id, c.means, extra=extra)
        else:
            log.info("webhooks ops alert %s cleared", c.id, extra=extra)
    return changes


def _log_gauges(snap: dict) -> None:
    for lane in ("normal", "slow"):
        log.info(
            "webhooks backlog",
            extra={
                "metric": "webhooks.backlog.due",
                "lane": lane,
                "value": snap["backlog_due"][lane],
                "oldest_due_seconds": snap["backlog_oldest_due_seconds"][lane],
            },
        )
    log.info(
        "webhooks backlog held by endpoint limits",
        extra={
            "metric": "webhooks.backlog.held_by_endpoint_limits",
            "value": snap["backlog_held_by_endpoint_limits"],
        },
    )
    for b in snap["slots_busy"]:
        log.info("webhooks slots", extra={"metric": "webhooks.slots.busy", **b})
    for e in snap["endpoints"]:
        log.info("webhooks endpoints", extra={"metric": "webhooks.endpoints", **e})
    log.info(
        "webhooks secrets in overlap",
        extra={
            "metric": "webhooks.secrets.in_overlap",
            "value": snap["secrets_in_overlap"],
        },
    )


def _step(out: dict, name: str, fn, *args) -> None:
    """``out[name] = fn(*args)``; a step that raises is logged and recorded, and the pass goes on."""
    try:
        out[name] = fn(*args)
    except Exception as exc:  # noqa: BLE001 - one step never skips the others
        out[name] = None
        out.setdefault("failed_steps", []).append(name)
        log.error(
            "webhooks health step failed: %s",
            name,
            extra={
                "metric": "webhooks.health.step_failed",
                "step": name,
                "error_type": type(exc).__name__,
            },
        )


def run(now: datetime, scale: float = 1.0) -> dict:
    out: dict = {}
    _step(out, "offboarding_turned_off", sweep_offboarding)
    _step(out, "secrets_revoked", expire_previous_secrets, now)
    _step(out, "slow_lane_cleared", recheck_slow_lane, now, scale)
    _step(out, "turned_off_idle", disable_idle_failing, now, scale)
    _step(out, "alerts_changed", stats_and_alerts, now, scale)
    _step(out, "failing_notices", notices.send_failing, now, scale)
    _step(out, "turned_off_notices", notices.send_turned_off, now)
    _step(out, "creator_removed_notices", notices.send_creator_removed, now)
    return out


def stats_and_alerts(now: datetime, scale: float = 1.0) -> list[str]:
    """The gauges, the alerts on them, the stored snapshot and the metric lines; returns the alert changes."""
    snap = gauges.snapshot(now, scale)
    blockers = config.send_blockers()
    snap["sending_blocked"] = bool(blockers)
    snap["sending_blockers"] = blockers
    kept = OpsSnapshot.objects.filter(id="retention").first()
    snap["orgs_over_attempt_quota"] = (kept.data if kept else {}).get(
        "orgs_over_attempt_quota", []
    )
    changes = evaluate_alerts(snap, now, scale)
    snap["alerts"] = {
        a.id: {"active": a.active, "since": a.since and a.since.isoformat()}
        for a in OpsAlert.objects.order_by("id")
    }
    OpsSnapshot.objects.update_or_create(
        id="stats", defaults={"computed_at": now, "data": snap}
    )
    _log_gauges(snap)
    return [f"{c.id}:{'fired' if c.fired else 'cleared'}" for c in changes]
