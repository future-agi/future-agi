import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0032_service_grants")]
    operations = [
        migrations.CreateModel(
            name="BatchSubmissionIntent",
            fields=[
                (
                    "grant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        primary_key=True,
                        serialize=False,
                        to="accounts.servicegrant",
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(db_index=True)),
                ("resolved_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "accounts_batch_submission_intent"},
        ),
    ]
