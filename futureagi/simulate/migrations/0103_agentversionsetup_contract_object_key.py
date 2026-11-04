from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simulate", "0102_agent_version_connect_triggers"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentversionsetup",
            name="contract_object_key",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
