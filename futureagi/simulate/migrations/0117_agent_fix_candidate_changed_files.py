"""A candidate's changed files, counted once when it is stored, so reading an epoch never
reads its patch. A nullable column only; 0118 counts the candidates stored before it."""

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
