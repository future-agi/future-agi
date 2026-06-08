import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0026_onboardinglifecyclesendallowlist_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationChannel',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('type', models.CharField(choices=[('email_list', 'email_list'), ('slack_webhook', 'slack_webhook'), ('webhook', 'webhook')], db_index=True, max_length=32)),
                ('display_name', models.CharField(max_length=120)),
                ('target_identifier', models.CharField(blank=True, default='', max_length=255)),
                ('encrypted_config', models.TextField(blank=True, null=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('last_tested_at', models.DateTimeField(blank=True, null=True)),
                ('last_test_status', models.CharField(choices=[('untested', 'untested'), ('ready', 'ready'), ('failed', 'failed')], default='untested', max_length=32)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_notification_channels', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_channels', to='accounts.organization')),
                ('workspace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notification_channels', to='accounts.workspace')),
            ],
            options={
                'db_table': 'accounts_notification_channel',
                'ordering': ('type', 'display_name'),
                'indexes': [models.Index(fields=['organization', 'workspace', 'type', 'is_active'], name='notif_channel_org_ws_type'), models.Index(fields=['organization', 'is_active'], name='notif_channel_org_active')],
            },
        ),
        migrations.CreateModel(
            name='NotificationDeliveryLog',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('family', models.CharField(db_index=True, max_length=64)),
                ('source_type', models.CharField(max_length=64)),
                ('source_id', models.CharField(blank=True, max_length=128, null=True)),
                ('channel', models.CharField(db_index=True, max_length=32)),
                ('recipient_type', models.CharField(blank=True, default='', max_length=64)),
                ('recipient_identifier_masked', models.CharField(blank=True, default='', max_length=255)),
                ('notification_key', models.CharField(blank=True, default='', max_length=160)),
                ('idempotency_key', models.CharField(blank=True, db_index=True, max_length=220, null=True)),
                ('stage', models.CharField(blank=True, default='', max_length=96)),
                ('severity', models.CharField(blank=True, default='', max_length=32)),
                ('status', models.CharField(choices=[('eligible', 'eligible'), ('suppressed', 'suppressed'), ('sent', 'sent'), ('failed', 'failed'), ('clicked', 'clicked'), ('completed', 'completed')], db_index=True, max_length=16)),
                ('suppressed_reason', models.CharField(blank=True, max_length=64, null=True)),
                ('route_url', models.TextField(blank=True, default='')),
                ('sent_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('clicked_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('error', models.TextField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_delivery_logs', to='accounts.organization')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notification_delivery_logs', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notification_delivery_logs', to='accounts.workspace')),
            ],
            options={
                'db_table': 'accounts_notification_delivery_log',
                'ordering': ('-created_at',),
                'indexes': [models.Index(fields=['organization', 'workspace', 'family', '-created_at'], name='notif_log_org_ws_family'), models.Index(fields=['organization', 'status', '-created_at'], name='notif_log_org_status'), models.Index(fields=['family', 'channel', 'status'], name='notif_log_family_channel')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('deleted', False), ('idempotency_key__isnull', False)), fields=('organization', 'idempotency_key'), name='notif_log_unique_idempotency')],
            },
        ),
        migrations.CreateModel(
            name='NotificationPreference',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('family', models.CharField(choices=[('product_onboarding', 'product_onboarding'), ('daily_quality_digest', 'daily_quality_digest'), ('usage_budget', 'usage_budget'), ('gateway_alert', 'gateway_alert'), ('observe_monitor', 'observe_monitor'), ('eval_quality_alert', 'eval_quality_alert'), ('workspace_admin', 'workspace_admin')], db_index=True, max_length=64)),
                ('channel', models.CharField(choices=[('email', 'email'), ('in_app', 'in_app'), ('slack', 'slack'), ('webhook', 'webhook')], db_index=True, max_length=32)),
                ('enabled', models.BooleanField(db_index=True, default=True)),
                ('mute_until', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('frequency_cap_minutes', models.PositiveIntegerField(blank=True, null=True)),
                ('settings', models.JSONField(blank=True, default=dict)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_notification_preferences', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_preferences', to='accounts.organization')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_notification_preferences', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notification_preferences', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notification_preferences', to='accounts.workspace')),
            ],
            options={
                'db_table': 'accounts_notification_preference',
                'ordering': ('family', 'channel', '-updated_at'),
                'indexes': [models.Index(fields=['organization', 'workspace', 'family', 'channel'], name='notif_pref_org_ws_family'), models.Index(fields=['organization', 'user', 'family', 'channel'], name='notif_pref_org_user_family')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('deleted', False), ('user__isnull', True), ('workspace__isnull', True)), fields=('organization', 'family', 'channel'), name='notif_pref_unique_org'), models.UniqueConstraint(condition=models.Q(('deleted', False), ('user__isnull', True), ('workspace__isnull', False)), fields=('organization', 'workspace', 'family', 'channel'), name='notif_pref_unique_ws'), models.UniqueConstraint(condition=models.Q(('deleted', False), ('user__isnull', False), ('workspace__isnull', True)), fields=('organization', 'user', 'family', 'channel'), name='notif_pref_unique_user_org'), models.UniqueConstraint(condition=models.Q(('deleted', False), ('user__isnull', False), ('workspace__isnull', False)), fields=('organization', 'workspace', 'user', 'family', 'channel'), name='notif_pref_unique_user_ws')],
            },
        ),
    ]
