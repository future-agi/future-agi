"""Dataset metadata is an independent map, with no data rewrite."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0144_workspace_tags"),
    ]

    operations = [
        migrations.AddField(
            model_name="dataset",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
