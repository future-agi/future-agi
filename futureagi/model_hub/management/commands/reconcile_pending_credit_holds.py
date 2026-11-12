"""Reconcile pending managed holds; no provider execution or schema migration.

FI_CREDIT_SETTLE_RECORD_LOOKUP optionally names a callable (dotted import path)
accepting organization_id and gateway_request_id and returning None or a scoped
{organization_id, gateway_request_id, actual_amount} record. No reader is assumed
on this base: the default leaves holds open until the 24-hour release deadline.
Excess incidents are immutable fields on their holds, atomically recorded with
settlement/correction. Reports and alerts read those fields; final rows need no
incident delivery, acknowledgement or reconciliation enumeration.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from model_hub.services.credit_ledger_port import (
    get_credit_ledger,
    logger,
    reconcile_pending_hold,
    utc_now,
)


def lookup_settle_record(*, organization_id, gateway_request_id):
    reader = getattr(settings, "FI_CREDIT_SETTLE_RECORD_LOOKUP", None)
    if not reader:
        return None
    lookup = import_string(reader) if isinstance(reader, str) else reader
    return lookup(organization_id=organization_id, gateway_request_id=gateway_request_id)


class Command(BaseCommand):
    help = "Settle pending credit holds against usage records, or release at 24 hours."

    def handle(self, *args, **options):
        now = utc_now()
        failures = 0
        counts = {"settled": 0, "released": 0, "pending_reconcile": 0}
        for hold in get_credit_ledger().list_pending_holds(older_than=now):
            try:
                record = None
                if hold.get("gateway_request_id"):
                    record = lookup_settle_record(
                        organization_id=hold["organization_id"],
                        gateway_request_id=hold["gateway_request_id"],
                    )
                outcome = reconcile_pending_hold(
                    organization_id=hold["organization_id"],
                    gateway_request_id=hold.get("gateway_request_id"),
                    hold_id=hold["hold_id"], amount_estimate=hold["amount_estimate"],
                    opened_at=hold["opened_at"], settle_record=record, now=now,
                )
                counts[outcome] += 1
            except Exception as exc:
                failures += 1
                # A failing reader is not evidence of no record. Leave open;
                # log identifiers and exception type only, never exception text.
                logger.error("credit_pending_reconcile_failed",
                             organization_id=hold.get("organization_id"),
                             request_id=hold.get("request_id"),
                             gateway_request_id=hold.get("gateway_request_id"),
                             error_type=type(exc).__name__)
        self.stdout.write(" ".join(f"{key}={value}" for key, value in counts.items()))
        if failures:
            raise CommandError(f"{failures} pending holds could not be reconciled")
