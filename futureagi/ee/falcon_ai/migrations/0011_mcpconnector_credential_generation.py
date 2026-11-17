from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('falcon_ai', '0010_encrypt_connector_credentials'),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpconnector",
            name="credential_generation",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
    ]
