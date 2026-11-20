from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0097_eval_logger_task_created_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="observabilityprovider",
            name="poll_state",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
