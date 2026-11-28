from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0113_merge_agent_layer_and_evidence_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentdefinition",
            name="source_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
