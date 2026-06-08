import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0021_merge_20260526_0921'),
    ]

    operations = [
        migrations.CreateModel(
            name='OnboardingActivationEvent',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_name', models.CharField(db_index=True, max_length=96)),
                ('product_path', models.CharField(blank=True, default='', max_length=32)),
                ('activation_stage', models.CharField(blank=True, default='', max_length=96)),
                ('source', models.CharField(blank=True, default='', max_length=64)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('is_sample', models.BooleanField(db_index=True, default=False)),
                ('idempotency_key', models.CharField(blank=True, db_index=True, max_length=160, null=True)),
                ('occurred_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onboarding_activation_events', to='accounts.organization')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='onboarding_activation_events', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onboarding_activation_events', to='accounts.workspace')),
            ],
            options={
                'db_table': 'accounts_onboarding_activation_event',
                'ordering': ('-occurred_at', '-created_at'),
                'indexes': [models.Index(fields=['organization', 'workspace', 'event_name', '-occurred_at'], name='onb_evt_org_ws_name_ts'), models.Index(fields=['organization', 'workspace', 'product_path', '-occurred_at'], name='onb_evt_org_ws_path_ts'), models.Index(fields=['user', 'event_name', '-occurred_at'], name='onb_evt_user_name_ts'), models.Index(fields=['workspace', 'is_sample', 'event_name'], name='onb_evt_ws_sample_name')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('deleted', False), ('idempotency_key__isnull', False)), fields=('organization', 'workspace', 'idempotency_key'), name='onb_evt_unique_idempotency')],
            },
        ),
    ]
