import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more"),
        ("prism", "0031_request_log_v2_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccRequestAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.CharField(max_length=255)),
                ("attempt_id", models.CharField(max_length=300, unique=True)),
                ("index", models.PositiveSmallIntegerField()),
                ("kind", models.CharField(choices=[("primary", "Primary"), ("retry", "Retry"), ("failover", "Failover"), ("model_fallback", "Model Fallback"), ("auto_fallback", "Auto Fallback"), ("request_fallback", "Request Fallback"), ("hedge", "Hedge"), ("selector", "Selector")], max_length=32)),
                ("reason", models.CharField(blank=True, default="", max_length=64)),
                ("provider", models.CharField(blank=True, default="", max_length=255)),
                ("model", models.CharField(blank=True, default="", max_length=255)),
                ("endpoint_id", models.CharField(blank=True, default="", max_length=255)),
                ("upstream_provider", models.CharField(blank=True, default="", max_length=255)),
                ("served_upstream_provider", models.CharField(blank=True, default="", max_length=64)),
                ("upstream_key_id", models.CharField(blank=True, default="", max_length=128)),
                ("credential_type", models.CharField(blank=True, default="", max_length=8)),
                ("status_code", models.IntegerField(blank=True, null=True)),
                ("outcome", models.CharField(choices=[("success", "Success"), ("error", "Error"), ("timeout", "Timeout"), ("cancelled", "Cancelled"), ("discarded", "Discarded"), ("skipped", "Skipped"), ("circuit_open", "Circuit Open")], max_length=16)),
                ("error_class", models.CharField(blank=True, default="", max_length=32)),
                ("error_origin", models.CharField(blank=True, default="", max_length=16)),
                ("error_message", models.CharField(blank=True, default="", max_length=512)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("latency_ms", models.IntegerField(blank=True, null=True)),
                ("ttfb_ms", models.IntegerField(blank=True, null=True)),
                ("prompt_tokens", models.IntegerField(blank=True, null=True)),
                ("completion_tokens", models.IntegerField(blank=True, null=True)),
                ("cached_tokens", models.IntegerField(blank=True, null=True)),
                ("cache_write_tokens", models.IntegerField(blank=True, null=True)),
                ("reasoning_tokens", models.IntegerField(blank=True, null=True)),
                ("upstream_cost_micros", models.BigIntegerField(blank=True, null=True)),
                ("billed_cost_micros", models.BigIntegerField(blank=True, null=True)),
                ("landed_cost_micros", models.BigIntegerField(blank=True, null=True)),
                ("upstream_fee_bps", models.IntegerField(blank=True, null=True)),
                ("processing_fee_bps", models.IntegerField(blank=True, null=True)),
                ("cost_source", models.CharField(blank=True, default="", max_length=32)),
                ("usage_estimated", models.BooleanField(default=False)),
                ("billable", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name="agentcc_request_attempts", to="accounts.organization")),
                ("request_log", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="attempts", to="prism.agentccrequestlog")),
            ],
            options={
                "db_table": "agentcc_request_attempt",
                "indexes": [
                    models.Index(fields=["organization", "request_id"], name="agentcc_att_org_request_idx"),
                    models.Index(fields=["organization", "created_at"], name="agentcc_att_org_created_idx"),
                ],
            },
        ),
    ]
