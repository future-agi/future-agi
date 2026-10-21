from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0091_merge_hosted_harness_dev"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="hostedharnessjob",
            name="harness_job_scenario_count_1_200",
        ),
        migrations.AddConstraint(
            model_name="hostedharnessjob",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    scenario_count__gte=1,
                    scenario_count__lte=5000,
                ),
                name="harness_job_scenario_count_1_5000",
            ),
        ),
    ]
