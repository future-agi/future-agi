import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0022_onboarding_activation_event'),
    ]

    operations = [
        migrations.CreateModel(
            name='OnboardingGoal',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('goal', models.CharField(max_length=64)),
                ('primary_path', models.CharField(max_length=32)),
                ('source', models.CharField(blank=True, default='', max_length=64)),
                ('reason', models.CharField(blank=True, default='', max_length=64)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('selected_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onboarding_goals', to='accounts.organization')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='selected_onboarding_goals', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onboarding_goals', to='accounts.workspace')),
            ],
            options={
                'db_table': 'accounts_onboarding_goal',
                'ordering': ('-selected_at', '-created_at'),
                'indexes': [models.Index(fields=['organization', 'workspace', 'is_active'], name='onb_goal_org_ws_active'), models.Index(fields=['organization', 'workspace', 'primary_path'], name='onb_goal_org_ws_path'), models.Index(fields=['user', '-selected_at'], name='onb_goal_user_selected')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('deleted', False), ('is_active', True)), fields=('organization', 'workspace'), name='onb_goal_unique_active_workspace')],
            },
        ),
    ]
