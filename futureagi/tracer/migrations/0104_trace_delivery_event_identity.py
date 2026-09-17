from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("tracer", "0103_trace_investigation_usage_receipt")]

    operations = [
        # Broker offsets can be reused after a topic is recreated. The existing
        # organization/event_id constraint remains the durable deduplication key.
        # Reversal requires resolving reused offsets first; do not delete receipts.
        migrations.RemoveConstraint(
            model_name="traceinvestigationdelivery",
            name="unique_trace_investigation_delivery",
        ),
    ]
