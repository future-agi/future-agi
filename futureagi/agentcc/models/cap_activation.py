"""Policy lifetime, separate from editable/deletable config versions."""

from django.db import models
from django.utils import timezone


class AgentccCapActivation(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    level = models.CharField(max_length=256)
    activated_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "level"],
                name="agentcc_cap_activation_org_level",
            )
        ]


class AgentccCapPolicyRevision(models.Model):
    """Org-wide activation order, independent of saved/deletable versions."""

    organization = models.OneToOneField(
        "accounts.Organization", on_delete=models.CASCADE
    )
    revision = models.PositiveBigIntegerField(default=0)
