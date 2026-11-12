"""Idempotent audit projection. Never grants, consumes or recreates Redis authority."""

import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models.organization_membership import OrganizationMembership
from agentcc.contracts.gateway_admin import MCPApproval
from agentcc.models import AgentccMCPApproval

_TRANSITIONS = {
    "pending": {"approved", "denied", "used", "expired"},
    "approved": {"used", "expired"},
    "denied": {"expired"},
    "used": {"expired"},
    "expired": set(),
}


def _date(value):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError("Invalid approval timestamp")
    return parsed


def normalize(organization, payload):
    rec = MCPApproval.model_validate(payload).model_dump(exclude_none=True)
    if rec["org_id"] != str(organization.pk):
        raise ValueError("Approval organization mismatch")
    bounds = {
        "approval_id": 4096,
        "virtual_server_id": 64,
        "tool": 255,
        "args_hash": 64,
        "agent_run_id": 128,
        "note": 1024,
    }
    for field, maximum in bounds.items():
        if len(rec.get(field, "")) > maximum:
            raise ValueError("Approval field exceeds its bound")
    principal = rec["principal"]
    if len(principal.get("key_id", "")) > 255:
        raise ValueError("Approval principal exceeds its bound")
    requested, expires = _date(rec["requested_at"]), _date(rec["expires_at"])
    if expires <= timezone.now():
        rec["status"] = "expired"
    actor = None
    if rec.get("decided_by"):
        # The gateway's actor is supplied only by the session bridge. Resolve
        # it within this org, never attach an arbitrary foreign user FK.
        try:
            actor = (
                OrganizationMembership.no_workspace_objects.filter(
                    organization=organization, user_id=rec["decided_by"]
                )
                .values_list("user_id", flat=True)
                .first()
            )
        except (ValueError, TypeError, ValidationError):
            raise ValueError("Invalid approval actor") from None
    defaults = {
        "organization": organization,
        "approval_id": rec["approval_id"],
        "virtual_server_id": rec["virtual_server_id"],
        "tool": rec["tool"],
        "args_hash": rec["args_hash"],
        "agent_run_id": rec.get("agent_run_id", ""),
        "api_key_id": principal.get("key_id", ""),
        "requested_at": requested,
        "expires_at": expires,
        "status": rec["status"],
        "decided_by_id": actor,
        "decided_at": _date(rec["decided_at"]) if rec.get("decided_at") else None,
        "note": rec.get("note", ""),
    }
    return rec, defaults


def project_approval(organization, payload):
    rec, values = normalize(organization, payload)
    digest = hashlib.sha256(rec["approval_id"].encode()).hexdigest()
    with transaction.atomic():
        row, created = AgentccMCPApproval.objects.get_or_create(
            approval_digest=digest, defaults=values
        )
        if created:
            return rec
        row = AgentccMCPApproval.objects.select_for_update().get(pk=row.pk)
        for name in (
            "approval_id",
            "virtual_server_id",
            "tool",
            "args_hash",
            "agent_run_id",
            "api_key_id",
            "requested_at",
            "expires_at",
        ):
            if getattr(row, name) != values[name]:
                raise ValueError("Approval binding changed")
        if row.organization_id != organization.pk:
            raise ValueError("Approval organization mismatch")
        # A stale in-flight list response cannot roll back a decision or use.
        if values["status"] in _TRANSITIONS[row.status]:
            row.status = values["status"]
        if values["decided_at"] and not row.decided_at:
            row.decided_at = values["decided_at"]
            row.decided_by_id = values["decided_by_id"]
            row.note = values["note"]
        row.save(update_fields=["status", "decided_at", "decided_by", "note"])
        rec["status"] = row.status
        if row.decided_at:
            rec["decided_at"] = row.decided_at.isoformat()
            rec["note"] = row.note
            if row.decided_by_id:
                rec["decided_by"] = str(row.decided_by_id)
    return rec


def reconcile_organization(organization, client):
    cursor, seen, records = "", set(), []
    while True:
        payload = client.list(organization, cursor=cursor)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("approvals"), list)
            or len(payload["approvals"]) > 1000
        ):
            raise ValueError("Invalid approvals response")
        # An older gateway's capped response is not proof of completion.
        next_cursor = payload.get("next_cursor")
        if not isinstance(next_cursor, str) or len(next_cursor) > 4096:
            raise ValueError("Invalid approvals cursor")
        # Validate every tenant binding before any projection or response,
        # including pages beyond the original retention-ordered window.
        for record in payload["approvals"]:
            normalize(organization, record)
        records.extend(payload["approvals"])
        if not next_cursor:
            break
        if next_cursor in seen:
            raise ValueError("Repeated approvals cursor")
        seen.add(next_cursor)
        cursor = next_cursor
    with transaction.atomic():
        return [project_approval(organization, record) for record in records]


def expire_audit():
    # Logical expiry still advances once Redis's audit retention has elapsed.
    return (
        AgentccMCPApproval.objects.exclude(status="expired")
        .filter(expires_at__lte=timezone.now())
        .update(status="expired")
    )
