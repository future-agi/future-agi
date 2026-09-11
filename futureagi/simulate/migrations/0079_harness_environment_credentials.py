import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0024_sosloginproxy"),
        ("simulate", "0078_agentdefinition_target_speaks_first"),
    ]

    operations = [
        migrations.CreateModel(
            name="HarnessCredentialFile",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("environment_name", models.CharField(max_length=255)),
                ("filename", models.CharField(default="credential", max_length=255)),
                ("content_type", models.CharField(default="application/octet-stream", max_length=255)),
                ("encrypted_content", models.TextField()),
                ("size", models.PositiveIntegerField()),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="harness_credential_files", to="accounts.organization")),
                ("workspace", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="harness_credential_files", to="accounts.workspace")),
            ],
            options={"db_table": "simulate_harness_credential_file", "ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="HarnessEnvironmentCredentials",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("harness_job_id", models.CharField(db_index=True, max_length=255, unique=True)),
                ("encrypted_environment", models.TextField(default="")),
                ("credential_file_ids", models.JSONField(blank=True, default=list)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="harness_environment_credentials", to="accounts.organization")),
                ("workspace", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="harness_environment_credentials", to="accounts.workspace")),
            ],
            options={"db_table": "simulate_harness_environment_credentials", "ordering": ("-created_at",)},
        ),
    ]
