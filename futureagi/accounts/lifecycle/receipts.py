"""Check and record EE's purge receipt for a deleted workspace or organization (READINESS 3.22).

EE answers a lifecycle event ``pending`` until its purge is done, then ``applied`` with an
``omega-host-purge-receipt/v1`` receipt (every time it is asked after that). The relay hands the
receipt here before it marks the outbox row delivered:

- :func:`validate` keeps only the receipt's known fields and refuses one that does not describe
  this event (a malformed receipt leaves the row pending, never a success with no proof);
- :func:`record` writes one ``OmegaPurgeReceipt`` per event and, for a workspace whose
  organization still exists, one ``audit_log`` row (``omega.workspace_purged``). Idempotent.
"""

import json
import re
from datetime import UTC, datetime

from django.db import transaction

from accounts.lifecycle.interpret import EVENT_WORKSPACE_DELETED
from accounts.models.audit_log import AuditLog, AuditScope
from accounts.models.omega_purge_receipt import OmegaPurgeOutcome, OmegaPurgeReceipt
from accounts.models.organization import Organization

RECEIPT_SCHEMA = "omega-host-purge-receipt/v1"
# Not in AuditAction.CHOICES, like the rollout actions: `action` has no database constraint.
ACTION_WORKSPACE_PURGED = "omega.workspace_purged"
MAX_RECEIPT_BYTES = 65_536
# EE's receipt names every store of its store inventory (fix round 3), about 40 today.
MAX_STORES = 64
# What EE says happened to each store; absent on receipts from before the inventory.
STORE_OUTCOMES = frozenset(
    {"erased", "partial", "kept", "not_mounted", "not_host_writable"}
)
_STORE = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
_TEXT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


class ReceiptInvalid(ValueError):
    """EE's receipt does not describe this event; the row stays pending."""


def _count(value, field):
    if type(value) is not int or value < 0:
        raise ReceiptInvalid(field)
    return value


def _stores(value):
    if not isinstance(value, dict) or len(value) > MAX_STORES:
        raise ReceiptInvalid("stores")
    stores = {}
    for name, total in value.items():
        if (
            not isinstance(name, str)
            or not _STORE.match(name)
            or not isinstance(total, dict)
        ):
            raise ReceiptInvalid("stores")
        retained = total.get("retained", [])
        if (
            not isinstance(retained, list)
            or len(retained) > 20
            or not all(
                isinstance(line, str) and 0 < len(line) <= 400 for line in retained
            )
        ):
            raise ReceiptInvalid("stores.retained")
        stores[name] = {
            "erased": _count(total.get("erased"), "stores.erased"),
            "retired": _count(total.get("retired", 0), "stores.retired"),
            "retained": list(retained),
        }
        if "outcome" in total:
            if total["outcome"] not in STORE_OUTCOMES:
                raise ReceiptInvalid("stores.outcome")
            stores[name]["outcome"] = total["outcome"]
        if "runsCanceled" in total:
            stores[name]["runsCanceled"] = _count(total["runsCanceled"], "stores.runs")
    return stores


def _audit(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 1000:
        raise ReceiptInvalid("audit")
    entries = []
    for entry in value:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("entryId"), str)
            or not _TEXT_ID.match(entry["entryId"])
            or not isinstance(entry.get("hash"), str)
            or not _HASH.match(entry["hash"])
        ):
            raise ReceiptInvalid("audit")
        entries.append({"entryId": entry["entryId"], "hash": entry["hash"]})
    return entries


def validate(receipt, event):
    """The receipt's known fields, checked against the event it answers."""
    if not isinstance(receipt, dict) or receipt.get("schemaVersion") != RECEIPT_SCHEMA:
        raise ReceiptInvalid("schema")
    if len(json.dumps(receipt)) > MAX_RECEIPT_BYTES:
        raise ReceiptInvalid("size")
    if (
        receipt.get("eventId") != event["id"]
        or receipt.get("kind") != event["type"]
        or receipt.get("hostOrgId") != event["organizationId"]
        or receipt.get("hostWorkspaceId") != event["workspaceId"]
    ):
        raise ReceiptInvalid("scope")
    purge_id = receipt.get("purgeId")
    if not isinstance(purge_id, str) or not _TEXT_ID.match(purge_id):
        raise ReceiptInvalid("purgeId")
    return {
        "schemaVersion": RECEIPT_SCHEMA,
        "purgeId": purge_id,
        "eventId": event["id"],
        "kind": event["type"],
        "hostOrgId": event["organizationId"],
        "hostWorkspaceId": event["workspaceId"],
        "requestedAtMs": _count(receipt.get("requestedAtMs"), "requestedAtMs"),
        "acceptedAtMs": _count(receipt.get("acceptedAtMs"), "acceptedAtMs"),
        "completedAtMs": _count(receipt.get("completedAtMs"), "completedAtMs"),
        "tenants": _count(receipt.get("tenants"), "tenants"),
        "bindingsClosed": _count(receipt.get("bindingsClosed"), "bindingsClosed"),
        "stores": _stores(receipt.get("stores")),
        "audit": _audit(receipt.get("audit")),
    }


def _at(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def record(row, event, receipt):
    """Write the receipt (``receipt`` is a validated dict, or None when EE ignored the event)."""
    workspace_id = event["workspaceId"]
    outcome = OmegaPurgeOutcome.PURGED if receipt else OmegaPurgeOutcome.IGNORED
    with transaction.atomic():
        _, created = OmegaPurgeReceipt.objects.get_or_create(
            event_id=row.event_id,
            defaults={
                "kind": event["type"],
                "organization_id": event["organizationId"],
                "workspace_id": workspace_id,
                "outcome": outcome,
                "requested_at": _at(event["occurredAtMs"]),
                "completed_at": _at(receipt["completedAtMs"]) if receipt else None,
                "receipt": receipt or {},
            },
        )
        if not created or event["type"] != EVENT_WORKSPACE_DELETED:
            return
        # An organization deleted since has taken its audit log with it; the receipt row stays.
        if not Organization.objects.filter(id=event["organizationId"]).exists():
            return
        AuditLog.objects.create(
            organization_id=event["organizationId"],
            actor=None,
            action=ACTION_WORKSPACE_PURGED,
            scope=AuditScope.WORKSPACE,
            target_id=workspace_id,
            changes={
                "outcome": outcome,
                "tenants": receipt["tenants"] if receipt else 0,
                "stores": receipt["stores"] if receipt else {},
            },
            metadata={
                "eventId": event["id"],
                "purgeId": receipt["purgeId"] if receipt else None,
                "requestedAtMs": event["occurredAtMs"],
                "completedAtMs": receipt["completedAtMs"] if receipt else None,
                "eeAudit": receipt["audit"] if receipt else None,
            },
        )
