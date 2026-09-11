from django.db import migrations


def _backfill(apps, schema_editor):
    from model_hub.management.commands.backfill_score_tracer_project import (
        backfill_tracer_project_ids,
    )

    backfill_tracer_project_ids()


def _noop(apps, schema_editor):
    # Irreversible data backfill; nothing to undo (leaves values in place).
    pass


class Migration(migrations.Migration):
    # Batched, streamed backfill — not a single transaction.
    atomic = False

    dependencies = [
        ('model_hub', '0119_score_tracer_project_index_concurrent'),
    ]

    operations = [
        migrations.RunPython(_backfill, _noop),
    ]
