"""The Postgres ``Store`` adapter (DESIGN.md sections 3, 4, 7 and 13.1; R2, R3, R6, R12).

Fan-out claims pending events with SKIP LOCKED and locks every involved endpoint state row once, in endpoint id
order, so two loops cannot deadlock. Each event is matched, then fanned out, in its own savepoints: an event that
cannot be (malformed ``data``, no builder, a body over 64 KB, a database error on its rows) rolls back alone, is marked
``failed`` with a staff-only reason, and the rest of the batch goes on. Claims, leases and releases are in
:mod:`webhooks.store_claim`; settles in :mod:`webhooks.store_settle`.

Lock order everywhere: delivery rows (SKIP LOCKED, never waited on), then endpoint state rows in endpoint id order.
"""

from __future__ import annotations

import hashlib
import logging

from django.db import transaction
from django.db.models import Q

from webhooks.core import fanout
from webhooks.core.payloads import variant_body
from webhooks.core.redact import log_exception
from webhooks.models import (
    Delivery,
    Endpoint,
    EndpointState,
    Event,
    Payload,
    Subscription,
)
from webhooks.store_claim import ClaimMixin
from webhooks.store_rows import dt_of, insert_row, iso_ms
from webhooks.store_settle import (  # noqa: F401 - re-exported
    SettleMixin,
    disable_endpoint,
    pause_backlog,
)

log = logging.getLogger("webhooks.fanout")


class OrmStore(ClaimMixin, SettleMixin):
    def __init__(self, render=variant_body, scale: float = 1.0):
        self.render = render
        self.scale = scale
        """FI_WEBHOOKS_TIME_SCALE as the adapter decided it: the health windows of a settle (section 7.7)."""

    # ---- fan-out -----------------------------------------------------------------------------------------------

    def fan_out(self, now_ms: int, limit: int) -> int:
        now = dt_of(now_ms)
        with transaction.atomic():
            events = list(
                Event.objects.select_for_update(skip_locked=True)
                .filter(fanout_state="pending")
                .filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
                .order_by("created_at", "id")[:limit]
            )
            if not events:
                return 0
            plans: list[tuple[Event, list[tuple[Endpoint, bool]]]] = []
            involved: dict[str, Endpoint] = {}
            for ev in events:
                try:
                    # Its own savepoint: a database error while matching (a statement or lock timeout) rolls back this
                    # event only and leaves the batch transaction usable, as malformed data does.
                    with transaction.atomic():
                        matched = self._match(ev)
                except Exception as exc:  # noqa: BLE001 - this event only
                    self._poison(ev, exc, now)
                    continue
                plans.append((ev, matched))
                involved.update({ep.id: ep for ep, _ in matched})
            for ep_id in sorted(involved):
                EndpointState.objects.get_or_create(
                    endpoint_id=ep_id,
                    defaults={"organization_id": involved[ep_id].organization_id},
                )
            states = {
                s.endpoint_id: s
                for s in EndpointState.objects.select_for_update()
                .filter(endpoint_id__in=sorted(involved))
                .order_by("endpoint_id")
            }
            for ev, matched in plans:
                before = {
                    ep.id: (states[ep.id].next_seq, states[ep.id].paused_count)
                    for ep, _ in matched
                }
                try:
                    with transaction.atomic():
                        self._fan_one(ev, matched, states, now)
                except Exception as exc:  # noqa: BLE001 - its savepoint is rolled back; the batch goes on
                    for ep_id, (seq, paused) in before.items():
                        states[ep_id].next_seq, states[ep_id].paused_count = seq, paused
                    self._poison(ev, exc, now)
            for st in states.values():
                EndpointState.objects.filter(pk=st.pk).update(
                    next_seq=st.next_seq, paused_count=st.paused_count
                )
        # From when each event became due: its commit, or its deliberate not_before (B8's +5 s, contract 7.2).
        lags = sorted(
            max(
                0.0,
                (
                    now - max(ev.created_at, ev.not_before or ev.created_at)
                ).total_seconds(),
            )
            for ev in events
        )
        log.info(
            "webhooks fan-out pass",
            extra={
                "metric": "webhooks.fanout.lag_seconds",
                "events": len(lags),
                "p50": lags[(len(lags) - 1) // 2],
                "p95": lags[min(len(lags) - 1, int(len(lags) * 0.95))],
                "max": lags[-1],
            },
        )
        return len(events)

    def _fan_one(self, ev: Event, matched, states, now) -> None:
        for ep, targeted in matched:
            st = states[ep.id]
            p = fanout.plan_delivery(
                ep.status, st.next_seq, st.paused_count, ev.is_test
            )
            insert_row(
                Delivery(
                    organization_id=ev.organization_id,
                    event=ev,
                    endpoint=ep,
                    payload=self._payload(ev, ep),
                    seq=p.seq,
                    targeted=targeted,
                    status=p.status,
                    skip_reason=p.skip_reason or "",
                    next_attempt_at=now if p.status == "pending" else None,
                    is_test=ev.is_test,
                )
            )
            st.next_seq, st.paused_count = p.next_seq, p.paused_count
        Event.objects.filter(id=ev.id).update(
            fanout_state="done", fanned_out_at=now, delivery_count=len(matched)
        )

    def _poison(self, ev: Event, exc: Exception, now) -> None:
        """The event cannot be fanned out: ``failed``, with the error type and a short message for staff, logged as an
        error (metric ``webhooks.fanout.failed``), and ``fanned_out_at`` set to when its fan-out ended, which the
        health pass's WA10 counts. Nothing was delivered for it, and it is not retried every pass."""
        reason = f"{type(exc).__name__}: {str(exc)[:150]}"[:200]
        Event.objects.filter(id=ev.id).update(
            fanout_state="failed", fanout_error=reason, fanned_out_at=now
        )
        log_exception(
            log,
            "webhooks event could not be fanned out",
            exc,
            metric="webhooks.fanout.failed",
            event_id=ev.id,
            event_type=ev.type,
        )

    def _match(self, ev: Event) -> list[tuple[Endpoint, bool]]:
        qs = Endpoint.objects.filter(
            organization_id=ev.organization_id, deleted=False, kind="http"
        )
        if ev.is_test:
            qs = qs.filter(id=ev.test_endpoint_id)
        else:
            qs = qs.filter(Q(workspace_id=ev.workspace_id) | Q(workspace__isnull=True))
        subs: dict[str, list[dict]] = {}
        for s in Subscription.objects.filter(endpoint__in=qs).values(
            "endpoint_id", "event_type", "filters"
        ):
            subs.setdefault(s["endpoint_id"], []).append(
                {"event_type": s["event_type"], "filters": s["filters"]}
            )
        facts = {
            "type": ev.type,
            "workspace_id": None if ev.workspace_id is None else str(ev.workspace_id),
            "test": ev.is_test,
            "test_endpoint_id": ev.test_endpoint_id,
            **fanout.facts_from_data(ev.type, ev.data),
        }
        out = []
        for ep in qs.order_by("id"):
            f = dict(facts)
            # Run targeting reaches only endpoints an admin opted in (contract section 5.5).
            f["targeted_endpoint_id"] = (
                ev.targeted_endpoint_id if ep.accepts_run_targeting else None
            )
            m = fanout.match(
                {
                    "id": ep.id,
                    "workspace_id": None
                    if ep.workspace_id is None
                    else str(ep.workspace_id),
                    "subscriptions": subs.get(ep.id, []),
                },
                f,
            )
            if m.deliver:
                out.append((ep, m.targeted))
        return out

    def _payload(self, ev: Event, ep: Endpoint) -> Payload:
        key = {
            "event": ev,
            "payload_mode": ep.payload_mode,
            "api_version": ep.api_version,
            "include_people_names": ep.include_people_names,
        }
        found = Payload.objects.filter(**key).first()
        if found:
            return found
        body = self.render(
            event_id=ev.id,
            type_=ev.type,
            timestamp=iso_ms(ev.occurred_at),
            organization_id=str(ev.organization_id),
            workspace_id=None if ev.workspace_id is None else str(ev.workspace_id),
            data=ev.data,
            builder_input=ev.builder_input,
            variant={
                "payload_mode": ep.payload_mode,
                "include_people_names": ep.include_people_names,
            },
            test=ev.is_test,
        )
        return insert_row(
            Payload(
                organization_id=ev.organization_id,
                body=body,
                body_sha256=hashlib.sha256(body).hexdigest(),
                body_bytes=len(body),
                **key,
            )
        )
