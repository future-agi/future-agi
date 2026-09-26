"""Give existing PAYG orgs the metered Stripe subscription they never had.

PAYG orgs carry no subscription: ``remove_addon`` cancels the add-on and
``_handle_subscription_deleted`` nulls the id. Meter events are addressed
to the customer, so their usage accumulates in Stripe meters with no
subscription item to invoice it against. New downgrades are covered by
``_ensure_payg_metered_subscription``; this backfills the existing ones.

Subscriptions are created with Stripe's default collection method
(charge_automatically). Orgs with no default payment method are skipped
and reported — creating one for them would only fail into dunning.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Create metered-only Stripe subscriptions for PAYG orgs that have none "
        "(skips orgs without a default payment method)"
    )

    def add_arguments(self, parser):
        parser.add_argument("--org-id", default="", help="Restrict to one org")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from django.db.models import Q
        from ee.cloud.billing.stripe_service import StripeService
        from ee.usage.models.usage import OrganizationSubscription, PlanChoices

        qs = OrganizationSubscription.objects.filter(
            Q(stripe_subscription_id__isnull=True) | Q(stripe_subscription_id=""),
            plan=PlanChoices.PAYG,
            deleted=False,
        )
        if options["org_id"]:
            qs = qs.filter(organization_id=options["org_id"])

        customer_field = StripeService._customer_id_field()
        created = skipped_no_customer = skipped_no_pm = errors = 0

        for sub in qs.iterator(chunk_size=200):
            org_id = str(sub.organization_id)
            customer_id = getattr(sub, customer_field)
            if not customer_id:
                self.stdout.write(f"SKIP {org_id} — no Stripe customer")
                skipped_no_customer += 1
                continue

            if not StripeService._has_default_payment_method(customer_id):
                self.stdout.write(f"SKIP {org_id} — no default payment method")
                skipped_no_pm += 1
                continue

            if options["dry_run"]:
                self.stdout.write(f"DRY {org_id} {customer_id}")
                continue

            try:
                sub_id = StripeService._ensure_payg_metered_subscription(sub)
            except Exception as exc:
                errors += 1
                self.stderr.write(f"ERROR {org_id}: {exc}")
                continue

            if sub_id:
                created += 1
                self.stdout.write(f"OK {org_id} {sub_id}")
            else:
                errors += 1
                self.stderr.write(f"ERROR {org_id}: subscription not created")

        self.stdout.write(
            f"Done. created={created} skipped_no_customer={skipped_no_customer} "
            f"skipped_no_payment_method={skipped_no_pm} errors={errors}"
        )
