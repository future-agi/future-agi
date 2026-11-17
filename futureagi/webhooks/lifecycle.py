"""Org deletion and member erasure for every ``webhooks_*`` table (DESIGN.md sections 4, 7.5 and 13.4; P1, P4).

``PURGE_RULES`` names the rule for each table. :func:`uncovered_tables` is the coverage check: a new ``webhooks_*``
table with no rule fails ``test_lifecycle``.

- **Org deletion.** Every tenant table has an ``organization`` foreign key with ``on_delete=CASCADE``, so deleting
  the ``Organization`` row removes everything. :func:`purge_organization` does the same in chunks first (each a
  short transaction, children before parents), so a large org never becomes one long cascading transaction.
- **Member erasure.** :func:`erase_member` nulls the person's name in kept bodies, in the frozen event data and in
  the receiver excerpts of the event's deliveries (section 7.5). Excerpts are scrubbed with the names found in the
  event itself, so they are cleaned whether or not the body is still kept: bodies are dropped at 30 days, excerpts
  stay for the org's log retention (90 days by default). Foreign keys to the user (``owner``, ``created_by``,
  ``disabled_by``, ``revoked_by``, ``actor``) are ``SET_NULL`` and need nothing here.

FA ``dev`` has no org-deletion job or member-erasure hook yet; these are the functions such a job calls.
"""

from __future__ import annotations

import hashlib
import logging

from django.apps import apps
from django.db import transaction

from webhooks.core import lifecycle
from webhooks.models import Attempt, Delivery, Event, Payload

log = logging.getLogger("webhooks.lifecycle")

CHUNK = lifecycle.PURGE_CHUNK

ORG_CASCADE = (
    "org: deleted in chunks by purge_organization, and by the organization FK's CASCADE"
)
INSTALL_WIDE = "install-wide operations data: no tenant rows, nothing to purge"

PURGE_RULES: dict[str, str] = {
    "webhooks_api_action": ORG_CASCADE
    + "; retention: write, test and bulk_redeliver rows after a day (retention.purge_api_ledger); a redeliver row"
    " goes with its delivery (the delivery FK's CASCADE); erasure: none (the actor FK is SET_NULL, no names)",
    "webhooks_attempt": ORG_CASCADE + "; erasure: excerpts scrubbed",
    "webhooks_delivery": ORG_CASCADE,
    "webhooks_payload": ORG_CASCADE + "; erasure: name nulled in the body",
    "webhooks_event": ORG_CASCADE + "; erasure: name nulled in data and builder_input",
    "webhooks_subscription": ORG_CASCADE,
    "webhooks_secret": ORG_CASCADE,
    "webhooks_endpoint_state": ORG_CASCADE,
    "webhooks_endpoint": ORG_CASCADE,
    "webhooks_policy": ORG_CASCADE,
    "webhooks_dispatcher_beat": INSTALL_WIDE,
    "webhooks_ops_alert": INSTALL_WIDE,
    "webhooks_ops_snapshot": INSTALL_WIDE,
}

ORDER = (
    "webhooks_api_action",
    "webhooks_attempt",
    "webhooks_delivery",
    "webhooks_payload",
    "webhooks_event",
    "webhooks_subscription",
    "webhooks_secret",
    "webhooks_endpoint_state",
    "webhooks_endpoint",
    "webhooks_policy",
)
"""Children before parents."""


def webhooks_tables(models=None) -> list[str]:
    models = apps.get_app_config("webhooks").get_models() if models is None else models
    return sorted(m._meta.db_table for m in models)


def uncovered_tables(models=None) -> list[str]:
    """``webhooks_*`` tables with no purge rule (the coverage check of section 4)."""
    return [t for t in webhooks_tables(models) if t not in PURGE_RULES]


def _model(table: str):
    for m in apps.get_app_config("webhooks").get_models():
        if m._meta.db_table == table:
            return m
    raise LookupError(table)


def purge_organization(organization_id) -> dict[str, int]:
    """Delete every ``webhooks_*`` row of one organization, in chunks, children first. Idempotent."""
    out: dict[str, int] = {}
    for table in ORDER:
        model = _model(table)
        pk = model._meta.pk.name
        n = 0
        while True:
            with transaction.atomic():
                ids = list(
                    model.objects.filter(organization_id=organization_id)
                    .order_by(pk)
                    .values_list(pk, flat=True)[:CHUNK]
                )
                if not ids:
                    break
                n += model.objects.filter(**{f"{pk}__in": ids}).delete()[0]
        out[table] = n
    log.info(
        "webhooks rows of an organization purged",
        extra={"metric": "webhooks.lifecycle.org_purged", **out},
    )
    return out


def erase_member(organization_id, user_id) -> dict[str, int]:
    """Null the member's name in every kept body, event and excerpt of the org (P1). Returns the rows rewritten."""
    person = str(user_id)
    counts = {"payloads": 0, "events": 0, "attempts": 0}
    last = ""
    while True:
        with transaction.atomic():
            rows = list(
                Payload.objects.select_for_update()
                .filter(
                    organization_id=organization_id, body__isnull=False, id__gt=last
                )
                .order_by("id")[:CHUNK]
            )
            if not rows:
                break
            for p in rows:
                body, found = lifecycle.erase_body(bytes(p.body), person)
                if body is None:
                    continue
                Payload.objects.filter(id=p.id).update(
                    body=body,
                    body_sha256=hashlib.sha256(body).hexdigest(),
                    body_bytes=len(body),
                )
                counts["payloads"] += 1
                counts["attempts"] += _scrub_excerpts(
                    Delivery.objects.filter(payload_id=p.id), found
                )
            last = rows[-1].id
    last = ""
    while True:
        with transaction.atomic():
            rows = list(
                Event.objects.select_for_update()
                .filter(organization_id=organization_id, id__gt=last)
                .order_by("id")[:CHUNK]
            )
            if not rows:
                break
            for ev in rows:
                data, f1 = lifecycle.erase_person(ev.data, person)
                built, f2 = lifecycle.erase_person(ev.builder_input, person)
                if f1 or f2 or data != ev.data or built != ev.builder_input:
                    Event.objects.filter(id=ev.id).update(
                        data=data, builder_input=built
                    )
                    counts["events"] += 1
                    # Every delivery of the event, body kept or not, in the same transaction as the event's rewrite
                    # (a later run finds no name in the event any more).
                    counts["attempts"] += _scrub_excerpts(
                        Delivery.objects.filter(event_id=ev.id), f1 + f2
                    )
            last = rows[-1].id
    log.info(
        "webhooks member erased",
        extra={"metric": "webhooks.lifecycle.member_erased", **counts},
    )
    return counts


def _scrub_excerpts(deliveries, names: list[str]) -> int:
    """Remove the names from the receiver excerpts of these deliveries' attempts; returns the rows rewritten."""
    if not names:
        return 0
    n = 0
    for a in Attempt.objects.filter(
        delivery__in=deliveries,
        response_body_excerpt__isnull=False,
    ).only("id", "response_body_excerpt"):
        new = lifecycle.scrub_names(a.response_body_excerpt, names)
        if new != a.response_body_excerpt:
            Attempt.objects.filter(id=a.id).update(response_body_excerpt=new)
            n += 1
    return n
