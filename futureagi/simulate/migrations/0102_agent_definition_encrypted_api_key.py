from django.db import migrations, models
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied


class Migration(migrations.Migration):
    dependencies = [
        ('simulate', '0101_agent_fix_candidate_override'),
    ]
    # c2.5 recorded this as simulate.0100_agent_definition_encrypted_api_key.
    operations = [SkipDatabaseIfLegacyApplied(legacy=('simulate', '0100_agent_definition_encrypted_api_key'), operations=[
        migrations.AddField(
            model_name="agentdefinition",
            name="encrypted_api_key",
            field=models.TextField(null=True, blank=True, editable=False),
        ),
    ])]
