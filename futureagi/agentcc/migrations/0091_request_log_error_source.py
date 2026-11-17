from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0090_request_log_error_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentccrequestlog",
            name="error_source",
            field=models.CharField(blank=True, max_length=16, null=True),
        ),
    ]
