"""``webhooks-retention`` (daily, cron ``30 3 * * *``): DESIGN.md section 13.4, R8 and R12.

| Data | Kept | Then |
|---|---|---|
| payload bodies | 30 days | dropped; ``body_sha256`` and ``body_bytes`` kept |
| events with zero deliveries | 7 days | deleted |
| events, deliveries, attempts | the org's ``log_retention_days`` (default 90, 7-365) | deleted |
| secrets | until revoked, expired or the endpoint deleted | deleted |
| ``cli_listen`` endpoints | 24 h after the last poll | deleted, with their deliveries |
| attempt excerpts | the org's newest 5 million attempt rows | the oldest excerpts dropped (WA9) |
| the API's ledger (``webhooks_api_action``): writes, tests, bulk redeliveries | 1 day | deleted |
| the API's ledger: ``redeliver`` rows (who asked for an attempt) | with their delivery | deleted with it |

Every purge runs in chunks of at most 1,000 rows per statement, each chunk in its own short transaction, oldest first
(the ``host_events`` ``_PURGE_CHUNK`` pattern), never as one long transaction. A paused delivery that is deleted gives
its place in the 10,000 backlog cap back (``paused_count``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, F, Q
from django.db.models.functions import Greatest

from webhooks.core import lifecycle
from webhooks.core.alerts import ATTEMPT_LOG_QUOTA
from webhooks.models import (
    ApiAction,
    Attempt,
    Delivery,
    Endpoint,
    EndpointState,
    Event,
    OpsSnapshot,
    Payload,
    Policy,
    Secret,
)
from webhooks.store_rows import dt_of, ms_of

log = logging.getLogger("webhooks.retention")

CHUNK = lifecycle.PURGE_CHUNK
EVENT_CHUNK = 100
"""Events per purge transaction; their deliveries and attempts go first, each statement at most ``CHUNK`` rows."""

API_LEDGER_KEEP_S = 86_400
"""WH-2's ledger rows for writes, tests and bulk redeliveries feed only the API's rate limits, whose longest window is
the bulk redelivery's hour; a day is kept. ``redeliver`` rows name who asked for an attempt and go with their
delivery."""
API_LEDGER_KINDS = ("write", "test", "bulk_redeliver")


def _ids(qs, n: int = CHUNK) -> list:
    return list(qs.values_list("id", flat=True)[:n])


def drop_bodies(cutoff: datetime) -> int:
    """Payload bodies of events created before ``cutoff``; their hash and size stay."""
    total = 0
    while True:
        with transaction.atomic():
            ids = _ids(
                Payload.objects.filter(
                    body__isnull=False, event__created_at__lt=cutoff
                ).order_by("created_at")
            )
            if not ids:
                return total
            total += Payload.objects.filter(id__in=ids).update(body=None)


def delete_events(event_ids: list[str]) -> dict:
    """Delete these events with their attempts, deliveries and payloads, bottom up, in chunks. Paused deliveries give
    their backlog places back."""
    counts = {"attempts": 0, "deliveries": 0, "payloads": 0, "events": 0}
    while True:
        with transaction.atomic():
            ids = _ids(Attempt.objects.filter(delivery__event_id__in=event_ids))
            if not ids:
                break
            counts["attempts"] += Attempt.objects.filter(id__in=ids).delete()[0]
    while True:
        with transaction.atomic():
            rows = list(
                Delivery.objects.filter(event_id__in=event_ids).values_list(
                    "id", "endpoint_id", "status"
                )[:CHUNK]
            )
            if not rows:
                break
            paused: dict[str, int] = {}
            for _id, ep, status in rows:
                if status == "paused":
                    paused[ep] = paused.get(ep, 0) + 1
            for ep in sorted(paused):
                EndpointState.objects.filter(endpoint_id=ep).update(
                    paused_count=Greatest(F("paused_count") - paused[ep], 0)
                )
            # The deliveries' own count: Django's CASCADE also deletes their ledger rows (``redeliver``).
            counts["deliveries"] += (
                Delivery.objects.filter(id__in=[r[0] for r in rows])
                .delete()[1]
                .get(Delivery._meta.label, 0)
            )
    with transaction.atomic():
        counts["payloads"] += Payload.objects.filter(event_id__in=event_ids).delete()[0]
        counts["events"] += Event.objects.filter(id__in=event_ids).delete()[0]
    return counts


def purge_events(qs) -> dict:
    """Delete every event of ``qs`` (ordered oldest first), ``EVENT_CHUNK`` at a time."""
    total = {"attempts": 0, "deliveries": 0, "payloads": 0, "events": 0}
    while True:
        ids = _ids(qs.order_by("created_at", "id"), EVENT_CHUNK)
        if not ids:
            return total
        for k, v in delete_events(ids).items():
            total[k] += v


def org_log_days(org_id) -> int:
    p = Policy.objects.filter(organization_id=org_id, workspace__isnull=True).first()
    return lifecycle.clamp_log_days(p.log_retention_days if p else None)


def purge_secrets(now: datetime) -> int:
    """Revoked secrets, previous secrets past their overlap, and every secret of a deleted endpoint."""
    total = 0
    q = (
        Q(state="revoked")
        | Q(state="previous", expires_at__lte=now)
        | Q(endpoint__deleted=True)
    )
    while True:
        with transaction.atomic():
            ids = _ids(Secret.objects.filter(q).order_by("id"))
            if not ids:
                return total
            total += Secret.objects.filter(id__in=ids).delete()[0]


def purge_api_ledger(cutoff: datetime) -> int:
    """The API ledger's write, test and bulk-redelivery rows created before ``cutoff``, in chunks."""
    total = 0
    while True:
        with transaction.atomic():
            ids = _ids(
                ApiAction.objects.filter(
                    kind__in=API_LEDGER_KINDS, created_at__lt=cutoff
                ).order_by("id")
            )
            if not ids:
                return total
            total += ApiAction.objects.filter(id__in=ids).delete()[0]


def purge_listen_endpoints(cutoff: datetime) -> int:
    """``cli_listen`` endpoints whose session expired more than 24 h ago, with their deliveries (WH-4b's kind)."""
    n = 0
    for ep_id in Endpoint.objects.filter(
        kind="cli_listen", listen_expires_at__lt=cutoff
    ).values_list("id", flat=True):
        while True:
            with transaction.atomic():
                ids = _ids(Attempt.objects.filter(delivery__endpoint_id=ep_id))
                if not ids:
                    break
                Attempt.objects.filter(id__in=ids).delete()
        while True:
            with transaction.atomic():
                ids = _ids(Delivery.objects.filter(endpoint_id=ep_id))
                if not ids:
                    break
                Delivery.objects.filter(id__in=ids).delete()
        with transaction.atomic():
            Endpoint.objects.filter(id=ep_id).delete()
        n += 1
    return n


def _older_than(settled_at, attempt_id) -> Q:
    """Attempt rows before ``(settled_at, id)`` in ``order_by("settled_at", "id")`` order (Postgres sorts a null
    ``settled_at`` last, so such a row is the newest)."""
    if settled_at is None:
        return Q(settled_at__isnull=False) | Q(
            settled_at__isnull=True, id__lt=attempt_id
        )
    return Q(settled_at__lt=settled_at) | Q(settled_at=settled_at, id__lt=attempt_id)


def attempt_quota(quota: int = ATTEMPT_LOG_QUOTA) -> list[str]:
    """Orgs past the attempt-log quota. The org's newest ``quota`` rows keep their excerpts; every older row loses its
    excerpt, oldest first (section 7.8). The cut is the oldest row of the newest ``quota``, so a later pass with no new
    rows changes nothing, and one with new rows moves the cut only by as many. Returns those orgs."""
    over = [
        (str(row["organization_id"]), row["n"])
        for row in Attempt.objects.values("organization_id")
        .annotate(n=Count("id"))
        .filter(n__gt=quota)
    ]
    for org, n in over:
        # The (n - quota)-th row from the oldest is the oldest row kept whole (an index walk of n - quota rows, not
        # of the quota). Rows settled since the count are newer, so they only make the cut more generous.
        cut = list(
            Attempt.objects.filter(organization_id=org)
            .order_by("settled_at", "id")
            .values_list("settled_at", "id")[n - quota : n - quota + 1]
        )
        if not cut:
            continue
        older = _older_than(*cut[0])
        while True:
            with transaction.atomic():
                ids = _ids(
                    Attempt.objects.filter(
                        older, organization_id=org, response_body_excerpt__isnull=False
                    ).order_by("settled_at", "id")
                )
                if not ids:
                    break
                Attempt.objects.filter(id__in=ids).update(response_body_excerpt=None)
        log.error(
            "webhooks attempt log over its quota for an org",
            extra={
                "metric": "webhooks.attempts.over_quota",
                "alert": "WA9",
                "org": org,
                "rows": n,
            },
        )
    return [org for org, _n in over]


def run(now: datetime, scale: float = 1.0, quota: int = ATTEMPT_LOG_QUOTA) -> dict:
    """One retention pass over every org. Returns the counts (logged as ``webhooks.retention``)."""
    now_ms = ms_of(now)
    base = lifecycle.cutoffs(now_ms, None, scale)
    out: dict = {"bodies_dropped": drop_bodies(dt_of(base["body"]))}
    zero = Event.objects.filter(
        fanout_state="done",
        delivery_count=0,
        created_at__lt=dt_of(base["zero_delivery_event"]),
    )
    out["zero_delivery_events"] = purge_events(zero)["events"]
    logs = {"attempts": 0, "deliveries": 0, "payloads": 0, "events": 0}
    for org in (
        Event.objects.order_by().values_list("organization_id", flat=True).distinct()
    ):
        cut = lifecycle.cutoffs(now_ms, org_log_days(org), scale)["log"]
        got = purge_events(
            Event.objects.filter(organization_id=org, created_at__lt=dt_of(cut))
        )
        for k, v in got.items():
            logs[k] += v
    out["log"] = logs
    out["secrets"] = purge_secrets(now)
    out["api_ledger"] = purge_api_ledger(
        now - timedelta(seconds=API_LEDGER_KEEP_S * scale)
    )
    out["listen_endpoints"] = purge_listen_endpoints(dt_of(base["listen_idle"]))
    out["orgs_over_attempt_quota"] = attempt_quota(quota)
    extra = {k: v for k, v in out.items() if k != "log"}
    extra.update({f"log_{k}": v for k, v in logs.items()})
    log.info("webhooks retention pass", extra={"metric": "webhooks.retention", **extra})
    OpsSnapshot.objects.update_or_create(
        id="retention", defaults={"computed_at": now, "data": out}
    )
    return out
