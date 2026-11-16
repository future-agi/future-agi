from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0113_merge_actors_and_evidence_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="callexecutionsnapshot",
            name="call_metadata",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
