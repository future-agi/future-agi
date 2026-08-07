import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DeploymentTelemetryState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("singleton_key", models.IntegerField(default=1, unique=True)),
                (
                    "instance_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "instance_secret",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("telemetry_disabled", models.BooleanField(default=False)),
                ("registered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "registration_kind",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("minimal_disabled", "Minimal disabled"),
                            ("full", "Full"),
                        ],
                        default="",
                        max_length=32,
                    ),
                ),
                (
                    "registration_status",
                    models.CharField(
                        choices=[("idle", "Idle"), ("in_progress", "In progress")],
                        default="idle",
                        max_length=20,
                    ),
                ),
                (
                    "registration_claimed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "registration_metadata",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "last_registration_attempt_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "last_registration_error",
                    models.TextField(blank=True, default=""),
                ),
                (
                    "last_heartbeat_attempt_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "last_heartbeat_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "last_heartbeat_window_start",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "last_heartbeat_window_end",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "deployment_telemetry_state",
                "verbose_name": "deployment telemetry state",
                "verbose_name_plural": "deployment telemetry state",
            },
        ),
    ]
