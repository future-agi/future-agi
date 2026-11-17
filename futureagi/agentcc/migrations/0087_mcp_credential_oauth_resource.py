"""Retain each OAuth grant's exact resource; legacy grants remain unknown."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0086_merge_dispatch_claim_webhook_at_rest"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentccmcpcredential",
            name="oauth_resource",
            field=models.CharField(
                max_length=2048, null=True, blank=True, default=None, db_default=None
            ),
        ),
    ]
