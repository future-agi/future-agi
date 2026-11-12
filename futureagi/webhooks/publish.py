"""``webhooks.publish()``: the producer side of the outbox (DESIGN.md sections 3, 7.4 and 13.1, R4, R12, R13).

Called inside the producer's own transaction. It never makes a network call or a Temporal RPC, and never fails the
user's action for volume: (1) an indexed EXISTS for a live endpoint in the organization (no row when there is none),
(2) INSERT the event, unique per (organization, source_key), (3) ``SELECT pg_notify('webhooks', '')``, which
Postgres delivers only if the transaction commits.

A type outside the v1 catalog is refused at once (``UnknownEventType``): a producer bug fails the producer's own
tests, never the dispatcher. The result says whether the row is new, so B8 intake can answer a repeated notice with
``duplicate: true`` (contract section 5.3).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, NamedTuple

from django.db import IntegrityError, connection, transaction

from webhooks.core import ids
from webhooks.core.fanout import CATALOG
from webhooks.models import Endpoint, Event

log = logging.getLogger("webhooks.publish")

NOTIFY_CHANNEL = "webhooks"
_listeners: list[Callable[[Mapping[str, Any]], None]] = []


class SourceKeyConflict(Exception):
    """The same source_key arrived with a different source_hash (the API answers 409)."""


class UnknownEventType(ValueError):
    """The type is not in the v1 event catalog (DESIGN.md section 14)."""


class PublishResult(NamedTuple):
    """``event_id`` is None when the organization has no live endpoint (no row). ``created`` is False for a repeated
    ``source_key`` with the same hash: the existing event's id comes back (B8 contract section 5.3, ``duplicate``).

    Test ``.event_id``, ``.created`` or ``.duplicate``, never the result itself: as a 2-tuple it is always true,
    ``PublishResult(None, False)`` included."""

    event_id: str | None
    created: bool

    @property
    def duplicate(self) -> bool:
        return self.event_id is not None and not self.created


def add_listener(fn: Callable[[Mapping[str, Any]], None]) -> None:
    """In-process listeners run on every publish, with or without endpoints (for example the W6 label re-check)."""
    if fn not in _listeners:
        _listeners.append(fn)


def source_hash_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def notify() -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_notify(%s, '')", [NOTIFY_CHANNEL])


def publish(
    *,
    type: str,  # noqa: A002 - the catalog's word
    organization_id,
    workspace_id,
    data: Mapping[str, Any],
    source_key: str,
    source_hash: str,
    producer: str,
    occurred_at: datetime,
    subject_kind: str = "",
    subject_id: str = "",
    builder_input: Mapping[str, Any] | None = None,
    targeted_endpoint_id: str | None = None,
    not_before: datetime | None = None,
    event_id: str | None = None,
) -> PublishResult:
    """Queue one event. Returns ``(event_id, created)``: the new id and True; the existing id and False for a
    repeated source_key; or (None, False) when the organization has no live endpoint. Must run inside the producer's
    transaction (``atomic`` is required, so a caller outside one fails loudly instead of committing the event on its
    own)."""
    if not connection.in_atomic_block:
        raise RuntimeError(
            "webhooks.publish() must run inside the producer's transaction"
        )
    if type not in CATALOG:
        raise UnknownEventType(f"{type!r} is not in the webhooks event catalog")
    payload = {
        "type": type,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "data": data,
    }
    for fn in list(_listeners):
        try:
            fn(payload)
        except Exception:  # noqa: BLE001 - a listener never fails the producer
            log.exception(
                "webhooks listener failed", extra={"metric": "webhooks.listener.failed"}
            )
    live = Endpoint.objects.filter(
        organization_id=organization_id, deleted=False
    ).exclude(kind="cli_listen")
    if not live.exists():
        log.info(
            "no endpoint",
            extra={"metric": "webhooks.events.skipped_no_endpoint", "type": type},
        )
        return PublishResult(None, False)
    existing = Event.objects.filter(
        organization_id=organization_id, source_key=source_key
    ).first()
    if existing is not None:
        if existing.source_hash != source_hash:
            raise SourceKeyConflict(source_key)
        return PublishResult(existing.id, False)
    eid = event_id or ids.new_id("evt", ids.datetime_ms(occurred_at))
    try:
        with transaction.atomic():
            Event.objects.create(
                id=eid,
                organization_id=organization_id,
                workspace_id=workspace_id,
                type=type,
                occurred_at=occurred_at,
                producer=producer,
                subject_kind=subject_kind,
                subject_id=subject_id,
                source_key=source_key,
                source_hash=source_hash,
                data=dict(data),
                builder_input=None if builder_input is None else dict(builder_input),
                targeted_endpoint_id=targeted_endpoint_id,
                not_before=not_before,
            )
    except IntegrityError:
        # A concurrent producer inserted the same key first; the unique constraint is the arbiter.
        row = Event.objects.filter(
            organization_id=organization_id, source_key=source_key
        ).first()
        if row is None:
            raise
        if row.source_hash != source_hash:
            raise SourceKeyConflict(source_key) from None
        return PublishResult(row.id, False)
    notify()
    log.info(
        "published",
        extra={
            "metric": "webhooks.events.published",
            "type": type,
            "producer": producer,
        },
    )
    return PublishResult(eid, True)
