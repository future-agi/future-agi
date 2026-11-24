"""A candidate's changed files, counted once when it is stored, so reading an epoch never
reads its patch. A nullable column only: a candidate stored before it has none, and
reads with no change line."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0116_agent_fix_issue_job"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentfixcandidate",
            name="changed_files",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
