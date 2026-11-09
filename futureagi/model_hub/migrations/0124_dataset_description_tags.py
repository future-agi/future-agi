from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0123_usereval_is_experiment_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="dataset",
            name="description",
            field=models.TextField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="dataset",
            name="tags",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
