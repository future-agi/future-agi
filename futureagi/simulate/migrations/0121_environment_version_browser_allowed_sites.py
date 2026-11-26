from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0120_merge_cancel_confirmation_and_scenario_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="harnessenvironmentversion",
            name="browser_allowed_sites",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
