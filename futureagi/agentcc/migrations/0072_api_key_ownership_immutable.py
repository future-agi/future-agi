from django.conf import settings
from django.db import migrations, models

import agentcc.models.api_key_ownership


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0071_merge_request_log_consent_and_mcp"),
        ("prism", "0058_retire_ee_credentials"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="agentccapikey",
            options={
                "base_manager_name": "unscoped_objects",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AlterModelManagers(
            name="agentccapikey",
            managers=[
                ("objects", models.Manager()),
                ("unscoped_objects", models.Manager()),
            ],
        ),
        migrations.AlterField(
            model_name="agentccapikey",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=agentcc.models.api_key_ownership.unlink_deleted_key_user,
                related_name="agentcc_api_keys",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
