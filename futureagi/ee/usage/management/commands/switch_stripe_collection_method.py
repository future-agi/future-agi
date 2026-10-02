"""One-off migration for the auto-charge rollout: flip existing Stripe
subscriptions from send_invoice to charge_automatically and backfill
card display fields (network logo, last 4). Orgs whose Stripe customer
has no default payment method are skipped and keep send_invoice."""

import stripe
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Switch existing Stripe subscriptions to charge_automatically "
        "(skips orgs without a default payment method)"
    )

    def add_arguments(self, parser):
        parser.add_argument("--org-id", default="", help="Restrict to one org")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from ee.cloud.billing.stripe_service import StripeService
        from ee.usage.models.usage import OrganizationSubscription

        qs = (
            OrganizationSubscription.objects.filter(deleted=False)
            .exclude(stripe_subscription_id__isnull=True)
            .exclude(stripe_subscription_id="")
        )
        if options["org_id"]:
            qs = qs.filter(organization_id=options["org_id"])

        is_live = StripeService._is_live()
        switched = skipped = errors = 0
        for sub in qs.iterator(chunk_size=200):
            customer_id = (
                sub.stripe_customer_id_live if is_live else sub.stripe_customer_id_test
            )
            pm_id = (
                StripeService._default_payment_method_id(customer_id)
                if customer_id
                else None
            )
            if not pm_id:
                self.stdout.write(
                    f"SKIP {sub.organization_id} — no default payment method"
                )
                skipped += 1
                continue

            if pm_id.startswith("pm_") and not options["dry_run"]:
                try:
                    pm = stripe.PaymentMethod.retrieve(pm_id)
                    StripeService.store_default_card_details(
                        str(sub.organization_id), pm
                    )
                except Exception as exc:
                    self.stderr.write(
                        f"WARN {sub.organization_id} card backfill failed: {exc}"
                    )

            for sub_id in filter(
                None, [sub.stripe_subscription_id, sub.stripe_fee_subscription_id]
            ):
                if options["dry_run"]:
                    self.stdout.write(f"DRY {sub.organization_id} {sub_id}")
                    continue
                try:
                    stripe.Subscription.modify(
                        sub_id, collection_method="charge_automatically"
                    )
                    switched += 1
                    self.stdout.write(f"OK {sub.organization_id} {sub_id}")
                except Exception as exc:
                    errors += 1
                    self.stderr.write(f"ERROR {sub.organization_id} {sub_id}: {exc}")

        self.stdout.write(
            f"Done. switched={switched} skipped={skipped} errors={errors}"
        )
