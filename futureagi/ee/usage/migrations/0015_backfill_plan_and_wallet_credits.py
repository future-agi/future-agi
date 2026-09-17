"""
Data migration: backfill plan, billing_interval, and CreditBalance from legacy tier data.

Phase 1.3 of pricing revamp — TH-3681

Three sequential operations (idempotent, reversible):
1. Populate `plan` from `subscription_tier.name`
2. Set `billing_interval='annual'` for yearly tier orgs
3. Create `CreditBalance` from `wallet_balance` for orgs with positive balance

Mapping (old → new):
- free → plan='free', billing_interval='monthly'
- basic → plan='payg', billing_interval='monthly'
- basic_yearly → plan='payg', billing_interval='annual'
- custom → plan='enterprise', billing_interval='monthly'

Runs AFTER 0014 which adds the CreditBalance schema and new fields.
"""

from django.db import migrations


WALLET_MIGRATION_DESCRIPTION = "Migrated from legacy wallet balance"
TIER_NAME_TO_PLAN = {
    "basic": "payg",
    "basic_yearly": "payg",
    "custom": "enterprise",
}


def populate_plan(apps, schema_editor):
    """Populate plan field from subscription_tier.name.

    Idempotent: only updates rows where plan is still the default 'free'.
    Uses tier_id lookup to avoid N+1 queries on tier name resolution.
    """
    OrgSub = apps.get_model("usage", "OrganizationSubscription")
    SubTier = apps.get_model("usage", "SubscriptionTier")

    tier_ids_by_plan = {}
    for tier_id, tier_name in SubTier.objects.values_list("id", "name"):
        plan = TIER_NAME_TO_PLAN.get(tier_name)
        if plan is None:
            continue
        tier_ids_by_plan.setdefault(plan, []).append(tier_id)

    updated = 0
    for plan, tier_ids in tier_ids_by_plan.items():
        updated += OrgSub.objects.filter(
            plan="free",
            subscription_tier_id__in=tier_ids,
            deleted=False,
        ).update(plan=plan)

    if updated:
        print(f"\n  Backfilled plan for {updated} OrganizationSubscription rows")


def set_annual_interval(apps, schema_editor):
    """Set billing_interval='annual' for orgs previously on basic_yearly tier.

    Idempotent: only updates rows where billing_interval is still 'monthly'.
    """
    OrgSub = apps.get_model("usage", "OrganizationSubscription")
    SubTier = apps.get_model("usage", "SubscriptionTier")

    yearly_tier_ids = list(
        SubTier.objects.filter(name="basic_yearly").values_list("id", flat=True)
    )

    if yearly_tier_ids:
        updated = OrgSub.objects.filter(
            subscription_tier_id__in=yearly_tier_ids,
            billing_interval="monthly",
            deleted=False,
        ).update(billing_interval="annual")

        if updated:
            print(f"\n  Set billing_interval='annual' for {updated} yearly orgs")


def migrate_wallet_to_credits(apps, schema_editor):
    """Create CreditBalance for orgs with wallet_balance > 0.

    Idempotent: skips if a CreditBalance with matching description already exists
    for this org (checks credit_type + description combo).

    Uses a single set-based INSERT ... SELECT statement to avoid per-row ORM
    round-trips when running against remote databases.
    """
    OrgSub = apps.get_model("usage", "OrganizationSubscription")
    CreditBalance = apps.get_model("usage", "CreditBalance")

    connection = schema_editor.connection
    quote_name = connection.ops.quote_name

    org_sub_table = quote_name(OrgSub._meta.db_table)
    credit_balance_table = quote_name(CreditBalance._meta.db_table)

    org_sub_org_col = quote_name(OrgSub._meta.get_field("organization").column)
    org_sub_wallet_col = quote_name(OrgSub._meta.get_field("wallet_balance").column)
    org_sub_deleted_col = quote_name(OrgSub._meta.get_field("deleted").column)

    credit_org_col = quote_name(CreditBalance._meta.get_field("organization").column)
    credit_type_col = quote_name(CreditBalance._meta.get_field("credit_type").column)
    original_amount_col = quote_name(
        CreditBalance._meta.get_field("original_amount").column
    )
    remaining_amount_col = quote_name(
        CreditBalance._meta.get_field("remaining_amount").column
    )
    currency_col = quote_name(CreditBalance._meta.get_field("currency").column)
    description_col = quote_name(CreditBalance._meta.get_field("description").column)
    issued_by_col = quote_name(CreditBalance._meta.get_field("issued_by").column)
    expires_at_col = quote_name(CreditBalance._meta.get_field("expires_at").column)
    created_at_col = quote_name(CreditBalance._meta.get_field("created_at").column)
    updated_at_col = quote_name(CreditBalance._meta.get_field("updated_at").column)
    deleted_col = quote_name(CreditBalance._meta.get_field("deleted").column)
    deleted_at_col = quote_name(CreditBalance._meta.get_field("deleted_at").column)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {credit_balance_table} (
                {credit_org_col},
                {credit_type_col},
                {original_amount_col},
                {remaining_amount_col},
                {currency_col},
                {description_col},
                {issued_by_col},
                {expires_at_col},
                {created_at_col},
                {updated_at_col},
                {deleted_col},
                {deleted_at_col}
            )
            SELECT
                org_sub.{org_sub_org_col},
                %s,
                CAST(org_sub.{org_sub_wallet_col} AS NUMERIC(10, 2)),
                CAST(org_sub.{org_sub_wallet_col} AS NUMERIC(10, 2)),
                %s,
                %s,
                %s,
                NULL,
                NOW(),
                NOW(),
                FALSE,
                NULL
            FROM {org_sub_table} org_sub
            WHERE org_sub.{org_sub_wallet_col} > 0
              AND org_sub.{org_sub_org_col} IS NOT NULL
              AND org_sub.{org_sub_deleted_col} = FALSE
              AND NOT EXISTS (
                  SELECT 1
                  FROM {credit_balance_table} credit_balance
                  WHERE credit_balance.{credit_org_col} = org_sub.{org_sub_org_col}
                    AND credit_balance.{credit_type_col} = %s
                    AND credit_balance.{description_col} = %s
              )
            """,
            [
                "prepaid",
                "usd",
                WALLET_MIGRATION_DESCRIPTION,
                "",
                "prepaid",
                WALLET_MIGRATION_DESCRIPTION,
            ],
        )
        migrated = cursor.rowcount

    if migrated:
        print(f"\n  Created {migrated} CreditBalance records")


def reverse_plan(apps, schema_editor):
    """Reset plan and billing_interval to defaults.

    Safe: old code reads subscription_tier FK which is unchanged.
    """
    OrgSub = apps.get_model("usage", "OrganizationSubscription")
    updated = (
        OrgSub.objects.filter(deleted=False)
        .exclude(plan="free")
        .update(
            plan="free",
            billing_interval="monthly",
        )
    )
    if updated:
        print(f"\n  Reversed plan/billing_interval for {updated} rows")


def reverse_annual_interval(apps, schema_editor):
    """Reset billing_interval to monthly for orgs that were on basic_yearly tier."""
    OrgSub = apps.get_model("usage", "OrganizationSubscription")
    SubTier = apps.get_model("usage", "SubscriptionTier")
    yearly_tier_ids = list(
        SubTier.objects.filter(name="basic_yearly").values_list("id", flat=True)
    )
    if yearly_tier_ids:
        OrgSub.objects.filter(
            subscription_tier_id__in=yearly_tier_ids,
            billing_interval="annual",
            deleted=False,
        ).update(billing_interval="monthly")


def reverse_credits(apps, schema_editor):
    """Delete CreditBalance records created by this migration only.

    Uses description as the migration marker to avoid deleting
    manually-created promotional credits.
    """
    CreditBalance = apps.get_model("usage", "CreditBalance")
    deleted, _ = CreditBalance.objects.filter(
        description=WALLET_MIGRATION_DESCRIPTION,
    ).delete()
    if deleted:
        print(f"\n  Deleted {deleted} migrated CreditBalance records")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("usage", "0014_eelicensegrant_eelicenseinstance_eeinstanceheartbeat"),
    ]

    operations = [
        migrations.RunPython(populate_plan, reverse_code=reverse_plan),
        migrations.RunPython(set_annual_interval, reverse_code=reverse_annual_interval),
        migrations.RunPython(migrate_wallet_to_credits, reverse_code=reverse_credits),
    ]
