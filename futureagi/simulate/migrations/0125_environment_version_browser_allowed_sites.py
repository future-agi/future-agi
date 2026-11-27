from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0124_agent_fix_outside_attempts"),
    ]

    operations = [
        migrations.AddField(
            model_name="harnessenvironmentversion",
            name="browser_allowed_sites",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
