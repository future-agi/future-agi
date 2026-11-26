import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("simulate", "0116_agent_fix_issue_job"),
    ]

    operations = [
        migrations.CreateModel(
            name="HarnessLockedVersion",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("label", models.CharField(max_length=32)),
                ("locked_at", models.DateTimeField()),
                ("unlocked_at", models.DateTimeField(blank=True, null=True)),
                ("train_count", models.PositiveIntegerField()),
                ("test_count", models.PositiveIntegerField()),
                (
                    "environment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="simulate.hostedharnessjob",
                    ),
                ),
                (
                    "environment_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="+",
                        to="simulate.harnessenvironmentversion",
                    ),
                ),
                (
                    "locked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "suite_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="+",
                        to="simulate.harnesssuiteversion",
                    ),
                ),
                (
                    "unlocked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "simulate_harness_locked_version",
                "indexes": [
                    models.Index(
                        fields=["environment", "-locked_at"],
                        name="idx_hlockver_env_locked",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("unlocked_at__isnull", True)),
                        fields=("environment_version", "suite_version"),
                        name="uniq_harness_lockver_pair",
                    ),
                    models.UniqueConstraint(
                        fields=("environment", "label"),
                        name="uniq_harness_lockver_label",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="HarnessTaskSplit",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
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
                    "split",
                    models.CharField(
                        choices=[("train", "Train"), ("test", "Test")], max_length=8
                    ),
                ),
                (
                    "first_locked_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="simulate.harnesslockedversion",
                    ),
                ),
                (
                    "scenario",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="simulate.hostedharnessscenario",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_harness_task_split",
            },
        ),
        migrations.CreateModel(
            name="HarnessLockedVersionMember",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
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
                    "split",
                    models.CharField(
                        choices=[("train", "Train"), ("test", "Test")], max_length=8
                    ),
                ),
                (
                    "locked_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="simulate.harnesslockedversion",
                    ),
                ),
                (
                    "scenario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="+",
                        to="simulate.hostedharnessscenario",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_harness_locked_version_member",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("locked_version", "scenario"),
                        name="uniq_harness_lockver_member",
                    )
                ],
            },
        ),
    ]
