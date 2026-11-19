from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0136_evaltask_failure_reason")]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="workflow_generation",
            field=models.UUIDField(editable=False, null=True),
        ),
    ]
