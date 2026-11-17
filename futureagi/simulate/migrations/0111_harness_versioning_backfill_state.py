import uuid

import django.db.models.deletion
from django.db import migrations, models


STATES = [
    ("not_started", "Not started"),
    ("running", "Running"),
    ("complete", "Complete"),
    ("failed", "Failed"),
    ("off", "Off"),
]


class Migration(migrations.Migration):
    dependencies = [("simulate", "0110_seed_system_personas")]

    # Schema only. Object-store work belongs to the independent upgrade command.
    operations = [
        migrations.CreateModel(
            name="HarnessVersioningBackfillProgress",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=STATES, default="not_started", max_length=16
                    ),
                ),
                ("run_token", models.UUIDField(blank=True, editable=False, null=True)),
                ("attempt", models.PositiveIntegerField(default=0)),
                ("total", models.PositiveBigIntegerField(default=0)),
                ("processed", models.PositiveBigIntegerField(default=0)),
                ("succeeded", models.PositiveBigIntegerField(default=0)),
                ("failed", models.PositiveBigIntegerField(default=0)),
                ("skipped", models.PositiveBigIntegerField(default=0)),
                ("outcomes", models.JSONField(default=dict)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.organization",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HarnessVersioningBackfillOutcome",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("run_token", models.UUIDField(editable=False)),
                ("attempt", models.PositiveIntegerField(default=1)),
                (
                    "state",
                    models.CharField(
                        choices=STATES, default="not_started", max_length=16
                    ),
                ),
                ("outcome_code", models.CharField(blank=True, default="", max_length=64)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "environment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="simulate.hostedharnessjob",
                    ),
                ),
            ],
        ),
    ]
