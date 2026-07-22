"""One-time baseline backfill: stamp config_hash + correct status on legacy
live EvalLogger rows.

``atomic=False`` + the service's id-batched updates keep this off the single
giant-lock path on the multi-million-row table; the service is idempotent (it
only touches rows missing a hash / on the wrong status), so a retried deploy
resumes cleanly. The forward op imports the live hash service — unavoidable for
the transitive config hash, and acceptable for a one-time data backfill.
"""

from django.db import migrations


def _forward(apps, schema_editor):
    from tracer.services.eval_tasks.backfill import backfill_config_hash_and_status

    backfill_config_hash_and_status()


def _reverse(apps, schema_editor):
    # A baseline stamp isn't meaningfully reversible (we can't recover which rows
    # were null before); leave the data in place on rollback.
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("tracer", "0086_eval_entry_attempts"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
