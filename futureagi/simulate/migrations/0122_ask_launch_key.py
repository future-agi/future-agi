from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0121_merge_eval_result_ask_and_dev"),
    ]

    operations = [
        migrations.AddField(
            model_name="simulateask",
            name="launch_key",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="simulateask",
            constraint=models.UniqueConstraint(
                condition=models.Q(("launch_key__isnull", False)),
                fields=("organization", "launch_key"),
                name="uniq_simulate_ask_org_launch_key",
            ),
        ),
    ]
