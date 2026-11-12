"""Optimistic vendor state and cascade-safe foreign keys on V10's own leaf."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0064_mcp_vendor_connections")]

    operations = [
        migrations.AddField(
            model_name="agentccmcpvendorbinding",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorconnection",
            name="consent_generation",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorconnection",
            name="connect_pending",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="agentccmcpvendorbinding",
            name="credential",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                to="prism.agentccmcpcredential",
            ),
        ),
        migrations.AlterField(
            model_name="agentccmcpvendorconnection",
            name="binding",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="connections",
                to="prism.agentccmcpvendorbinding",
            ),
        ),
    ]
