"""
Sync billing.yaml → PlanEntitlement DB rows.

Reads the entitlements section from billing.yaml and upserts default rows
(organization=NULL). Never touches per-org overrides (organization IS NOT NULL).

Usage:
    python manage.py sync_billing_config
    python manage.py sync_billing_config --dry-run
"""

from django.core.management.base import BaseCommand

from ee.usage.models.usage import PlanChoices, PlanEntitlement
from ee.usage.services.config import BillingConfig


class Command(BaseCommand):
    help = "Sync billing.yaml entitlements to PlanEntitlement table (defaults only, no org overrides)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing to DB.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        config = BillingConfig.reload()  # Force reload to pick up latest YAML

        created = 0
        updated = 0
        unchanged = 0
        errors = 0

        valid_plans = {c.value for c in PlanChoices}

        for feature, plan_map in config._schema.entitlements.items():
            for plan_key, value in plan_map.items():
                if plan_key not in valid_plans:
                    self.stderr.write(
                        self.style.WARNING(
                            f"  SKIP: {feature}/{plan_key} — invalid plan key"
                        )
                    )
                    errors += 1
                    continue

                # Determine if int or bool
                if isinstance(value, bool):
                    defaults = {"value_int": None, "value_bool": value}
                elif isinstance(value, int):
                    defaults = {"value_int": value, "value_bool": None}
                else:
                    self.stderr.write(
                        self.style.WARNING(
                            f"  SKIP: {feature}/{plan_key} — unsupported value type: {type(value)}"
                        )
                    )
                    errors += 1
                    continue

                if dry_run:
                    # Check if row exists
                    existing = PlanEntitlement.objects.filter(
                        feature=feature,
                        plan=plan_key,
                        organization__isnull=True,
                        deleted=False,
                    ).first()

                    if existing is None:
                        self.stdout.write(f"  CREATE: {feature}/{plan_key} = {value}")
                        created += 1
                    elif (existing.value_int != defaults.get("value_int")) or (
                        existing.value_bool != defaults.get("value_bool")
                    ):
                        old_val = (
                            existing.value_int
                            if existing.value_int is not None
                            else existing.value_bool
                        )
                        self.stdout.write(
                            f"  UPDATE: {feature}/{plan_key}: {old_val} → {value}"
                        )
                        updated += 1
                    else:
                        unchanged += 1
                else:
                    _, was_created = PlanEntitlement.objects.update_or_create(
                        feature=feature,
                        plan=plan_key,
                        organization=None,
                        defaults=defaults,
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1

        # Summary
        action = "Would sync" if dry_run else "Synced"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{action}: {created} created, {updated} updated, "
                f"{unchanged} unchanged, {errors} errors"
            )
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes written"))

        # Count org overrides (informational)
        override_count = PlanEntitlement.objects.filter(
            organization__isnull=False, deleted=False
        ).count()
        if override_count > 0:
            self.stdout.write(
                f"  ({override_count} per-org overrides exist — NOT touched)"
            )
