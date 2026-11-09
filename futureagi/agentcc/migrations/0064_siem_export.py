# Generated for lane G16-SIEM, 2026-10-27

"""SIEM export (G16-SIEM): an organization's export destinations and the
queue of exports on their way there. Tables only; no data moves, and every
destination a later write adds starts disabled."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more'),
        ('prism', '0063_mcp_credential_grant_revision'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentccSIEMDestination',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=120)),
                ('kind', models.CharField(choices=[('webhook', 'Webhook'), ('otlp_http', 'Otlp Http')], max_length=16)),
                ('url', models.CharField(max_length=2048)),
                ('streams', models.JSONField(default=list)),
                ('enabled', models.BooleanField(default=False)),
                ('auth_header', models.CharField(blank=True, default='', max_length=64)),
                ('encrypted_secret', models.TextField(blank=True, null=True)),
                ('secret_hint', models.CharField(blank=True, default='', max_length=8)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_delivered_at', models.DateTimeField(blank=True, null=True)),
                ('last_failed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name='agentcc_siem_destinations', to='accounts.organization')),
            ],
            options={
                'db_table': 'agentcc_siem_destination',
                'ordering': ['name'],
                'indexes': [models.Index(fields=['organization', 'enabled'], name='agentcc_siem_dest_org_idx')],
                'constraints': [models.UniqueConstraint(fields=('organization', 'name'), name='unique_agentcc_siem_dest_name')],
            },
        ),
        migrations.CreateModel(
            name='AgentccSIEMDelivery',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('stream', models.CharField(max_length=16)),
                ('event_ids', models.JSONField(default=list)),
                ('event_count', models.IntegerField(default=0)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('delivered', 'Delivered'), ('dead_letter', 'Dead Letter'), ('discarded', 'Discarded')], default='pending', max_length=16)),
                ('attempts', models.IntegerField(default=0)),
                ('max_attempts', models.IntegerField(default=6)),
                ('next_retry_at', models.DateTimeField(blank=True, null=True)),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('last_response_code', models.IntegerField(blank=True, null=True)),
                ('last_error', models.CharField(blank=True, default='', max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('destination', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='prism.agentccsiemdestination')),
                ('organization', models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name='agentcc_siem_deliveries', to='accounts.organization')),
            ],
            options={
                'db_table': 'agentcc_siem_delivery',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['organization', 'status', '-created_at'], name='agentcc_siem_dlv_org_idx'), models.Index(fields=['status', 'next_retry_at'], name='agentcc_siem_dlv_due_idx')],
            },
        ),
    ]
