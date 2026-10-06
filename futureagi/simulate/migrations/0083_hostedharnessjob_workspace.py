import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0024_sosloginproxy"),
        ("simulate", "0082_merge_hosted_harness_outputs"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="workspace",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hosted_harness_jobs",
                to="accounts.workspace",
            ),
        ),
        migrations.AddIndex(
            model_name="hostedharnessjob",
            index=models.Index(
                fields=["organization", "workspace", "state"],
                name="idx_hjob_org_ws_state",
            ),
        ),
    ]
