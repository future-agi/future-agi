"""Join vendor lifecycle with the adapter/SIEM chain; no operations."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0065_merge_mcp_adapters_siem"),
        ("prism", "0068_mcp_vendor_connect_leases"),
    ]

    operations = []
