"""An account's deletion erases its address from the Hub's email outbox (HUB-18, E02).

The platform deletes members with `Model.delete()`, so the Hub hooks into the deletion itself, as
the Hub erasure service does for its other records. `outbox.forget_address` deletes the sent and
dead rows that named the address (their frozen body was written to that person) and removes it from
rows still to be delivered. It runs after the deletion commits, so a deletion that rolls back keeps
the rows, and an outbox error can never stop the deletion.
"""

from __future__ import annotations

import structlog
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver

logger = structlog.get_logger(__name__)


@receiver(pre_delete, sender=get_user_model(), dispatch_uid="omega_hub_outbox_forget_address")
def forget_outbox_address(sender, instance, using, **kwargs):
    email = (getattr(instance, "email", "") or "").strip()
    if not email:
        return

    def forget():
        from omega_hub.services import outbox

        try:
            outbox.forget_address(email)
        except Exception as exc:  # noqa: BLE001 - logged; the operator re-runs it (RUNBOOK)
            logger.error("omega_hub_outbox_forget_failed", error=f"{exc.__class__.__name__}: {exc}"[:300])

    transaction.on_commit(forget, using=using)
