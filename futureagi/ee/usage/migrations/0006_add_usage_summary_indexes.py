
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('usage', '0005_alter_apicalltype_name'),
    ]

    # Disable atomic transactions for CONCURRENT index creation
    atomic = False

    operations = [
        # Composite index for most common query pattern
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS 
                idx_apicalllog_org_workspace_created 
            ON usage_apicalllog (organization_id, workspace_id, created_at DESC)
            WHERE status IN ('success', 'processing') AND deducted_cost > 0;
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS idx_apicalllog_org_workspace_created;
            """
        ),
        
        # Index for workspace-specific queries
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS 
                idx_apicalllog_workspace_created_cost 
            ON usage_apicalllog (workspace_id, created_at DESC, deducted_cost)
            WHERE status IN ('success', 'processing');
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS idx_apicalllog_workspace_created_cost;
            """
        ),
        
        # Index for API call type categorization
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS 
                idx_apicalllog_type_org_created 
            ON usage_apicalllog (api_call_type_id, organization_id, created_at DESC);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS idx_apicalllog_type_org_created;
            """
        ),
    ]