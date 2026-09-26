from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0109_simulation_debug_analysis_per_call"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="traceinvestigationreport",
            name="unique_current_simulation_investigation",
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationreport",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("is_current", True),
                    ("deleted", False),
                    ("test_execution__isnull", False),
                ),
                fields=("project", "job"),
                name="unique_current_simulation_investigation",
            ),
        ),
    ]
