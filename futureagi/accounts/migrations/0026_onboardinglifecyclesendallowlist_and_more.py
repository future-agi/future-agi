import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0025_onboarding_lifecycle_dry_run'),
    ]

    operations = [
        migrations.CreateModel(
            name='OnboardingLifecycleSendAllowlist',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('scope_type', models.CharField(choices=[('user', 'user'), ('workspace', 'workspace'), ('organization', 'organization'), ('domain', 'domain')], max_length=32)),
                ('scope_value', models.CharField(max_length=255)),
                ('campaign_group', models.CharField(blank=True, max_length=64, null=True)),
                ('environment', models.CharField(db_index=True, default='local', max_length=32)),
                ('enabled', models.BooleanField(db_index=True, default=True)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_onboarding_lifecycle_send_allowlists', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'accounts_onboarding_lifecycle_send_allowlist',
                'ordering': ('scope_type', 'scope_value', 'campaign_group'),
                'indexes': [models.Index(fields=['environment', 'enabled', 'scope_type'], name='onb_send_allow_env_scope'), models.Index(fields=['scope_type', 'scope_value'], name='onb_send_allow_scope_value')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('campaign_group__isnull', False), ('deleted', False)), fields=('scope_type', 'scope_value', 'campaign_group', 'environment'), name='onb_send_allow_unique_group'), models.UniqueConstraint(condition=models.Q(('campaign_group__isnull', True), ('deleted', False)), fields=('scope_type', 'scope_value', 'environment'), name='onb_send_allow_unique_scope')],
            },
        ),
        migrations.CreateModel(
            name='OnboardingLifecycleSendLog',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('campaign_key', models.CharField(db_index=True, max_length=96)),
                ('campaign_group', models.CharField(db_index=True, max_length=64)),
                ('template_key', models.CharField(max_length=96)),
                ('template_version', models.CharField(max_length=32)),
                ('primary_path', models.CharField(blank=True, max_length=32, null=True)),
                ('activation_stage', models.CharField(max_length=96)),
                ('recommended_action_id', models.CharField(blank=True, max_length=96, null=True)),
                ('target_success_event', models.CharField(blank=True, max_length=96, null=True)),
                ('target_route', models.TextField()),
                ('click_url', models.TextField(blank=True, default='')),
                ('status', models.CharField(choices=[('queued', 'queued'), ('sent', 'sent'), ('failed', 'failed'), ('clicked', 'clicked'), ('completed', 'completed'), ('suppressed', 'suppressed')], db_index=True, default='queued', max_length=16)),
                ('suppression_reason', models.CharField(blank=True, max_length=64, null=True)),
                ('provider_status', models.CharField(blank=True, max_length=32, null=True)),
                ('provider_message_id', models.CharField(blank=True, max_length=255, null=True)),
                ('failure_reason', models.TextField(blank=True, null=True)),
                ('queued_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('clicked_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('unsubscribed_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('evaluation_log', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='send_logs', to='accounts.onboardinglifecycleevaluationlog')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onboarding_lifecycle_send_logs', to='accounts.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onboarding_lifecycle_send_logs', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='onboarding_lifecycle_send_logs', to='accounts.workspace')),
            ],
            options={
                'db_table': 'accounts_onboarding_lifecycle_send_log',
                'ordering': ('-created_at',),
                'indexes': [models.Index(fields=['user', '-created_at'], name='onb_send_user_ts'), models.Index(fields=['workspace', 'campaign_key', '-created_at'], name='onb_send_ws_campaign_ts'), models.Index(fields=['campaign_key', 'status', '-created_at'], name='onb_send_campaign_status'), models.Index(fields=['target_success_event', 'status'], name='onb_send_target_status'), models.Index(fields=['sent_at'], name='onb_send_sent_at'), models.Index(fields=['clicked_at'], name='onb_send_clicked_at')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('deleted', False)), fields=('evaluation_log', 'campaign_key', 'user', 'workspace'), name='onb_send_unique_eval')],
            },
        ),
    ]
