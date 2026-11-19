import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("simulate", "0113_merge_actors_and_evidence_import"),
        ("tracer", "0135_eval_put_back"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evidenceimport",
            name="source_kind",
            field=models.CharField(
                choices=[
                    ("session", "Session"),
                    ("call", "Call"),
                    ("trace", "Trace"),
                    ("transcript", "Transcript"),
                    ("dataset", "Dataset rows"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="evidenceimport",
            name="source_issue",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="epoch_imports",
                to="tracer.traceerrorgroup",
            ),
        ),
        migrations.CreateModel(
            name="AgentFixIssueJob",
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
                ("sample", models.JSONField(default=list)),
                ("launch", models.JSONField(blank=True, null=True)),
                ("ending", models.JSONField(blank=True, null=True)),
                (
                    "baseline_run",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="issue_job_baseline",
                        to="simulate.hostedharnessjob",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "environment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="simulate.hostedharnessjob",
                    ),
                ),
                (
                    "evidence_import",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="issue_job",
                        to="simulate.evidenceimport",
                    ),
                ),
                (
                    "fix_run",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="issue_job",
                        to="simulate.agentfixrun",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_agent_fix_issue_job",
                "indexes": [
                    models.Index(
                        fields=["environment", "-created_at"],
                        name="idx_issue_job_env_created",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("baseline_run__isnull", False),
                            ("deleted", False),
                            ("ending__isnull", True),
                            ("fix_run__isnull", True),
                        ),
                        fields=("environment",),
                        name="uniq_measuring_issue_job_per_environment",
                    )
                ],
            },
        ),
    ]
