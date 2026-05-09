"""
Migration: make EndUser.user_id_type non-nullable with default "custom".

Backfills existing NULL rows before altering the column so no data is lost
and the constraint cannot be violated during the migration itself.

Issue: #305 — NULL user_id_type breaks unique_together deduplication because
NULL != NULL in SQL, allowing duplicate (project, organization, user_id, NULL)
rows to coexist.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0073_merge_20260428_1653"),
    ]

    operations = [
        # 1. Backfill existing NULLs so the ALTER below succeeds without data loss.
        migrations.RunSQL(
            sql="UPDATE tracer_enduser SET user_id_type = 'custom' WHERE user_id_type IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
        # 2. Alter column: drop nullable, set default.
        migrations.AlterField(
            model_name="enduser",
            name="user_id_type",
            field=models.CharField(
                choices=[
                    ("email", "Email"),
                    ("phone", "Phone"),
                    ("uuid", "UUID"),
                    ("custom", "Custom"),
                ],
                default="custom",
                max_length=50,
            ),
        ),
    ]
