from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0099_harness_versioning"),
    ]

    operations = [
        migrations.CreateModel(
            name="SandboxHarnessJob",
            fields=[
                ("job_id", models.TextField(primary_key=True, serialize=False)),
                ("deleted", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="sandbox_harness_jobs",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.CASCADE,
                        related_name="sandbox_harness_jobs",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_sandbox_harness_job",
                "indexes": [
                    models.Index(
                        fields=["organization", "workspace", "deleted"],
                        name="sandbox_job_tenant_idx",
                    )
                ],
            },
        ),
    ]
