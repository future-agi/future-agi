from django.db import migrations, models


class Migration(migrations.Migration):
    """A database default for UserAlertMonitor.alert_when.

    0123 added it NOT NULL without one, so a build from before 0123 creates
    monitors without the column and fails, during a rollout and after a
    rollback. The default equals the field's existing default, and setting it
    is a catalog-only change.
    """

    dependencies = [("tracer", "0143_merge_feed_escalation_and_metrics")]

    operations = [
        migrations.AlterField(
            model_name="useralertmonitor",
            name="alert_when",
            field=models.CharField(
                choices=[
                    ("point", "Point estimate"),
                    ("confident", "Whole 95% interval"),
                ],
                db_default="point",
                default="point",
                max_length=16,
            ),
        ),
    ]
