from django.db import migrations, models

# Adds a nullable column to the registered high-volume eval-task table.
UPDATE_SCOPE = ("postgres:tracer_eval_task",)


class Migration(migrations.Migration):
    dependencies = [("tracer", "0135_eval_put_back")]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="failure_reason",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
