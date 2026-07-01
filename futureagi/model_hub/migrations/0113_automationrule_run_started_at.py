from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0112_eval_ground_truth_tenant_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="automationrule",
            name="run_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
