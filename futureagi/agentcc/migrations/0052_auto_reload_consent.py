"""Add consent receipts without inferring authorization from legacy settings.

Hand-authored while cloud authentication is expired; makemigrations --check and
fresh migration application are mandatory VM gates before integration.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more"),
        ("prism", "0051_encrypt_alert_channel_urls"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccAutoReloadConsent",
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
                ("actor_identifier", models.UUIDField(editable=False)),
                ("consent_version", models.CharField(max_length=64)),
                ("accepted_at", models.DateTimeField()),
                ("threshold_micros", models.BigIntegerField()),
                ("amount_micros", models.BigIntegerField()),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agentcc_auto_reload_consents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agentcc_auto_reload_consents",
                        to="accounts.organization",
                    ),
                ),
            ],
            options={
                "db_table": "agentcc_auto_reload_consent",
                "indexes": [
                    models.Index(
                        fields=["organization", "accepted_at"],
                        name="agentcc_ar_consent_org_at",
                    ),
                ],
            },
        ),
    ]
