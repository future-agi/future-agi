from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0141_native_experiment_run_bindings")]

    operations = [
        migrations.AddField(
            model_name="experimentstable",
            name="description",
            field=models.TextField(blank=True, default="", db_default="", max_length=4000),
        ),
    ]
