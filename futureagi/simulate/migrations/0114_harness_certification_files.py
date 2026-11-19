from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simulate", "0104_hosted_job_capability_snapshot"),
        ("simulate", "0113_merge_actors_and_evidence_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="harnessassembledbundle",
            name="certification_files",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="harnessenvironmentversion",
            name="certification_files",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
