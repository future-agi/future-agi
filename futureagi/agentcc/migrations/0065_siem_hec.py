"""HEC choice only. MCP-LINE integration needs a merge with its 0065 leaf.

This lane's export is 0063; MCP-LINE composed that export as 0064_siem_export
and has 0065_merge_mcp_adapters_siem. Keep this migration immutable on the
lane; the integrator must resolve the dependency and add a merge migration.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0064_siem_export")]

    operations = [
        migrations.AlterField(
            model_name="agentccsiemdestination",
            name="kind",
            field=models.CharField(
                choices=[("webhook", "Webhook"), ("otlp_http", "Otlp Http"), ("hec", "Hec")],
                max_length=16,
            ),
        ),
    ]
