"""Bind managed-credit Checkout sessions to the org that opened them (MUST-FIX G).

Hand-written CreateModel (the pattern of 0052_auto_reload_consent); a new
table only, no data migration. makemigrations prism --check and a fresh
migrate --plan on E2B are the gates.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "accounts",
            "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more",
        ),
        ("prism", "0078_merge_dispatch_claim_webhook_at_rest"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccCheckoutBinding",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("session_digest", models.CharField(max_length=64, unique=True)),
                ("amount_micros", models.BigIntegerField()),
                ("currency", models.CharField(default="usd", max_length=3)),
                ("livemode", models.BooleanField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agentcc_checkout_bindings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agentcc_checkout_bindings",
                        to="accounts.organization",
                    ),
                ),
            ],
            options={
                "db_table": "agentcc_checkout_binding",
                "indexes": [
                    models.Index(
                        fields=["organization", "created_at"],
                        name="agentcc_ckout_bind_org_at",
                    ),
                ],
            },
        ),
    ]
