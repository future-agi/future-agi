from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0111_grouping_budget_wait")]
    operations = [
        migrations.AddField(
            "tracegroupingwork",
            "failure_code",
            models.CharField(max_length=100, blank=True, db_default=""),
        ),
        migrations.AddField(
            "tracegroupingwork",
            "last_failure_at",
            models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            "tracegroupingwork",
            "retry_start_attempt",
            models.PositiveIntegerField(default=0, db_default=0),
        ),
        migrations.AddField(
            "tracegroupingwork",
            "retry_limit",
            models.PositiveSmallIntegerField(default=5, db_default=5),
        ),
        migrations.AddField(
            "tracegroupingattempt",
            "failure_code",
            models.CharField(max_length=100, blank=True, db_default=""),
        ),
    ]
