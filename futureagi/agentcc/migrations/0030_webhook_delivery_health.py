# Webhook delivery health and the terminal event statuses.
#
# Every new column has a db_default or is nullable, so pods still on the old
# code keep inserting webhooks while the deploy rolls out. The status change only adds
# choices; the column is unchanged. The indexes are built concurrently in 0031.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0029_request_log_unique_request_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentccwebhook",
            name="consecutive_failures",
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="disabled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="disabled_reason",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="dropped_events_total",
            field=models.PositiveBigIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="last_dropped_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="failing_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="delivery_lease_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="next_delivery_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentccwebhook",
            name="last_run_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="agentccwebhookevent",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("delivered", "Delivered"),
                    ("failed", "Failed"),
                    ("dead_letter", "Dead Letter"),
                    ("cancelled", "Cancelled"),
                    ("expired", "Expired"),
                    ("dropped", "Dropped"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
