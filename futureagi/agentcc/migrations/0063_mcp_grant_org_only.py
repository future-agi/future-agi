"""A K2 connect (connector_k2) and the grant it stores record that they are
the org's own app registration's only, so the grant's revocation never reads
the server's later configuration to decide that. Columns only; no data moves:
the K2 runtime is off by default, and an existing grant keeps falling back to
its server's current entry."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0062_mcp_cleanup_receipts")]

    operations = [
        migrations.AddField(
            model_name="agentccmcpconnectstate",
            name="org_only",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.AddField(
            model_name="agentccmcpcredential",
            name="oauth_org_only",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
