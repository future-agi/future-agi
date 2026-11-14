from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0097_eval_logger_task_created_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="useralertmonitor",
            name="webhook_url",
            field=models.URLField(blank=True, null=True),
        ),
    ]
