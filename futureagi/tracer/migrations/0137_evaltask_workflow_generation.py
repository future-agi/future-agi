from django.db import migrations, models

# Adds a nullable column to the registered high-volume eval-task table.
UPDATE_SCOPE = ("postgres:tracer_eval_task",)


class Migration(migrations.Migration):
    dependencies = [("tracer", "0136_evaltask_failure_reason")]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="workflow_generation",
            field=models.UUIDField(editable=False, null=True),
        ),
    ]
