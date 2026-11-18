# No existing task is opted into dispatch.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0135_eval_put_back"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="instant_paused_before_start_at",
            field=models.DateTimeField(editable=False, null=True),
        ),
        migrations.CreateModel(
            name="EvalTaskStartRequest",
            fields=[
                (
                    "task",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="+",
                        serialize=False,
                        to="tracer.evaltask",
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("first_reserved_at", models.DateTimeField(null=True)),
                ("admitted_run_id", models.UUIDField(null=True)),
                ("superseded_at", models.DateTimeField(editable=False, null=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("claimed", "Claimed"),
                            ("accepted", "Accepted"),
                            ("exhausted", "Exhausted"),
                            ("start_failed", "Start retry exhausted"),
                            ("void", "Void"),
                        ],
                        default="pending",
                        max_length=12,
                    ),
                ),
                ("lease_until", models.DateTimeField(null=True)),
                ("claim_token", models.UUIDField(null=True)),
                ("last_outcome", models.CharField(default="requested", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "tracer_eval_task_start_request",
                "indexes": [
                    models.Index(
                        fields=["state", "lease_until"], name="eval_start_state_lease"
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(attempts__lte=2),
                        name="eval_start_max_two_attempts",
                    )
                ],
            },
        ),
    ]
