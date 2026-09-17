from django.db import migrations


class Migration(migrations.Migration):
    """Move DeploymentTelemetry* out of the usage app in state only.

    The models moved to ee.cloud.telemetry.models with
    app_label="cloud_telemetry" and managed=False, pointing at the same
    physical tables (db_table="usage_deploymenttelemetry*"). Only the
    Django state ownership changes here; the underlying tables must
    survive this migration untouched.
    """

    dependencies = [
        ('usage', '0027_retire_legacy_license_models'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='DeploymentTelemetryHeartbeat'),
                migrations.DeleteModel(name='DeploymentTelemetryInstance'),
            ],
        ),
    ]
