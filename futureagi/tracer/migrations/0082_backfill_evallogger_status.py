"""Backfill EvalLogger.status for pre-existing rows.

Migration 0081 added the ``status`` field with default='completed', so every
existing row — including error rows — got status='completed'. This migration
corrects rows where ``error=True`` to ``status='failed'``.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0081_evallogger_status_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE tracer_eval_logger
                SET status = 'failed'
                WHERE error = true AND status = 'completed';
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
