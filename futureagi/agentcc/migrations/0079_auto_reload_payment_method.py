"""Add an empty binding, never infer consent from a legacy saved method.

Existing receipts remain unbound and cannot authorize a payment. Only a fresh
explicit acknowledgement records the account's current payment method.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0078_historical_key_revocations")]

    operations = [
        migrations.AddField(
            model_name="agentccautoreloadconsent",
            name="payment_method_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
