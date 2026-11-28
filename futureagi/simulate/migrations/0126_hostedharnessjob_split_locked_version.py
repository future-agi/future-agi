import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    # Every run reads and writes the hosted-jobs table, so this transaction only adds
    # two nullable columns: catalog-only, with no scan. The locked version has no
    # database foreign key, which would scan the table under its lock; Django enforces
    # its RESTRICT. Its index builds concurrently in 0127.
    dependencies = [
        ("simulate", "0125_merge_eval_config_keys_and_agent_fix_outside_attempts"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="split",
            field=models.CharField(
                blank=True,
                choices=[("train", "Train"), ("test", "Test")],
                max_length=8,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="hostedharnessjob",
            name="locked_version",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="+",
                to="simulate.harnesslockedversion",
            ),
        ),
    ]
