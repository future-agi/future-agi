"""Join adapter grant provenance with the SIEM/revision chain; no operations."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0063_mcp_grant_org_only"),
        ("prism", "0064_siem_export"),
    ]

    operations = []
