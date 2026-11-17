"""Add the permanent, unique explicit-dispatch replay barrier."""

from django.db import migrations, models
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied


class Migration(migrations.Migration):
    dependencies = [
        ('prism', '0084_checkout_actor_identifier'),
    ]

    # c2.5 recorded this as prism.0075_dispatch_claim.
    operations = [SkipDatabaseIfLegacyApplied(legacy=('prism', '0075_dispatch_claim'), operations=[
        migrations.CreateModel(
            name="AgentccDispatchClaim",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "agentcc_dispatch_claim"},
        ),
    ])]
