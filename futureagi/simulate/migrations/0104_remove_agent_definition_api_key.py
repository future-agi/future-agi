from django.db import migrations
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied


class Migration(migrations.Migration):
    dependencies = [
        ('simulate', '0103_encrypt_agent_definition_api_key'),
    ]
    # c2.5 recorded this as simulate.0102_remove_agent_definition_api_key.
    operations = [SkipDatabaseIfLegacyApplied(legacy=('simulate', '0102_remove_agent_definition_api_key'), operations=[
        migrations.RemoveField(model_name="agentdefinition", name="api_key"),
    ])]
