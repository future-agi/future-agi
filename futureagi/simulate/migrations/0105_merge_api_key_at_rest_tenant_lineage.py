"""Join independent branches without changing the accepted M1 -> M2 -> M3 chain.

All old credential writers must remain fenced until the encrypted-storage chain
and this merge have completed. This graph join does not establish that fence.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('simulate', '0104_remove_agent_definition_api_key'),
        ('simulate', '0102_prompt_optimizer_tenant_lineage'),
    ]
    operations = []
