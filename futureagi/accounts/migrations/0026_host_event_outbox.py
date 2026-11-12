"""The FA -> Omega EE revocation outbox and its capture triggers (READINESS lane 1.2a)."""

import uuid

from django.db import migrations, models

from tfc.migration_database import executor_alias_scope


def install_capture(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        from accounts.host_events.capture_sql import install

        install(schema_editor)


def uninstall_capture(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        from accounts.host_events.capture_sql import uninstall

        uninstall(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        (
            "accounts",
            "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="HostEventOutbox",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "event_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("source", models.CharField(max_length=32)),
                ("operation", models.CharField(max_length=8)),
                ("user_id", models.UUIDField()),
                ("organization_id", models.UUIDField(blank=True, null=True)),
                ("workspace_id", models.UUIDField(blank=True, null=True)),
                ("old_state", models.JSONField(default=dict)),
                ("new_state", models.JSONField(blank=True, null=True)),
                ("captured_at", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("delivered", "Delivered"),
                            ("skipped", "Skipped"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("event", models.JSONField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField()),
                ("lease_until", models.DateTimeField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("ee_received_at_ms", models.BigIntegerField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=64)),
            ],
            options={
                "db_table": "accounts_host_event_outbox",
                "indexes": [
                    models.Index(
                        fields=["status", "next_attempt_at"], name="host_event_due"
                    ),
                    models.Index(
                        fields=["status", "captured_at"], name="host_event_age"
                    ),
                ],
            },
        ),
        migrations.RunPython(install_capture, uninstall_capture),
    ]
