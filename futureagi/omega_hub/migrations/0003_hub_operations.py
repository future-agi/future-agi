# Omega Hub operations: durable email delivery, checker claim timestamps, and restoreable storage.

import uuid

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("omega_hub", "0002_version_strict_semver"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkjob",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="EmailOutbox",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("template", models.CharField(max_length=80)),
                ("recipients", models.JSONField(default=list)),
                ("subject", models.TextField()),
                ("body", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("dead", "Dead letter"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("dead_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "omega_hub_email_outbox"},
        ),
        migrations.AddIndex(
            model_name="emailoutbox",
            index=models.Index(fields=["status", "next_attempt_at"], name="omega_hub_email_due_idx"),
        ),
        migrations.AddIndex(
            model_name="emailoutbox",
            index=models.Index(fields=["status", "created_at"], name="omega_hub_email_status_idx"),
        ),
    ]
