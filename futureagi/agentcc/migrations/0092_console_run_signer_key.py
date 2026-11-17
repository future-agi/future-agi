from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0091_request_log_error_source"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccConsoleRunSignerKey",
            fields=[
                (
                    "kid",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                ("deployment", models.CharField(db_index=True, max_length=64)),
                ("public_key", models.BinaryField(max_length=32)),
                ("created_by", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "revoked_by",
                    models.CharField(blank=True, default="", max_length=128),
                ),
            ],
            options={
                "db_table": "agentcc_console_run_signer_key",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(kid__regex="^[A-Za-z0-9._-]{1,64}$"),
                        name="console_run_signer_key_kid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(deployment__regex="^[A-Za-z0-9._-]{1,64}$"),
                        name="console_run_signer_key_deployment",
                    ),
                ],
            },
        ),
    ]
