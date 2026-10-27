from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("usage", "0026_deployment_telemetry"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="EEInstanceHeartbeat"),
                migrations.DeleteModel(name="EELicenseInstance"),
                migrations.DeleteModel(name="EELicenseGrant"),
                migrations.DeleteModel(name="EELicense"),
            ],
        ),
    ]
