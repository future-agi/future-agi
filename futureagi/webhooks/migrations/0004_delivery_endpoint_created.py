# WH-3 F6: an (endpoint_id, created_at) index on webhooks_delivery for the destination registry's 24 h counts
# (integrations/services/destinations.py) and the delivery log's time window. The table ships with this feature (no
# install has rows in it yet), so a plain AddIndex is used.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("webhooks", "0003_merge"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="delivery",
            index=models.Index(
                fields=["endpoint", "created_at"], name="dlv_endpoint_created"
            ),
        ),
    ]
