from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0101_agent_production_project"),
    ]

    operations = [
        migrations.AlterField(
            model_name="harnessagentversionruntime",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("creation", "Creation"),
                    ("push", "Push"),
                    ("upload", "Upload"),
                    ("prompt", "Prompt"),
                    ("agent_payload", "Agent payload"),
                    ("credentials", "Credentials"),
                    ("provider_sync", "Provider sync"),
                    ("backfill", "Backfill"),
                    ("connect", "Connect"),
                    ("sync", "Sync"),
                ],
                max_length=24,
            ),
        ),
    ]
