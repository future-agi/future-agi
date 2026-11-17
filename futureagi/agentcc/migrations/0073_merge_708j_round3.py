"""Join the accepted cap-policy, MCP and key-ownership leaves for 708J."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0053_cap_policy_revision"),
        ("prism", "0071_mcp_connect_cancellation_generation"),
        ("prism", "0072_api_key_ownership_immutable"),
    ]

    operations = []
