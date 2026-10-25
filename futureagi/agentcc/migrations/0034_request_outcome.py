import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more"),
        ("prism", "0033_managed_upstream_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccRequestOutcome",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.CharField(max_length=64)),
                ("agent_run_id", models.CharField(blank=True, max_length=128, null=True)),
                ("agent_id", models.CharField(blank=True, max_length=128, null=True)),
                ("agent_step", models.CharField(blank=True, max_length=32, null=True)),
                ("model", models.CharField(blank=True, default="", max_length=128)),
                ("outcome", models.CharField(choices=[("ok", "Ok"), ("bad", "Bad"), ("empty", "Empty"), ("content_filter", "Content Filter"), ("verify_failed", "Verify Failed"), ("retried", "Retried")], max_length=16)),
                ("source", models.CharField(choices=[("classified", "Classified"), ("feedback", "Feedback"), ("prev_outcome", "Prev Outcome")], max_length=16)),
                ("score", models.FloatField(blank=True, null=True)),
                ("event_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("occurred_at", models.DateTimeField()),
                ("organization", models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name="agentcc_request_outcomes", to="accounts.organization")),
            ],
            options={
                "db_table": "agentcc_request_outcome",
                "indexes": [
                    models.Index(fields=["organization", "request_id"], name="agentcc_out_org_request_idx"),
                    models.Index(fields=["organization", "agent_run_id"], name="agentcc_out_org_run_idx"),
                    models.Index(fields=["organization", "occurred_at"], name="agentcc_out_org_occurred_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("source", "classified")), fields=("organization", "request_id", "source"), name="agentcc_outcome_one_classified"),
                ],
            },
        ),
    ]
