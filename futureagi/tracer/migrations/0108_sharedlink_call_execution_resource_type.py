from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0107_investigation_attribution_explanation")]

    operations = [
        migrations.AlterField(
            model_name="sharedlink",
            name="resource_type",
            field=models.CharField(
                choices=[
                    ("trace", "Trace"),
                    ("dashboard", "Dashboard"),
                    ("eval_run", "Eval Run"),
                    ("dataset", "Dataset"),
                    ("project", "Project"),
                    ("call_execution", "Call Execution"),
                ],
                max_length=20,
            ),
        ),
    ]
