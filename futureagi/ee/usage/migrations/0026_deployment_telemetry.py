import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("usage", "0025_organizationsubscription_plan_changed_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeploymentTelemetryInstance",
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
                ("instance_id", models.UUIDField(unique=True)),
                ("telemetry_disabled", models.BooleanField(default=False)),
                (
                    "registration_kind",
                    models.CharField(
                        choices=[
                            ("minimal_disabled", "Minimal disabled"),
                            ("full", "Full"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "instance_secret",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("user_emails", models.JSONField(blank=True, default=list)),
                ("user_domains", models.JSONField(blank=True, default=list)),
                ("version_at_registration", models.CharField(max_length=100)),
                ("current_version", models.CharField(max_length=100)),
                ("deployment_type", models.CharField(max_length=50)),
                ("first_seen_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField()),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["-last_seen_at"],
                        name="deploy_inst_seen_idx",
                    ),
                    models.Index(
                        fields=["registration_kind"],
                        name="deploy_inst_kind_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="DeploymentTelemetryHeartbeat",
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
                ("window_start", models.DateTimeField()),
                ("window_end", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("version", models.CharField(max_length=100)),
                (
                    "active_users_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "traces_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "spans_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "projects_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "eval_logger_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "model_hub_evaluations_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "dataset_eval_runs_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "total_evaluations_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "simulation_runs_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "simulation_calls_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "experiments_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "gateway_requests_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "datasets_count",
                    models.PositiveBigIntegerField(blank=True, default=None, null=True),
                ),
                ("features_used", models.JSONField(blank=True, default=list)),
                (
                    "instance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="heartbeats",
                        to="usage.deploymenttelemetryinstance",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["instance", "-received_at"],
                        name="deploy_hb_recv_idx",
                    ),
                    models.Index(
                        fields=["instance", "window_start", "window_end"],
                        name="deploy_hb_window_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("instance", "window_start", "window_end"),
                        name="uniq_deploy_heartbeat_window",
                    )
                ],
            },
        ),
    ]
