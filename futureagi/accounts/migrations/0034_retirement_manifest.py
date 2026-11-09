from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0033_batch_submission_intent")]
    operations = [
        migrations.CreateModel(
            name="RetirementManifest",
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
                ("service", models.CharField(max_length=24)),
                ("object_id", models.CharField(max_length=64)),
                ("action", models.CharField(max_length=32)),
                ("prior_state", models.JSONField(default=dict)),
                ("applied_state", models.JSONField(default=dict)),
                ("applied_at", models.DateTimeField()),
                ("reverted_at", models.DateTimeField(null=True)),
                ("superseded_by", models.CharField(blank=True, max_length=64)),
                ("confirmed", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "accounts_retirement_manifest",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("service", "object_id", "action"),
                        name="retirement_object_action_unique",
                    )
                ],
            },
        )
    ]
