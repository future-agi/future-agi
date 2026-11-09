"""Fence connect workers and retain uncertain cleanup after lease expiry."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0067_mcp_vendor_retained_receipts")]
    operations = [
        migrations.AddField(
            model_name="agentccmcpvendorconnection",
            name="connect_lease_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorconnection",
            name="connect_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorcleanup",
            name="in_flight",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorcleanup",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
