import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.org_config_defaults import default_cost_tracking_config
from tfc.utils.base_model import BaseModel


class AgentccOrgConfig(BaseModel):
    """
    Per-organization gateway configuration. Each org gets its own provider API
    keys, guardrail pipeline, and routing strategy. Configs are versioned —
    only one version is active at a time per org.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_org_configs",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_org_configs",
        null=True,
        blank=True,
    )
    version = models.PositiveIntegerField(default=1)

    # Per-org guardrail pipeline (which checks, thresholds, actions)
    guardrails = models.JSONField(default=dict, blank=True)

    # Per-org routing strategy (strategy, fallbacks, conditional routes)
    routing = models.JSONField(default=dict, blank=True)

    # Per-org cache config (backend, semantic, edge, TTL)
    cache = models.JSONField(default=dict, blank=True)

    # Per-org rate limiting (RPM/TPM limits per org, key, model, user)
    rate_limiting = models.JSONField(default=dict, blank=True)

    # Per-org spend budgets (limits, periods, thresholds)
    budgets = models.JSONField(default=dict, blank=True)

    # Per-org cost tracking (custom pricing overrides)
    cost_tracking = models.JSONField(default=default_cost_tracking_config, blank=True)

    # Per-org IP access control (allow/deny lists)
    ip_acl = models.JSONField(default=dict, blank=True)

    # Per-org alerting (rules + notification channels)
    alerting = models.JSONField(default=dict, blank=True)

    # Per-org PII privacy/redaction (mode + patterns)
    privacy = models.JSONField(default=dict, blank=True)

    # Per-org tool/function calling policy (allow/deny lists)
    tool_policy = models.JSONField(default=dict, blank=True)

    # Per-org MCP config (servers, guardrails, tool rate limits)
    mcp = models.JSONField(default=dict, blank=True)

    # Per-org A2A config (agent card, external agent registry, auth, skills)
    a2a = models.JSONField(default=dict, blank=True)

    # Per-org audit logging (sinks, severity, categories)
    audit = models.JSONField(default=dict, blank=True)

    # Per-org model database overrides (custom token limits, pricing, capabilities)
    model_database = models.JSONField(default=dict, blank=True)

    # Per-org model map (model name → provider ID aliasing)
    model_map = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agentcc_org_configs",
    )
    change_description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "agentcc_org_config"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "version"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_org_config_version",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(deleted=False, is_active=True),
                name="unique_agentcc_org_config_active",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "-version"]),
            models.Index(fields=["organization", "is_active"]),
        ]

    def __str__(self):
        return f"OrgConfig {self.organization_id} v{self.version} ({'active' if self.is_active else 'inactive'})"
