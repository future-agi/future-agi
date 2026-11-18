"""Read-only inventory of who is liable to pay, and whether we can charge
them. Previews the invoice for a period per org (no writes, no Stripe
calls) and reports chargeability signals so the auto-charge rollout can
be verified against real data before going live."""

import csv
import sys
from datetime import datetime

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Preview per-org liability for a billing period (read-only): "
        "invoice totals, plan, status, and chargeability signals"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--period",
            default="",
            help="Billing period YYYY-MM (default: current month)",
        )
        parser.add_argument("--org-id", default="", help="Restrict to one org")
        parser.add_argument(
            "--all",
            action="store_true",
            help="Include orgs with zero liability",
        )
        parser.add_argument("--csv", default="", help="Write results to a CSV file")

    def handle(self, *args, **options):
        from ee.cloud.billing.stripe_service import StripeService
        from ee.usage.models.usage import OrganizationSubscription
        from ee.usage.services.billing_engine import BillingEngine

        period = options["period"] or datetime.utcnow().strftime("%Y-%m")
        is_live = StripeService._is_live()

        qs = OrganizationSubscription.objects.filter(deleted=False).select_related(
            "organization"
        )
        if options["org_id"]:
            qs = qs.filter(organization_id=options["org_id"])

        rows = []
        errors = 0
        for sub in qs.iterator(chunk_size=200):
            org_id = str(sub.organization_id)
            preview = BillingEngine.generate_invoice(org_id, period, preview=True)
            if preview.get("error"):
                errors += 1
                self.stderr.write(f"ERROR {org_id}: {preview['error']}")
                continue

            total = preview["total"]
            if total <= 0 and not options["all"]:
                continue

            customer_id = (
                sub.stripe_customer_id_live if is_live else sub.stripe_customer_id_test
            )
            rows.append(
                {
                    "org_id": org_id,
                    "org_name": getattr(sub.organization, "display_name", "") or "",
                    "plan": sub.plan,
                    "status": sub.status,
                    "period": period,
                    "platform_fee": preview["platform_fee"],
                    "usage_total": preview["usage_total"],
                    "credits_applied": preview["credits_applied"],
                    "total": total,
                    "stripe_customer": customer_id or "",
                    "card_on_file": sub.card_last_4_digits or "",
                    "wallet_balance": sub.wallet_balance,
                }
            )

        rows.sort(key=lambda r: r["total"], reverse=True)

        if options["csv"]:
            with open(options["csv"], "w", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=list(rows[0].keys()) if rows else []
                )
                writer.writeheader()
                writer.writerows(rows)
            self.stdout.write(f"Wrote {len(rows)} rows to {options['csv']}")
        else:
            writer = csv.DictWriter(
                sys.stdout,
                fieldnames=[
                    "org_id",
                    "org_name",
                    "plan",
                    "status",
                    "total",
                    "stripe_customer",
                    "card_on_file",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

        liable = [r for r in rows if r["total"] > 0]
        chargeable = [r for r in liable if r["stripe_customer"] and r["card_on_file"]]
        self.stdout.write(
            f"\nPeriod {period}: {len(liable)} liable orgs, "
            f"{len(chargeable)} chargeable (customer + card on file), "
            f"{len(liable) - len(chargeable)} need attention, {errors} errors"
        )
