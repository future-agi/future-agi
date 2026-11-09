# WH-1b operations (DESIGN.md sections 7.8 and 13.1-13.3): the shared send rate (it replaces WH-1a's token bucket
# columns) and the turned-off notice on the endpoint state row; the dispatcher heartbeats, the ops alert states and
# the gauge snapshot; the indexes the health pass and retention read by.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("webhooks", "0001_initial"),
    ]

    operations = [
        # The shared rate is rate_tat (a GCRA, webhooks.core.ratelimit); WH-1a's refilling bucket is gone.
        migrations.RemoveField(model_name="endpointstate", name="tokens"),
        migrations.RemoveField(model_name="endpointstate", name="tokens_at"),
        migrations.AddField(
            model_name="endpointstate",
            name="rate_tat",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="endpointstate",
            name="last_disabled_notice_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddIndex(
            model_name="attempt",
            index=models.Index(fields=["settled_at"], name="att_settled"),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                fields=["organization", "created_at"], name="evt_org_created"
            ),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                condition=models.Q(("fanned_out_at__isnull", False)),
                fields=["fanned_out_at"],
                name="evt_fanned_out",
            ),
        ),
        migrations.CreateModel(
            name="DispatcherBeat",
            fields=[
                (
                    "loop",
                    models.CharField(max_length=40, primary_key=True, serialize=False),
                ),
                ("beat_at", models.DateTimeField()),
                ("busy_normal", models.IntegerField(default=0)),
                ("busy_slow", models.IntegerField(default=0)),
                ("worker", models.CharField(blank=True, default="", max_length=200)),
            ],
            options={"db_table": "webhooks_dispatcher_beat"},
        ),
        migrations.CreateModel(
            name="OpsAlert",
            fields=[
                (
                    "id",
                    models.CharField(max_length=8, primary_key=True, serialize=False),
                ),
                ("active", models.BooleanField(default=False)),
                ("since", models.DateTimeField(null=True)),
                ("fired_at", models.DateTimeField(null=True)),
                ("cleared_at", models.DateTimeField(null=True)),
                ("detail", models.JSONField(default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "webhooks_ops_alert"},
        ),
        migrations.CreateModel(
            name="OpsSnapshot",
            fields=[
                (
                    "id",
                    models.CharField(max_length=20, primary_key=True, serialize=False),
                ),
                ("computed_at", models.DateTimeField()),
                ("data", models.JSONField(default=dict)),
            ],
            options={"db_table": "webhooks_ops_snapshot"},
        ),
    ]
