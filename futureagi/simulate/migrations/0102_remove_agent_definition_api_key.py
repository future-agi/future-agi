from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("simulate", "0101_encrypt_agent_definition_api_key")]
    operations = [
        migrations.RemoveField(model_name="agentdefinition", name="api_key"),
    ]
