from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0101_trace_investigation_reconciliation_cursor")]

    operations = [
        migrations.AddField(
            model_name="tracescanconfig",
            name="omega_last_claimed_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
