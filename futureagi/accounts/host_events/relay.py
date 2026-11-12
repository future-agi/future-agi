"""Deliver captured changes to Omega EE, retrying until EE accepts them (READINESS 1.2a).

One drain: take up to ``BATCH`` due rows under a short lease (``FOR UPDATE SKIP LOCKED``,
so two drains never take the same row and a drain that died hands its rows back when
the lease lapses), freeze each row's event the first time, POST them signed, then mark
each row delivered or schedule its retry with exponential backoff. Nothing is ever
dropped: a row stays pending until EE answers ``applied``, ``replayed`` or ``ignored``.
Delivery is at least once; EE refuses a second application of the same event id.

Drains run from three places: right after a membership or user write commits
(``accounts.signals.host_event_signals``), every 3 s from the Temporal schedule
(``tfc.temporal.schedules.omega_host_events``; it picks up bulk updates and retries), and
from ``manage.py drain_omega_host_events`` for operators.
"""

import random
import time
from collections import Counter
from datetime import timedelta

import structlog
from django.db import transaction
from django.db.models import Min, Q
from django.utils import timezone

from accounts.host_events import config as channel
from accounts.host_events.interpret import Lookups, collapse, interpret
from accounts.host_events.signing import encode_body
from accounts.host_events.transport import send_batch
from accounts.lifecycle.delivery import settle_lifecycle
from accounts.lifecycle.interpret import LIFECYCLE_SOURCES, split_lifecycle
from accounts.models.host_event_outbox import HostEventOutbox, HostEventStatus
from accounts.models.organization_membership import OrganizationMembership

logger = structlog.get_logger(__name__)

BATCH = 100
LEASE = timedelta(seconds=30)
MAX_BACKOFF_SECONDS = 300
MAX_BODY_BYTES = 65_536
# EE accepts at most this many organizations in one user.deactivated event.
MAX_ORGANIZATIONS_PER_EVENT = 500
BACKLOG_ALERT_SECONDS = 15 * 60
# A purge (READINESS 3.22) must finish within E02's hour; alert well before it.
PURGE_ALERT_SECONDS = 45 * 60
RETENTION = timedelta(days=7)
_PURGE_CHUNK = 1000
# A 3 s drain need not look for old rows every time.
_PURGE_EVERY_SECONDS = 600.0
_last_purge = {"at": None}
_last_misconfigured_log = {"at": None}
_CONFIG_TTL_SECONDS = 30.0
_config_cache = {"at": None, "value": None}


def current_config():
    """The channel configuration, re-read at most every 30 s (the secret can be rotated)."""
    now = time.monotonic()
    cached = _config_cache["value"]
    if cached is None or now - _config_cache["at"] > _CONFIG_TTL_SECONDS:
        cached = channel.load_config()
        _config_cache.update(at=now, value=cached)
    return cached


def _lookups():
    def org_level(user_id, organization_id):
        membership = (
            OrganizationMembership._base_manager.filter(
                user_id=user_id,
                organization_id=organization_id,
                is_active=True,
                deleted=False,
            )
            .order_by("-created_at")
            .first()
        )
        return membership.level_or_legacy if membership is not None else None

    def organizations_of(user_id):
        return set(
            OrganizationMembership._base_manager.filter(user_id=user_id).values_list(
                "organization_id", flat=True
            )
        )

    return Lookups(org_level=org_level, organizations_of=organizations_of)


def backoff(attempts):
    """Seconds before retry ``attempts`` + 1: 1, 2, 4, ... jittered by +-20%, never over 5 minutes."""
    base = min(MAX_BACKOFF_SECONDS, 2 ** max(0, attempts - 1))
    return min(MAX_BACKOFF_SECONDS, base * (0.8 + 0.4 * random.random()))


def _claim(limit, now):
    with transaction.atomic():
        ids = list(
            HostEventOutbox.objects.select_for_update(skip_locked=True)
            .filter(status=HostEventStatus.PENDING)
            # A fresh row is due at once. Its next_attempt_at is the database's clock, which
            # may differ from this process's; every retry time after that is this clock's.
            .filter(Q(attempts=0) | Q(next_attempt_at__lte=now))
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
            .order_by("id")
            .values_list("id", flat=True)[:limit]
        )
        if ids:
            rows = list(HostEventOutbox.objects.filter(id__in=ids).order_by("id"))
            for row in rows:
                row.attempts += 1
                row.lease_until = now + LEASE
            HostEventOutbox.objects.bulk_update(rows, ["attempts", "lease_until"])
            return rows
    return []


def _split(event, base_id, occurred_ms):
    """One row's events: one, or a user.deactivated split so no event names too many orgs."""
    organizations = event.pop("organizations", None)
    head = {"id": base_id, "occurredAtMs": occurred_ms, **event}
    if organizations is None:
        return [head]
    chunks = [
        organizations[start : start + MAX_ORGANIZATIONS_PER_EVENT]
        for start in range(0, len(organizations), MAX_ORGANIZATIONS_PER_EVENT)
    ]
    if len(chunks) == 1:
        return [{**head, "organizations": chunks[0]}]
    return [
        {**head, "id": f"{base_id}.{index}", "organizations": chunk}
        for index, chunk in enumerate(chunks)
    ]


def _freeze(rows, now):
    """Interpret rows that have no event yet. Returns the rows that have something to send."""
    lookups = _lookups()
    fresh = [row for row in rows if row.event is None]
    interpreted = collapse([(row.id, interpret(row, lookups)) for row in fresh])
    by_id = dict(interpreted)
    skipped = []
    for row in fresh:
        event = by_id[row.id]
        if event is None:
            row.status, row.lease_until, row.delivered_at = (
                HostEventStatus.SKIPPED,
                None,
                now,
            )
            skipped.append(row)
            continue
        # The instant EE revokes from. Keys are dated by this (app) clock, and the database's
        # clock may lag it, so take the later of the two: every key minted before the change
        # is then refused, never one minted after it plus the lag.
        occurred_ms = int(max(row.captured_at, now).timestamp() * 1000)
        row.event = {"events": _split(dict(event), str(row.event_id), occurred_ms)}
        row.save(update_fields=["event"])
    if skipped:
        HostEventOutbox.objects.bulk_update(
            skipped, ["status", "lease_until", "delivered_at"]
        )
    return [row for row in rows if row.status == HostEventStatus.PENDING]


def _batches(rows, issuer):
    """Group events into bodies of at most BATCH events and about MAX_BODY_BYTES.

    Revocations and lifecycle events never share a body: an EE that does not know a lifecycle
    event refuses its whole body, and that must never hold a revocation back.
    """
    for group in split_lifecycle(rows):
        batch = []
        for row in group:
            for event in row.event["events"]:
                candidate = batch + [event]
                if batch and (
                    len(candidate) > BATCH
                    or len(encode_body(issuer, candidate)) > MAX_BODY_BYTES
                ):
                    yield batch
                    candidate = [event]
                batch = candidate
        if batch:
            yield batch


def _settle(rows, delivered, error, now, stats, receipts=None):
    """Mark each row delivered (all its events accepted) or schedule its retry.

    ``receipts`` maps an event id to (sent_at, EE's receivedAtMs) for the batch that carried it.
    """
    receipts = receipts or {}
    done, retry = [], []
    for row in rows:
        ids = [event["id"] for event in row.event["events"]]
        if set(ids) <= delivered:
            sent_at, received_ms = receipts.get(ids[0], (None, None))
            row.status, row.delivered_at, row.lease_until, row.last_error = (
                HostEventStatus.DELIVERED,
                now,
                None,
                "",
            )
            row.sent_at, row.ee_received_at_ms = sent_at, received_ms
            done.append(row)
        else:
            row.lease_until = None
            row.next_attempt_at = now + timedelta(seconds=backoff(row.attempts))
            row.last_error = error or "ee_event_refused"
            retry.append(row)
    if done:
        HostEventOutbox.objects.bulk_update(
            done,
            [
                "status",
                "delivered_at",
                "lease_until",
                "last_error",
                "sent_at",
                "ee_received_at_ms",
            ],
        )
    if retry:
        HostEventOutbox.objects.bulk_update(
            retry, ["lease_until", "next_attempt_at", "last_error"]
        )
    stats["delivered"] += len(done)
    stats["retrying"] += len(retry)


def _deliver(rows, config, send, now, stats):
    delivered, errors, receipts = set(), [], {}
    outcomes, purge_receipts = {}, {}
    for batch in _batches(rows, config.issuer):
        body = encode_body(config.issuer, batch)
        sent_at = timezone.now()
        result = send(config, body, [event["id"] for event in batch])
        delivered |= result.delivered
        outcomes.update(result.outcomes)
        purge_receipts.update(result.receipts)
        for event in batch:
            receipts[event["id"]] = (sent_at, result.received_at_ms)
        if result.error:
            errors.append(result.error)
        logger.info(
            "omega_host_events_sent",
            events=len(batch),
            delivered=len(result.delivered),
            error=result.error or None,
            sent_at_ms=int(sent_at.timestamp() * 1000),
            ee_received_at_ms=result.received_at_ms,
        )
    # A lifecycle row counts as delivered only once its purge receipt is recorded (3.22).
    delivered, receipt_error = settle_lifecycle(
        rows, delivered, outcomes, purge_receipts
    )
    if receipt_error:
        errors.append(receipt_error)
    _settle(rows, delivered, errors[0] if errors else "", now, stats, receipts)
    if errors and set(errors) != {"ee_purge_pending"}:
        level = logger.error if "ee_refused_signature" in errors else logger.warning
        level("omega_host_events_delivery_failed", errors=sorted(set(errors)))


def purge(now=None, *, include_pending=False):
    """Delete settled rows older than the retention, a chunk at a time.

    ``include_pending`` also ages out undelivered rows. Only a process that sees the
    channel disabled passes it, and only for rows older than the retention: no process
    ever settles or drops a recent row because of its own environment, since an API and
    a worker in different containers may be configured differently.
    """
    cutoff = (now or timezone.now()) - RETENTION
    statuses = [HostEventStatus.DELIVERED, HostEventStatus.SKIPPED]
    if include_pending:
        statuses.append(HostEventStatus.PENDING)
    removed = 0
    while True:
        ids = list(
            HostEventOutbox.objects.filter(
                status__in=statuses, captured_at__lt=cutoff
            ).values_list("id", flat=True)[:_PURGE_CHUNK]
        )
        if not ids:
            return removed
        removed += HostEventOutbox.objects.filter(id__in=ids).delete()[0]


def _misconfigured_log_due():
    # Loud, but once a minute per process rather than on every 3 s drain.
    now = time.monotonic()
    if (
        _last_misconfigured_log["at"] is not None
        and now - _last_misconfigured_log["at"] < 60
    ):
        return False
    _last_misconfigured_log["at"] = now
    return True


def _purge_due():
    now = time.monotonic()
    if _last_purge["at"] is not None and now - _last_purge["at"] < _PURGE_EVERY_SECONDS:
        return False
    _last_purge["at"] = now
    return True


def backlog_seconds(now=None, *, purges=False):
    """Age of the oldest pending revocation, or with ``purges`` of the oldest pending purge."""
    pending = HostEventOutbox.objects.filter(status=HostEventStatus.PENDING)
    pending = (
        pending.filter(source__in=LIFECYCLE_SOURCES)
        if purges
        else pending.exclude(source__in=LIFECYCLE_SOURCES)
    )
    oldest = pending.aggregate(oldest=Min("captured_at"))["oldest"]
    return (
        0.0
        if oldest is None
        else max(0.0, ((now or timezone.now()) - oldest).total_seconds())
    )


def drain(
    *, config=None, send=send_batch, limit=BATCH, budget_seconds=20.0, purge_old=True
):
    """Deliver everything due, within ``budget_seconds``. Returns counts for logs and tests."""
    config = config or current_config()
    stats = Counter()
    if config.state != channel.READY:
        # This process cannot send. It claims nothing, so it never delays a process
        # that can (an API and a worker in different containers may differ).
        if config.state == channel.MISCONFIGURED and _misconfigured_log_due():
            logger.error(
                "omega_host_events_channel_misconfigured", problem=config.problem
            )
        if purge_old and _purge_due():
            stats["purged"] += purge(include_pending=config.state == channel.DISABLED)
        stats["backlog_seconds"] = int(backlog_seconds())
        return dict(stats)
    deadline = time.monotonic() + budget_seconds
    while time.monotonic() < deadline:
        now = timezone.now()
        rows = _claim(limit, now)
        if not rows:
            break
        stats["claimed"] += len(rows)
        pending = _freeze(rows, now)
        stats["skipped"] += len(rows) - len(pending)
        if pending:
            _deliver(pending, config, send, now, stats)
        if len(rows) < limit:
            break
    if purge_old and _purge_due():
        stats["purged"] += purge()
    lag = backlog_seconds()
    stats["backlog_seconds"] = int(lag)
    if lag > BACKLOG_ALERT_SECONDS:
        logger.error("omega_host_events_backlog", backlog_seconds=int(lag))
    purge_lag = backlog_seconds(purges=True)
    stats["purge_backlog_seconds"] = int(purge_lag)
    if purge_lag > PURGE_ALERT_SECONDS:
        logger.error("omega_host_purge_overdue", backlog_seconds=int(purge_lag))
    return dict(stats)
