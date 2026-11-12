from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY can't run inside a transaction, and avoids the
    # write-blocking lock a plain CREATE INDEX would take on the request log.
    atomic = False

    dependencies = [
        ("prism", "0030_request_log_v2"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="agentccrequestlog",
            index=models.Index(
                fields=["organization", "agent_run_id"],
                name="agentcc_rl_org_run_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentccrequestlog",
            index=models.Index(
                fields=["organization", "agent_id", "-started_at"],
                name="agentcc_rl_org_agent_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentccrequestlog",
            index=models.Index(
                fields=["organization", "billing_mode", "-started_at"],
                name="agentcc_rl_org_billing_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentccrequestlog",
            index=models.Index(
                fields=["organization", "request_id"],
                name="agentcc_rl_org_request_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentccrequestlog",
            index=models.Index(
                fields=["organization", "policy_variant", "-started_at"],
                name="agentcc_rl_org_variant_idx",
            ),
        ),
    ]
