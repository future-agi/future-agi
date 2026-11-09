"""Settling an attempt under its fencing token, and the endpoint health it drives (DESIGN.md sections 6.3, 7.2, 7.7
and 13.1, R2). A stale settle writes only its own attempt row, as ``superseded``.

Lock order: the settled delivery's own row, then its endpoint's state row; the endpoint row is only updated. Turning
an endpoint off (410, three ``ssrf_blocked`` in a row, a policy block) flips its status inside the settle; its due
backlog is paused AFTER the commit, in SKIP LOCKED batches (:func:`pause_backlog`). The settle never touches another
delivery's row, so it cannot deadlock with a claim that holds those rows and waits for the state row. Rows a claim
holds are paused by that claim: it reads the endpoint again once it has the state row, which every turn-off holds
until it commits (``store_claim._take_slot``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest

from webhooks import audit
from webhooks.core import health
from webhooks.core.fanout import BACKLOG_CAP
from webhooks.core.ports import Claim
from webhooks.health_state import state_of, write_state
from webhooks.models import ApiAction, Attempt, Delivery, Endpoint, EndpointState
from webhooks.store_rows import dt_of, insert_row, ms_of, write_row

log = logging.getLogger("webhooks.store")

PAUSE_BATCH = 500


class SettleMixin:
    def settle(self, claim: Claim, attempt: Mapping, action: Mapping) -> bool:
        now = dt_of(attempt.get("settled_at_ms") or attempt["started_at_ms"])
        if action["action"] == "block_by_policy":
            return self._settle_policy_block(claim, action, now)
        with transaction.atomic():
            row = insert_row(
                Attempt(
                    organization_id=claim.endpoint.organization_id,
                    delivery_id=claim.delivery_id,
                    n=attempt["n"],
                    trigger=attempt["trigger"],
                    actor_id=_asked_by(claim.delivery_id, attempt),
                    t=attempt.get("t"),
                    signature_key_tails=attempt.get("signature_key_tails") or [],
                    started_at=dt_of(attempt["started_at_ms"]),
                    dns_ms=attempt.get("dns_ms"),
                    ttfb_ms=attempt.get("ttfb_ms"),
                    duration_ms=attempt.get("duration_ms"),
                    resolved_ip=attempt.get("resolved_ip"),
                    ip_family=attempt.get("ip_family"),
                    response_status=attempt.get("response_status"),
                    response_headers=attempt.get("response_headers") or {},
                    response_body_excerpt=attempt.get("response_body_excerpt"),
                    outcome=attempt["outcome"],
                    error_detail=(attempt.get("error_detail") or "")[:300],
                    staff_detail=(attempt.get("staff_detail") or "")[:500],
                    settled_at=now,
                )
            )
            fields = {
                "status": action["delivery_status"],
                "retry_class": action.get("retry_class") or "",
                "final_outcome": action.get("final_outcome") or "",
                "lease_token": None,
                "lease_until": None,
                "next_attempt_at": dt_of(action["retry_at_ms"])
                if action["action"] == "retry"
                else None,
            }
            if action["delivery_status"] == "succeeded":
                fields["succeeded_at"] = now
            won = Delivery.objects.filter(
                id=claim.delivery_id, lease_token=claim.lease_token
            ).update(**fields)
            if not won:
                # A stale sender (stopped and resumed, or a lost renewal): only its own row, never the delivery or
                # the endpoint's health (R2).
                Attempt.objects.filter(id=row.id).update(
                    outcome="superseded", observed_outcome=attempt["outcome"]
                )
                return False
            if row.n > 4:
                Attempt.objects.filter(
                    delivery_id=claim.delivery_id, n=row.n - 1
                ).update(response_body_excerpt=None)
            if attempt.get("request_headers"):
                # Once per delivery; per-attempt values (t, attempt) live on the attempt rows.
                Delivery.objects.filter(
                    id=claim.delivery_id, request_headers={}
                ).update(request_headers=dict(attempt["request_headers"]))
            turned_off = self._health(claim, attempt, action, now)
            if action["action"] == "retry" and not claim.is_test:
                self._park_if_off(claim, turned_off)
        if turned_off:
            pause_backlog(claim.endpoint.id)
        return True

    def _park_if_off(self, claim: Claim, turned_off: bool) -> None:
        """A retry whose endpoint is off by now (this settle turned it off, or another did while this attempt was in
        flight, which ``pause_backlog`` could not see): paused at once, within the backlog cap, instead of waiting to
        be set aside at its next claim; cancelled when the endpoint was deleted (section 7.7). The state row is
        already locked by this settle."""
        ep = (
            Endpoint.objects.filter(id=claim.endpoint.id)
            .values_list("status", "deleted")
            .first()
        )
        if ep is None or ep[1]:
            Delivery.objects.filter(id=claim.delivery_id, status="retrying").update(
                status="cancelled",
                final_outcome="endpoint_deleted",
                next_attempt_at=None,
            )
            return
        if ep[0] == "enabled" and not turned_off:
            return
        room = EndpointState.objects.filter(
            endpoint_id=claim.endpoint.id, paused_count__lt=BACKLOG_CAP
        ).update(paused_count=F("paused_count") + 1)
        Delivery.objects.filter(id=claim.delivery_id, status="retrying").update(
            status="paused" if room else "skipped",
            skip_reason="" if room else "backlog_cap",
            next_attempt_at=None,
        )

    def _settle_policy_block(
        self, claim: Claim, action: Mapping, now: datetime
    ) -> bool:
        """The URL is outside the org or workspace allowlist now (section 6.3): nothing was sent, so no attempt row
        and the attempt count goes back; the delivery is paused with its sequence number (skipped with ``backlog_cap``
        past the 10,000 cap) and the endpoint becomes ``blocked_by_policy``. Never counts toward ``unsafe_address``."""
        with transaction.atomic():
            won = Delivery.objects.filter(
                id=claim.delivery_id, lease_token=claim.lease_token
            ).update(
                status="paused",
                lease_token=None,
                lease_until=None,
                next_attempt_at=None,
                attempts_count=Greatest(F("attempts_count") - 1, 0),
            )
            if not won:
                return False
            # The state row after the delivery row (the one lock order), then the 10,000 cap (R8) on it.
            paused = (
                EndpointState.objects.select_for_update()
                .filter(endpoint_id=claim.endpoint.id)
                .values_list("paused_count", flat=True)
                .first()
            )
            room = paused is None or paused < BACKLOG_CAP
            EndpointState.objects.filter(endpoint_id=claim.endpoint.id).update(
                in_flight=Greatest(F("in_flight") - 1, 0),
                paused_count=F("paused_count") + (1 if room else 0),
            )
            if not room:
                Delivery.objects.filter(id=claim.delivery_id).update(
                    status="skipped", skip_reason="backlog_cap"
                )
            turned_off = turn_off(
                claim.endpoint.id,
                action["disabled_status"],
                action["disabled_reason"],
                now,
            )
        if turned_off:
            pause_backlog(claim.endpoint.id)
        return True

    def _health(
        self, claim: Claim, attempt: Mapping, action: Mapping, now: datetime
    ) -> bool:
        """Health on the state row (section 7.7): fold this attempt, decide, move the lane; True when this settle
        turned the endpoint off (410, three ``ssrf_blocked`` in a row, or 24 h of nothing but failures)."""
        scale = getattr(self, "scale", 1.0)
        st = EndpointState.objects.select_for_update().get(
            endpoint_id=claim.endpoint.id
        )
        st.in_flight = max(0, st.in_flight - 1)
        now_ms = ms_of(now)
        rec = {
            "outcome": attempt["outcome"],
            "at_ms": now_ms,
            "duration_ms": attempt.get("duration_ms"),
            "test": claim.is_test,
        }
        decision = None
        if health.counts(rec):
            hs = health.fold(state_of(st), rec, scale)
            write_state(st, hs)
            st.last_outcome = attempt["outcome"]
            st.last_status_code = attempt.get("response_status")
            if attempt["outcome"] == "success":
                st.last_success_at = now
            decision = health.decide(hs, now_ms, scale)
            lane, until = health.lane_after(
                st.lane, ms_of(st.slow_until), decision, now_ms
            )
            if lane == "slow" and st.lane != "slow":
                log.warning(
                    "webhooks endpoint moved to the slow lane",
                    extra={
                        "metric": "webhooks.endpoint.slow_lane",
                        "endpoint_id": claim.endpoint.id,
                    },
                )
            st.lane, st.slow_until = lane, dt_of(until) if until else None
            if action.get("rate_halved_until_ms"):
                st.rate_halved_until = dt_of(action["rate_halved_until_ms"])
        if action["delivery_status"] == "paused":
            # This delivery (pause_backlog counts the rest), within the 10,000 cap (R8) on the locked state row; past
            # it, skipped with backlog_cap, keeping its sequence number.
            if st.paused_count < BACKLOG_CAP:
                st.paused_count += 1
            else:
                Delivery.objects.filter(id=claim.delivery_id, status="paused").update(
                    status="skipped", skip_reason="backlog_cap", next_attempt_at=None
                )
        write_row(st)
        reason = (
            action.get("disabled_reason")
            if action["action"] == "disable_endpoint"
            else None
        )
        if reason is None and decision is not None:
            reason = decision["disable_endpoint"]
        if not reason:
            return False
        return turn_off(claim.endpoint.id, "disabled_auto", reason, now)


def turn_off(endpoint_id: str, status: str, reason: str, now: datetime) -> bool:
    """Flip an ENABLED endpoint to ``disabled_auto`` or ``blocked_by_policy``; True when it changed. The endpoint row
    and, when it changed, its audit entry (:func:`_audit_turn_off`) are written; call :func:`pause_backlog` after the
    commit. The "turned off" email goes out from the health pass (``webhooks.notices``: ``disabled_at`` newer than the
    state row's ``last_disabled_notice_at``), never inside this transaction."""
    changed = bool(
        Endpoint.objects.filter(id=endpoint_id, status="enabled").update(
            status=status, disabled_reason=reason, disabled_at=now
        )
    )
    if changed:
        log.warning(
            "webhooks endpoint turned off",
            extra={
                "metric": "webhooks.endpoint.turned_off",
                "endpoint_id": endpoint_id,
                "status": status,
                "reason": reason,
            },
        )
        _audit_turn_off(endpoint_id, status, reason)
    return changed


def _asked_by(delivery_id: str, attempt: Mapping):
    """The person who asked for a redelivered attempt (section 4: ``actor_id`` with trigger ``redeliver``), from WH-2's
    ledger row for that attempt number; None for every other attempt, which no query is made for."""
    if attempt["trigger"] != "redeliver":
        return None
    return (
        ApiAction.objects.filter(
            delivery_id=delivery_id, kind="redeliver", attempt_n=attempt["n"]
        )
        .values_list("actor_id", flat=True)
        .first()
    )


def _audit_turn_off(endpoint_id: str, status: str, reason: str) -> None:
    """The audit entry of a turn-off the system made (section 9.2), with no actor, in the caller's transaction:
    ``webhook.endpoint.auto_disabled`` with the rule that fired, the last status and since when it fails, or
    ``webhook.endpoint.blocked_by_policy`` for a send the policy refused. In a savepoint: FA's ``log_audit`` swallows
    its own INSERT failure, which would otherwise leave the settle's transaction aborted; a failed entry is logged and
    never undoes the turn-off."""
    st = (
        EndpointState.objects.filter(endpoint_id=endpoint_id)
        .values("last_status_code", "last_outcome", "failing_since")
        .first()
        or {}
    )
    since = st.get("failing_since")
    details = {
        "last_status": st.get("last_status_code"),
        "last_outcome": st.get("last_outcome") or None,
        "failing_since": since.isoformat() if since else None,
    }
    try:
        with transaction.atomic():
            if status == "disabled_auto":
                audit.record_auto_disabled(endpoint_id, reason=reason, details=details)
            elif status == "blocked_by_policy":
                ep = Endpoint.objects.select_related("organization").get(id=endpoint_id)
                audit.record(
                    organization=ep.organization,
                    action=audit.ENDPOINT_BLOCKED,
                    target=endpoint_id,
                    system=True,
                    changes={"status": {"before": "enabled", "after": status}},
                    metadata={"reason": reason, "actor": "system", **details},
                )
    except Exception as exc:  # noqa: BLE001 - the entry never undoes the turn-off
        log.error(
            "webhooks turn-off audit entry not written",
            extra={
                "metric": "webhooks.audit.failed",
                "endpoint_id": endpoint_id,
                "error_type": type(exc).__name__,
            },
        )


def pause_backlog(endpoint_id: str, batch: int = PAUSE_BATCH) -> int:
    """Pause the due deliveries of an endpoint that is not enabled, in their own short transactions, SKIP LOCKED:
    a row a claim holds is skipped here and paused by that claim. Tests are never paused. Stops if the endpoint is
    enabled again. Returns how many were paused (``paused_count`` grows by as many)."""
    total = 0
    while True:
        with transaction.atomic():
            if Endpoint.objects.filter(id=endpoint_id, status="enabled").exists():
                return total
            ids = list(
                Delivery.objects.select_for_update(skip_locked=True)
                .filter(
                    endpoint_id=endpoint_id,
                    status__in=("pending", "retrying"),
                    is_test=False,
                )
                .order_by("id")
                .values_list("id", flat=True)[:batch]
            )
            if not ids:
                return total
            # The state row after the delivery rows (the one lock order); its paused_count holds the 10,000 cap
            # (R8): rows past it are skipped with backlog_cap and keep their sequence numbers.
            st = (
                EndpointState.objects.select_for_update()
                .filter(endpoint_id=endpoint_id)
                .first()
            )
            room = max(0, BACKLOG_CAP - st.paused_count) if st else len(ids)
            keep, over = ids[:room], ids[room:]
            moved = Delivery.objects.filter(id__in=keep).update(
                status="paused", next_attempt_at=None
            )
            if over:
                Delivery.objects.filter(id__in=over).update(
                    status="skipped", skip_reason="backlog_cap", next_attempt_at=None
                )
            EndpointState.objects.filter(endpoint_id=endpoint_id).update(
                paused_count=F("paused_count") + moved
            )
            total += moved
        if len(ids) < batch:
            return total


def disable_endpoint(endpoint_id: str, reason: str, now: datetime) -> int:
    """``disabled_auto`` with ``reason``, then its backlog paused (section 7.7). For callers outside any claim or
    settle transaction (the API, WH-2); returns how many deliveries were paused.

    The state row is locked first, as a settle does (one lock order: state row, then endpoint row). A claim waiting on
    it reads the endpoint after this commits and pauses its rows; a claim that holds it finishes its leases first."""
    with transaction.atomic():
        list(
            EndpointState.objects.select_for_update()
            .filter(endpoint_id=endpoint_id)
            .values_list("endpoint_id", flat=True)
        )
        turn_off(endpoint_id, "disabled_auto", reason, now)
    return pause_backlog(endpoint_id)
