from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0099_harness_versioning")]

    operations = [
        migrations.AddField(
            model_name="agentpromptoptimiserrun",
            name="tenant_lineage",
            field=models.JSONField(null=True, editable=False),
        ),
    ]
    # Deliberately no backfill: the historical FKs are not trustworthy provenance.
