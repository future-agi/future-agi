"""Gateway-owned reload generation for the organization's managed account.

The public ledger API has one managed account per organization. This sidecar
avoids editing the main-owned money model. It stores no balance, payment or
consent authority. All writers take the ledger account row lock first.
"""

from django.db import models

from accounts.models import Organization


class AgentccAutoReloadArming(models.Model):
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="agentcc_auto_reload_arming",
    )
    epoch = models.PositiveBigIntegerField(default=0)
    below_threshold = models.BooleanField(default=False)
    # Event latch only: authorization always uses the current consent receipt.
    intent_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "agentcc_auto_reload_arming"
