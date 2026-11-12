from django.db import migrations, models
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied


class Migration(migrations.Migration):
    dependencies = [
        ('simulate', '0101_agent_fix_candidate_override'),
    ]

    # c2.5 recorded this as simulate.0100_prompt_optimizer_tenant_lineage.
    operations = [SkipDatabaseIfLegacyApplied(legacy=('simulate', '0100_prompt_optimizer_tenant_lineage'), operations=[
        migrations.AddField(
            model_name="agentpromptoptimiserrun",
            name="tenant_lineage",
            field=models.JSONField(null=True, editable=False),
        ),
    ])]
    # Deliberately no backfill: the historical FKs are not trustworthy provenance.
