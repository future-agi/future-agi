from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY can't run inside a transaction, and lets every run
    # read and write the hosted-jobs table while it builds. A failed build leaves an
    # invalid idx_hjob_locked_version: drop it before running this again.
    atomic = False

    dependencies = [
        ("simulate", "0126_hostedharnessjob_split_locked_version"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="hostedharnessjob",
            index=models.Index(
                fields=["locked_version"], name="idx_hjob_locked_version"
            ),
        ),
    ]
