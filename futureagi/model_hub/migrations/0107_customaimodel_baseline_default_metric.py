import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0106_tools_workspace_uniqueness"),
    ]

    operations = [
        migrations.AddField(
            model_name="customaimodel",
            name="baseline_model_environment",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Production", "Production"),
                    ("Training", "Training"),
                    ("Validation", "Validation"),
                    ("Corpus", "Corpus"),
                ],
                default=None,
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="customaimodel",
            name="baseline_model_version",
            field=models.CharField(
                blank=True, default=None, max_length=255, null=True
            ),
        ),
        migrations.AddField(
            model_name="customaimodel",
            name="default_metric",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="custom_defaulted_by",
                to="model_hub.metric",
            ),
        ),
    ]
