# Generated for lane CN11 + CN24 (MCP-APPS-PLAN.md wave MCP-W4), 2026-10-25

"""A connect in progress records whose account it signs in: the member's own
(user), or the organization's one shared account an Owner or Admin signs in
(shared, CN24). One column with a default; no data moves (every existing row
signs in its member's own account)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('prism', '0059_mcp_connect_org_client'),
    ]

    operations = [
        migrations.AddField(
            model_name='agentccmcpconnectstate',
            name='mode',
            field=models.CharField(choices=[('user', 'User'), ('shared', 'Shared')], default='user', max_length=8),
        ),
    ]
