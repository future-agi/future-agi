"""Saved monitors keep the threshold rule they were saved with.

Adds ``UserAlertMonitor.threshold_rule``. Its database default is "earlier":
every monitor that exists when this runs, and every monitor a server still on
the previous release inserts without the column while the update rolls out,
reads "earlier" and keeps the rule it was saved under. Its Python default is
"current", so a monitor the updated server saves uses the current rule. It has
to be ``db_default``: a plain default is dropped after AddField, and the older
server's INSERT would then fail NOT NULL.

Update safety (``scripts/check_update_safety.py``,
``deploy/update/UPDATE_SAFETY.md``): one AddField and no RunPython or RunSQL,
so there is no ``data_migration`` to declare. ``tracer_useralertmonitor`` is
not in ``deploy/update/high-volume-tables.json``, and the gate adds table risks
only for registered tables. The column is a NOT NULL varchar with a constant
``db_default``, so PostgreSQL adds it without rewriting the table and no CHECK
constraint is created. No UPDATE_SAFETY declaration is needed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0132_seed_alert_issue_occurrences_high_water")]

    operations = [
        migrations.AddField(
            model_name="useralertmonitor",
            name="threshold_rule",
            field=models.CharField(
                choices=[("earlier", "Earlier"), ("current", "Current")],
                db_default="earlier",
                default="current",
                help_text="How the saved thresholds are judged: earlier for monitors saved before the threshold update and not edited since, current otherwise.",
                max_length=16,
            ),
        ),
    ]
