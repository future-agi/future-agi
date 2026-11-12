"""Short, fenced connect leases. Call these only under the binding row lock."""

import uuid
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from agentcc.services import mcp_vendor_cleanup as cleanup

LEASE_DURATION = timedelta(minutes=2)


def expired(row):
    return row.connect_pending and (
        row.connect_expires_at is None or row.connect_expires_at <= timezone.now()
    )


def owns(saved, snapshot):
    return (
        saved.connect_lease_id == snapshot.connect_lease_id
        and saved.consent_generation == snapshot.consent_generation
    )


def recover(row):
    if not expired(row):
        return False
    obligation = cleanup.remember(row)
    obligation.in_flight = True
    obligation.outcome, obligation.reason = "pending", "lease_incomplete"
    obligation.version += 1
    obligation.save()
    # A dead worker may have minted a handle it never persisted. Never silently
    # claim that cleanup succeeded. The old generation stays a durable debt;
    # a late worker may fill its handles, but cannot modify the new generation.
    row.connect_pending = False
    row.connect_lease_id = row.connect_expires_at = None
    row.status = "revoked"
    row.version += 1
    row.gateway_purge_pending = row.vendor_revoke_pending = True
    row.vendor_revoke_outcome = "pending"
    row.save()
    return True


def begin(row):
    row.connect_lease_id = uuid.uuid4()
    row.connect_expires_at = timezone.now() + LEASE_DURATION
    row.connect_pending = True
    row.save()
    obligation = cleanup.remember(row)
    obligation.in_flight = True
    obligation.version += 1
    obligation.save()


def finish(snapshot, saved):
    # Completing an old operation unblocks only its own cleanup attempt.
    snapshot.cleanup_obligations.filter(
        consent_generation=snapshot.consent_generation
    ).update(in_flight=False, version=F("version") + 1)
    if not owns(saved, snapshot):
        return
    saved.connect_pending = False
    saved.connect_expires_at = saved.connect_lease_id = None
    if saved.status == "revoked":
        saved.vendor_revoke_pending = True
        saved.vendor_revoke_outcome = "pending"
    saved.save()
