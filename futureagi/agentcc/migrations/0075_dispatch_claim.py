"""Add the permanent, unique explicit-dispatch replay barrier."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0074_api_key_provisioning_state")]

    operations = [
        migrations.CreateModel(
            name="AgentccDispatchClaim",
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
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "agentcc_dispatch_claim"},
        ),
    ]
