"""Claims, leases, renewals and releases on Postgres (DESIGN.md sections 7.8 and 13.1; R2, R3, R6).

**Ranking.** Candidates are read without a lock, bounded per endpoint by what the endpoint can take now: its free
in-flight count and the send starts its rate allows within the burst window (an endpoint at capacity contributes no
row, so one endpoint's backlog never holds back the org's other endpoints), then ranked per org in a window; Postgres refuses ``FOR UPDATE`` with window
functions, so the chosen ids are then locked with ``FOR UPDATE SKIP LOCKED``. Endpoints that are off or deleted
contribute a bounded batch so their due rows are paused or cancelled. Before a row is leased, its endpoint is read
again under the endpoint's state-row lock, so a turn-off that committed while the claim waited is honoured.

**Leases** use the database clock (``now()``), so a worker's clock skew cannot reap a live lease. **The send rate**
does too: each leased delivery reserves a send start on the state row's ``rate_tat`` (``webhooks.core.ratelimit``),
so starts to one endpoint keep ``1 / rate`` apart across every loop and replica (WT-20).

**Claims are built after the commit**: secrets, policies and history are read and decrypted outside the locks. A
delivery whose configuration does not decrypt (WA6) is released on its own, with an ops alert, and never stops the
others.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.db.models import F
from django.db.models.functions import Coalesce, Greatest, Now

from webhooks.core import fanout, ratelimit, schedule
from webhooks.core.dispatch import UNSENDABLE_RETRY_MS
from webhooks.core.ports import Claim
from webhooks.models import ApiAction, Delivery, Endpoint, EndpointState
from webhooks.store_rows import dt_of, endpoint_config, history, ms_of

log = logging.getLogger("webhooks.store")

OFF_ENDPOINT_BATCH = 100
"""Due rows of an endpoint that is off or deleted taken per pass, to be paused or cancelled."""
CANDIDATE_LIMIT = 2000

CANDIDATES_SQL = """
WITH st AS (
  SELECT s.endpoint_id, s.in_flight, e.max_in_flight, e.deleted, e.status,
         GREATEST(COALESCE(s.rate_tat, now()), now()) AS base,
         CASE WHEN s.rate_halved_until > %(now)s THEN GREATEST(0.5, e.rate_limit_per_s / 2.0)
              ELSE e.rate_limit_per_s::float8 END AS rate
  FROM webhooks_endpoint_state s
  JOIN webhooks_endpoint e ON e.id = s.endpoint_id
  WHERE (CASE WHEN s.lane = 'slow' AND s.slow_until > %(now)s THEN 'slow' ELSE 'normal' END) = %(lane)s
),
room AS (
  SELECT endpoint_id,
         CASE WHEN deleted OR status <> 'enabled' THEN %(off_batch)s
              WHEN base > now() + make_interval(secs => %(burst_s)s::float8) THEN 0
              ELSE LEAST(max_in_flight - in_flight,
                         FLOOR(EXTRACT(EPOCH FROM (now() + make_interval(secs => %(burst_s)s::float8) - base)) * rate)::int + 1)
         END AS n
  FROM st
),
cand AS (
  SELECT d.id, d.organization_id, d.next_attempt_at
  FROM room
  CROSS JOIN LATERAL (
    SELECT d.id, d.organization_id, d.next_attempt_at
    FROM webhooks_delivery d
    WHERE d.endpoint_id = room.endpoint_id AND d.status IN ('pending', 'retrying')
      AND d.next_attempt_at <= %(now)s
    ORDER BY d.next_attempt_at, d.id
    LIMIT GREATEST(room.n, 0)
  ) d
  WHERE room.n > 0
),
ranked AS (
  SELECT id, organization_id::text AS org, next_attempt_at,
         row_number() OVER (PARTITION BY organization_id ORDER BY next_attempt_at, id) AS rn
  FROM cand
)
SELECT id, org, rn FROM ranked WHERE rn <= %(per_org)s ORDER BY next_attempt_at, id LIMIT %(limit)s
"""


class ClaimMixin:
    def claim_due(
        self,
        now_ms: int,
        lane: str,
        slots: int,
        per_org: int,
        org_busy: Mapping[str, int] | None = None,
    ) -> list[Claim]:
        now = dt_of(now_ms)
        busy = dict(org_busy or {})
        with connection.cursor() as cur:
            cur.execute(
                CANDIDATES_SQL,
                {
                    "now": now,
                    "lane": lane,
                    "off_batch": OFF_ENDPOINT_BATCH,
                    "burst_s": ratelimit.BURST_MS / 1000,
                    "per_org": per_org,
                    "limit": CANDIDATE_LIMIT,
                },
            )
            # The org share counts this loop's in-flight attempts too (section 7.8, R6).
            ids = [r[0] for r in cur.fetchall() if r[2] <= per_org - busy.get(r[1], 0)]
        ids = ids[: slots + OFF_ENDPOINT_BATCH]
        if not ids:
            return []
        leased: list[tuple[str, str, int, int]] = []
        with transaction.atomic():
            rows = list(
                Delivery.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("endpoint")
                .filter(id__in=ids, status__in=("pending", "retrying"))
                .order_by("endpoint_id", "next_attempt_at", "id")
            )
            for d in rows:
                ep = d.endpoint
                if self._set_aside(d, ep) or len(leased) >= slots:
                    continue
                slot = self._take_slot(ep, now, d.is_test)
                if slot is None:
                    # Turned off or deleted after the statement above read it (seen under the state-row lock).
                    self._set_aside(d, ep)
                    continue
                wait_ms, start_delay_ms = slot
                if wait_ms:
                    Delivery.objects.filter(id=d.id).update(
                        next_attempt_at=now + timedelta(milliseconds=wait_ms)
                    )
                    continue
                leased.append((*self._lease_row(d, ep), start_delay_ms))
        return self._claims(leased, now, now_ms)

    def _set_aside(self, d: Delivery, ep: Endpoint) -> bool:
        """A due row that is not sent now: cancelled when its endpoint is deleted; paused when the endpoint is off
        (tests excepted), keeping its sequence number and counting toward the backlog cap; skipped with
        ``backlog_cap`` once 10,000 are paused (section 7.7, R8). True when the
        row was set aside."""
        if ep.deleted:
            Delivery.objects.filter(id=d.id).update(
                status="cancelled",
                final_outcome="endpoint_deleted",
                next_attempt_at=None,
            )
            return True
        if ep.status != "enabled" and not d.is_test:
            # Counted against the 10,000 cap in one conditional UPDATE on the state row (R8); past it, skipped.
            room = EndpointState.objects.filter(
                endpoint_id=ep.id, paused_count__lt=fanout.BACKLOG_CAP
            ).update(paused_count=F("paused_count") + 1)
            Delivery.objects.filter(id=d.id).update(
                status="paused" if room else "skipped",
                skip_reason="" if room else "backlog_cap",
                next_attempt_at=None,
            )
            return True
        return False

    def _take_slot(
        self, ep: Endpoint, now: datetime, is_test: bool
    ) -> tuple[int, int] | None:
        """In-flight and the send rate on the endpoint's state row, under its row lock (R6). ``(0, start_delay_ms)``
        = taken, with the reserved send start as a delay from now; ``(wait_ms, 0)`` = not now, and when one could
        be; None when the endpoint is off (for a real delivery) or deleted by now.

        The endpoint is read again under that lock, and ``ep`` is updated in place. The status the claim statement read
        can be stale by the time the lock is granted: a settle that turns the endpoint off (410, a third
        ``ssrf_blocked``, a policy block) and :func:`~webhooks.store_settle.disable_endpoint` both hold this row until
        they commit. Under READ COMMITTED this read sees what they committed, so the caller pauses the row instead of
        leasing it to an endpoint that is off.

        The rate is reserved on the database clock (``clock_timestamp()``, read once the lock is held), so loops and
        replicas with different clocks share one spacing."""
        EndpointState.objects.get_or_create(
            endpoint_id=ep.id, defaults={"organization_id": ep.organization_id}
        )
        st = EndpointState.objects.select_for_update().get(endpoint_id=ep.id)
        fresh = (
            Endpoint.objects.filter(id=ep.id)
            .values_list("status", "deleted", "max_in_flight", "rate_limit_per_s")
            .first()
        )
        if fresh is None:
            ep.deleted = True
        else:
            ep.status, ep.deleted, ep.max_in_flight, ep.rate_limit_per_s = fresh
        if ep.deleted or (ep.status != "enabled" and not is_test):
            return None
        if st.in_flight >= ep.max_in_flight:
            return 1000, 0
        halved = bool(st.rate_halved_until and st.rate_halved_until > now)
        rate = ratelimit.effective_rate(ep.rate_limit_per_s, halved)
        with connection.cursor() as cur:
            cur.execute("SELECT clock_timestamp()")
            db_now_ms = ms_of(cur.fetchone()[0])
        start, tat, wait_ms = ratelimit.reserve(ms_of(st.rate_tat), db_now_ms, rate)
        if start is None:
            return wait_ms, 0
        st.rate_tat, st.in_flight = dt_of(int(tat)), st.in_flight + 1
        # An UPDATE, not save(): no FA save receivers on the hot path (store_rows.insert_row).
        EndpointState.objects.filter(pk=st.pk).update(
            rate_tat=st.rate_tat, in_flight=st.in_flight
        )
        return 0, max(0, int(start - db_now_ms))

    def _lease_row(
        self, d: Delivery, ep: Endpoint, count: bool = True
    ) -> tuple[str, str, int]:
        """A fresh fencing token and ``lease_until = now() + 2 x (timeout + 5 s)`` on the database clock."""
        token = str(uuid.uuid4())
        n = d.attempts_count + 1 if count else d.attempts_count
        Delivery.objects.filter(id=d.id).update(
            lease_token=token,
            lease_until=Now()
            + timedelta(milliseconds=schedule.lease_ms(ep.timeout_ms)),
            status="sending",
            attempts_count=n,
            first_attempt_at=Coalesce(F("first_attempt_at"), Now()),
        )
        return d.id, token, n

    def reap_expired(self, now_ms: int) -> list[Claim]:
        """Leases past ``lease_until`` on the database clock, re-leased to this caller to write ``worker_lost``."""
        with transaction.atomic():
            rows = list(
                Delivery.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("endpoint")
                .filter(status="sending", lease_until__lt=Now())
                .order_by("lease_until")[:100]
            )
            # The lost attempt is attempt n itself: its worker_lost row is written by this claimer.
            leased = [(*self._lease_row(d, d.endpoint, count=False), 0) for d in rows]
        # The reaper never sends, so nothing is decrypted.
        return self._claims(leased, dt_of(now_ms), now_ms, decrypt=False)

    def _claims(
        self,
        leased: list[tuple[str, str, int, int]],
        now: datetime,
        now_ms: int,
        decrypt: bool = True,
    ) -> list[Claim]:
        if not leased:
            return []
        rows = {
            d.id: d
            for d in Delivery.objects.select_related(
                "endpoint", "event", "payload"
            ).filter(id__in=[x[0] for x in leased])
        }
        # Attempts a person asked for (WH-2's ledger, ``ApiAction`` kind ``redeliver``), one query per batch: their
        # attempt rows say ``redeliver`` (section 4), and the settle names the person (``store_settle``).
        redelivered = set(
            ApiAction.objects.filter(
                kind="redeliver", delivery_id__in=list(rows)
            ).values_list("delivery_id", "attempt_n")
        )
        out: list[Claim] = []
        for delivery_id, token, n, start_delay_ms in leased:
            d = rows[delivery_id]
            try:
                out.append(
                    self._claim_of(
                        d,
                        token,
                        n,
                        now,
                        decrypt,
                        start_delay_ms,
                        redelivered=(delivery_id, n) in redelivered,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - WA6: this delivery only; never its secrets in the log
                log.error(
                    "webhooks delivery configuration could not be read",
                    extra={
                        "metric": "webhooks.delivery.unsendable",
                        "reason": "config_unreadable",
                        "error_type": type(exc).__name__,
                        "delivery_id": delivery_id,
                        "endpoint_id": d.endpoint_id,
                        "alert": "WA6",
                    },
                )
                self._release_row(delivery_id, token, now_ms + UNSENDABLE_RETRY_MS)
        return out

    def _claim_of(
        self,
        d: Delivery,
        token: str,
        n: int,
        now: datetime,
        decrypt: bool,
        start_delay_ms: int = 0,
        redelivered: bool = False,
    ) -> Claim:
        return Claim(
            delivery_id=d.id,
            lease_token=token,
            lease_until_ms=ms_of(d.lease_until),
            event_id=d.event_id,
            event_type=d.event.type,
            seq=d.seq,
            is_test=d.is_test,
            attempt_n=n,
            trigger="test" if d.is_test else "redeliver" if redelivered else "auto",
            created_at_ms=ms_of(d.created_at),
            body=bytes(d.payload.body) if d.payload.body is not None else b"",
            endpoint=endpoint_config(d.endpoint, now, decrypt=decrypt),
            history=history(d.id),
            start_delay_ms=start_delay_ms,
            occurred_at_ms=ms_of(d.event.occurred_at),
        )

    def renew(self, delivery_id: str, lease_token: str, lease_until_ms: int) -> bool:
        # The caller's horizon, applied on the database clock.
        ahead_ms = max(0, lease_until_ms - time.time_ns() // 1_000_000)
        return bool(
            Delivery.objects.filter(
                id=delivery_id, lease_token=lease_token, status="sending"
            ).update(lease_until=Now() + timedelta(milliseconds=ahead_ms))
        )

    def release(self, claim: Claim, retry_at_ms: int, reason: str) -> bool:
        return self._release_row(claim.delivery_id, claim.lease_token, retry_at_ms)

    def _release_row(self, delivery_id: str, token: str, retry_at_ms: int) -> bool:
        """No attempt was made: the attempt count and the in-flight count go back, and the delivery is due again at
        ``retry_at_ms``, or ``failed`` with ``max_age_exceeded`` past its max age. Own row, then the state row."""
        with transaction.atomic():
            d = (
                Delivery.objects.select_for_update(of=("self",))
                .select_related("endpoint")
                .filter(id=delivery_id, lease_token=token)
                .first()
            )
            if d is None:
                return False
            n = max(0, d.attempts_count - 1)
            fields: dict = {
                "attempts_count": n,
                "lease_token": None,
                "lease_until": None,
            }
            if dt_of(retry_at_ms) > d.created_at + timedelta(
                seconds=d.endpoint.max_age_s
            ):
                fields.update(
                    status="failed",
                    final_outcome="max_age_exceeded",
                    next_attempt_at=None,
                )
            else:
                fields.update(
                    status="retrying" if n else "pending",
                    next_attempt_at=dt_of(retry_at_ms),
                )
            Delivery.objects.filter(id=d.id).update(**fields)
            EndpointState.objects.filter(endpoint_id=d.endpoint_id).update(
                in_flight=Greatest(F("in_flight") - 1, 0)
            )
            return True
