from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY can't run inside a transaction, and keeps a
    # tool-call table that is already taking writes open to them.
    atomic = False

    dependencies = [
        ("prism", "0040_mcp_oauth"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="agentcctoolcall",
            index=models.Index(
                fields=["organization", "-started_at"],
                name="agentcc_tc_org_started_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentcctoolcall",
            index=models.Index(
                fields=["organization", "agent_run_id"],
                name="agentcc_tc_org_run_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentcctoolcall",
            index=models.Index(
                fields=["organization", "server_id", "tool", "-started_at"],
                name="agentcc_tc_org_server_tool_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentcctoolcall",
            index=models.Index(
                fields=["organization", "api_key_id", "-started_at"],
                name="agentcc_tc_org_key_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="agentcctoolcall",
            index=models.Index(
                fields=["organization", "generation_id"],
                name="agentcc_tc_org_generation_idx",
            ),
        ),
    ]
