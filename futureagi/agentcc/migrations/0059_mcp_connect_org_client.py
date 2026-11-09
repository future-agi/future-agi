# Generated for lane CN11 + CN24 (MCP-APPS-PLAN.md wave MCP-W4), 2026-10-25

"""A connect in progress names the organization's own or the platform's OAuth
client it signs in as, and how that client authenticates. Columns only; no
data moves (a connect lives 10 minutes, and every existing row keeps its
public client)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('prism', '0058_mcp_upstream_oauth'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentccmcpconnectstate',
            name='client_kind',
            field=models.CharField(choices=[('cimd', 'Cimd'), ('dcr', 'Dcr'), ('org', 'Org'), ('platform', 'Platform')], max_length=8),
        ),
        migrations.AddField(
            model_name='agentccmcpconnectstate',
            name='client_auth',
            field=models.CharField(choices=[('none', 'None'), ('client_secret_basic', 'Basic'), ('client_secret_post', 'Post')], default='none', max_length=24),
        ),
    ]
