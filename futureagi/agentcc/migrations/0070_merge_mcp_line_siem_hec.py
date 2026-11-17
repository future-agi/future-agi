"""Join the SIEM HEC choice with the MCP-LINE vendor/adapter/SIEM chain; no operations."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0065_siem_hec"),
        ("prism", "0069_merge_mcp_vendor_adapters_siem"),
    ]

    operations = []
