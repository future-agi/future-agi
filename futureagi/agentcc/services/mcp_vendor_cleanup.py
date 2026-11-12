"""Durable cleanup handles survive consent superseding and transient failures."""

from copy import copy

from django.db.models import F
from django.utils import timezone

from agentcc.models.mcp_vendor import AgentccMCPVendorCleanup as Cleanup
from agentcc.models.mcp_vendor import AgentccMCPVendorReceipt as Receipt
from agentcc.services.mcp_revocation import Revocation


def remember(row):
    """Called under the binding lock before a reference can be overwritten."""
    obligation, _ = Cleanup.objects.get_or_create(
        connection=row,
        consent_generation=row.consent_generation,
        defaults={
            "vendor_account_ref": row.vendor_account_ref,
            "encrypted_session": row.encrypted_session,
        },
    )
    changed = False
    for name in ("vendor_account_ref", "encrypted_session"):
        value = getattr(row, name)
        if value and value != getattr(obligation, name):
            setattr(obligation, name, value)
            changed = True
    if changed:
        obligation.outcome, obligation.reason = "pending", ""
        obligation.version += 1
        obligation.save()
    return obligation


def run(row, revoke_one, *, offboard=False):
    # Snapshot and row.version fence prevent old HTTP responses from modifying
    # the current connection. Each old generation has its own durable outcome.
    remember(row)
    all_subjects = offboard and row.binding.vendor == "pipedream"
    if all_subjects:
        # Subjects are deterministic even for generations created before this
        # table existed. Deleting an external user covers abandoned links too.
        for generation in range(1, row.consent_generation):
            Cleanup.objects.get_or_create(connection=row, consent_generation=generation)
    results = []
    obligations = row.cleanup_obligations.order_by("consent_generation")
    for obligation in obligations:
        if obligation.outcome != "pending" and not all_subjects:
            results.append(Revocation(obligation.outcome, obligation.reason))
            continue
        snapshot = copy(row)
        snapshot.consent_generation = obligation.consent_generation
        snapshot.vendor_account_ref = obligation.vendor_account_ref
        snapshot.encrypted_session = obligation.encrypted_session
        try:
            result = revoke_one(snapshot, offboard=offboard)
        except Exception:
            result = Revocation("pending", "vendor_unavailable")
        if obligation.in_flight:
            result = Revocation("pending", "lease_incomplete")
        applied = Cleanup.objects.filter(
            pk=obligation.pk, version=obligation.version
        ).update(
            vendor_account_ref=snapshot.vendor_account_ref,
            outcome=result.outcome,
            reason=result.reason,
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
        if not applied:
            result = Revocation("pending", "cleanup_changed")
        Receipt.objects.create(
            connection=row,
            organization_id=row.binding.organization_id,
            connection_ref=row.pk,
            server_id=row.binding.server_id,
            consent_generation=obligation.consent_generation,
            step="generation_cleanup",
            outcome=result.outcome,
            reason=result.reason,
        )
        results.append(result)
    for outcome in ("pending", "unconfirmed"):
        for result in results:
            if result.outcome == outcome:
                return result
    return Revocation("confirmed")
