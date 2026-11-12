"""The agent key budget an Owner or Admin sets for one workspace (CN22c).

Every agent key the provisioning API mints for Omega EE in a workspace
spends at most this much per period (the gateway's keyscope budget). With no
row the platform default applies (``services/ee_agent_key_policy.py``). The
budget is read when the gateway loads the key, so a change reaches every
live key at the gateway's next key sync, with no new key.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace


class AgentccEEWorkspacePolicy(models.Model):
    class Period(models.TextChoices):
        DAILY = "daily"
        WEEKLY = "weekly"
        MONTHLY = "monthly"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.OneToOneField(
        Workspace, on_delete=models.CASCADE, related_name="agentcc_ee_policy"
    )
    agent_key_budget_usd = models.DecimalField(max_digits=12, decimal_places=2)
    budget_period = models.CharField(
        max_length=8, choices=Period.choices, default=Period.MONTHLY
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agentcc_ee_workspace_policy"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(agent_key_budget_usd__gte=0),
                name="agentcc_ee_policy_budget_not_negative",
            ),
        ]

    def __str__(self):
        return (
            f"EE agent key budget {self.agent_key_budget_usd} "
            f"{self.budget_period} for workspace {self.workspace_id}"
        )
