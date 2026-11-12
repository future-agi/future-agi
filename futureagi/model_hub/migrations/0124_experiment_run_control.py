from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0123_usereval_is_experiment_default")]
    operations = [
        migrations.CreateModel(
            name="ExperimentRunControl",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("workflow_id", models.CharField(max_length=255)),
                ("workflow_run_id", models.UUIDField(unique=True)),
                ("config_id", models.UUIDField()),
                ("kind", models.CharField(max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("experiment_dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="control_executions", to="model_hub.experimentdatasettable")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
