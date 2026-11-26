from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0122_agent_fix_candidate_changed_files"),
    ]

    operations = [
        migrations.AddField(
            model_name="harnessenvironmentversion",
            name="browser_allowed_sites",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
