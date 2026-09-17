from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("usage", "0024_increase_usage_display_precision"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationsubscription",
            name="plan_changed_at",
            field=models.BigIntegerField(
                blank=True,
                help_text=(
                    "Unix ts of the most recent plan change. Used by the "
                    "proration helper to compute mid-month platform-fee "
                    "fractions without a Stripe API round-trip. NULL for "
                    "orgs that haven't flipped plans since this column "
                    "shipped — proration falls back to Stripe metadata, "
                    "then ``created_at``."
                ),
                null=True,
            ),
        ),
    ]
