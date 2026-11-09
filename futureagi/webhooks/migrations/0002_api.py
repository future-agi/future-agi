# WH-2 (the API): the API's own ledger of writes, tests and redeliveries (webhooks.models.api).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0028_merge_authtoken_scope_and_simulation_agent_key'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('webhooks', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApiAction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('write', 'write'), ('test', 'test'), ('redeliver', 'redeliver'), ('bulk_redeliver', 'bulk_redeliver')], max_length=20)),
                ('attempt_n', models.IntegerField(null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('delivery', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='webhooks.delivery')),
                ('endpoint', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='webhooks.endpoint')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='accounts.organization')),
                ('workspace', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='accounts.workspace')),
            ],
            options={
                'db_table': 'webhooks_api_action',
                'indexes': [models.Index(fields=['organization', 'kind', 'created_at'], name='whapi_org_kind'), models.Index(fields=['delivery', 'attempt_n'], name='whapi_delivery_n')],
            },
        ),
    ]
