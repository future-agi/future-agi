from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0117_merge_error_feed_and_simulation_project")]

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
                    ("error_feed_issue", "Error Feed Issue"),
                ],
                max_length=20,
            ),
        ),
    ]
