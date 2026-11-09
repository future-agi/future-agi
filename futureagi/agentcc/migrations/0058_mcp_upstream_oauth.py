# Generated for lane M25x (MCP-PLAN.md M25x), 2026-10-25

"""Per-user upstream OAuth for MCP servers (M25x): connects in progress, DCR
clients reused per authorization server, the audit of the gateway's token
fetches, and keys their owner marked personal. Tables only; no data moves."""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more'),
        ('prism', '0057_mcp_credential_origin'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentccMCPConnectState',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('server_id', models.CharField(max_length=64)),
                ('state_hash', models.CharField(max_length=64, unique=True)),
                ('cookie_hash', models.CharField(max_length=64)),
                ('encrypted_verifier', models.TextField()),
                ('issuer', models.CharField(max_length=512)),
                ('token_endpoint', models.CharField(max_length=2048)),
                ('client_id', models.CharField(max_length=2048)),
                ('client_kind', models.CharField(choices=[('cimd', 'Cimd'), ('dcr', 'Dcr')], max_length=8)),
                ('resource', models.CharField(max_length=2048)),
                ('redirect_uri', models.CharField(max_length=2048)),
                ('origin', models.CharField(max_length=2048)),
                ('scopes', models.JSONField(blank=True, default=list)),
                ('iss_required', models.BooleanField(default=False)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='accounts.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'agentcc_mcp_connect_state',
            },
        ),
        migrations.CreateModel(
            name='AgentccMCPUpstreamClient',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('issuer', models.CharField(max_length=512)),
                ('redirect_uri', models.CharField(max_length=2048)),
                ('client_id', models.CharField(max_length=2048)),
                ('registered_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'agentcc_mcp_upstream_client',
                'constraints': [models.UniqueConstraint(fields=('issuer', 'redirect_uri'), name='agentcc_mcp_upstream_client_one_per_issuer')],
            },
        ),
        migrations.CreateModel(
            name='AgentccMCPUpstreamTokenAudit',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('org_id', models.CharField(max_length=64)),
                ('user_id', models.CharField(max_length=64)),
                ('server_id', models.CharField(max_length=64)),
                ('outcome', models.CharField(max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('credential', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='prism.agentccmcpcredential')),
            ],
            options={
                'db_table': 'agentcc_mcp_upstream_token_audit',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['org_id', 'created_at'], name='agentcc_mcp_token_audit_org')],
            },
        ),
        migrations.CreateModel(
            name='AgentccMCPPersonalKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('api_key', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='mcp_personal', to='prism.agentccapikey')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'agentcc_mcp_personal_key',
            },
        ),
    ]
