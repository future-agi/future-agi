# A Recalculate's put-back is watched for an hour, so the eval-task sweep can
# retire a restored result whose parent a late-committing delete removed
# (TH-3119).
#
# Lock note: as 0133, the foreign key to tracer_eval_logger is created with the
# table, which takes a SHARE ROW EXCLUSIVE lock on tracer_eval_logger. It is
# brief (the new table is empty), but it queues behind any long transaction on
# tracer_eval_logger and blocks its writes meanwhile: run it in a normal
# deploy window.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0134_merge_eval_recalculation_and_sampling"),
    ]

    operations = [
        migrations.CreateModel(
            name="EvalPutBack",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("restored_at", models.DateTimeField(db_index=True)),
                (
                    "checked_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "result",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="tracer.evallogger",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_eval_put_back",
            },
        ),
    ]
