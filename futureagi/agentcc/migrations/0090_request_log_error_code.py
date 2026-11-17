from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0089_normalize_two_key_org_period"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentccrequestlog",
            name="error_code",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
    ]
