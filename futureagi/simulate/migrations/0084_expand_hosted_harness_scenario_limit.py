from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0083_hostedharnessjob_workspace"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="hostedharnessjob",
            name="harness_job_scenario_count_1_10",
        ),
        migrations.AddConstraint(
            model_name="hostedharnessjob",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    scenario_count__gte=1,
                    scenario_count__lte=200,
                ),
                name="harness_job_scenario_count_1_200",
            ),
        ),
    ]
