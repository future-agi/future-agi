"""The relay's lifecycle step (READINESS 3.22): a lifecycle row is delivered only once its
purge receipt is recorded.

EE answers ``applied`` (or ``replayed``) with the receipt when the purge is done, and
``ignored`` when it runs without just-in-time identity (it holds nothing for the scope). A
receipt that does not describe the event, or a write that fails, keeps the row pending: it is
asked again, and EE answers with the same receipt.
"""

import structlog
from django.db import DatabaseError

from accounts.lifecycle.interpret import LIFECYCLE_SOURCES
from accounts.lifecycle.receipts import ReceiptInvalid, record, validate

logger = structlog.get_logger(__name__)


def settle_lifecycle(rows, delivered, outcomes, receipts):
    """Record each delivered lifecycle row's receipt. Returns (delivered ids, error code or "")."""
    kept = set(delivered)
    error = ""
    for row in rows:
        if row.source not in LIFECYCLE_SOURCES or not row.event:
            continue
        for event in row.event["events"]:
            if event["id"] not in kept:
                continue
            try:
                receipt = (
                    None
                    if outcomes.get(event["id"]) == "ignored"
                    else validate(receipts.get(event["id"]), event)
                )
                record(row, event, receipt)
            except ReceiptInvalid as exc:
                kept.discard(event["id"])
                error = "ee_receipt_invalid"
                logger.error(
                    "omega_host_purge_receipt_invalid",
                    event_id=event["id"],
                    field=str(exc),
                )
            except DatabaseError:
                kept.discard(event["id"])
                error = error or "receipt_not_recorded"
                logger.exception(
                    "omega_host_purge_receipt_not_recorded", event_id=event["id"]
                )
    return frozenset(kept), error
