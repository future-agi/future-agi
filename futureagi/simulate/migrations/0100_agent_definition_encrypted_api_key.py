from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0099_harness_versioning")]
    operations = [
        migrations.AddField(
            model_name="agentdefinition",
            name="encrypted_api_key",
            field=models.TextField(null=True, blank=True, editable=False),
        ),
    ]
