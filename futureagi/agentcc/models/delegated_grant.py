import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization


class AgentccDelegatedGrant(models.Model):
    """A user's grant letting one agent act for them within scopes and a cap.

    ``preapproved_rules`` may name tools and argument patterns, so neither
    they nor the scopes appear in ``str`` or ``repr``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_delegated_grants",
    )
    grantor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agentcc_delegated_grants",
    )
    agent_id = models.CharField(max_length=128)
    scopes = models.JSONField(default=list, blank=True)
    preapproved_rules = models.JSONField(default=list, blank=True)
    max_usd = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_delegated_grant"
        ordering = ["-created_at"]

    def __str__(self):
        state = "revoked" if self.revoked_at else "active"
        return f"grant {self.pk} agent={self.agent_id} ({state})"

    def __repr__(self):
        return f"<AgentccDelegatedGrant {self.pk} org={self.organization_id} {self}>"
