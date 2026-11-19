from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0127_skills_legacy_spend_policy")]

    operations = [
        migrations.AlterField(
            model_name="hubskillusagedaily",
            name="channel",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("api", "api"),
                    ("download", "download"),
                    ("mcp", "mcp"),
                    ("sdk", "sdk"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="hubskillusagedaily",
            name="sdk_version",
            field=models.CharField(max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="hubskillusagedaily",
            name="tracing_mode",
            field=models.CharField(
                max_length=9,
                null=True,
                choices=[
                    ("on", "on"),
                    ("load_only", "load_only"),
                    ("off", "off"),
                    ("no_otel", "no_otel"),
                ],
            ),
        ),
    ]
