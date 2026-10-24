import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more"),
        ("prism", "0032_request_attempt"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccManagedUpstreamKey",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider_id", models.CharField(max_length=64)),
                ("encrypted_key", models.TextField()),
                ("key_hash", models.CharField(max_length=128)),
                ("label", models.CharField(blank=True, default="", max_length=255)),
                ("status", models.CharField(choices=[("active", "Active"), ("disabled", "Disabled"), ("revoked", "Revoked")], default="active", max_length=16)),
                ("limit_usd", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("rotated_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agentcc_managed_upstream_keys", to="accounts.organization")),
            ],
            options={
                "db_table": "agentcc_managed_upstream_key",
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("status", "active")), fields=("organization", "provider_id"), name="agentcc_one_active_upstream_key"),
                ],
            },
        ),
    ]
