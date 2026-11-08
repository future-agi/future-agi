"""Executable proposed model contract for the integration-owned migration.

These classes are test-only until I lands the two canonical models. Services use
apps.get_model, so the same tests automatically exercise the real models once
integrated. They never install a runtime fallback or create production tables.
"""

from django.apps import apps
from django.conf import settings
from django.db import models

from model_hub.models.skills_hub_v12 import ContextRecord


def register_contract_models():
    """Register only during the module fixture, never at test collection."""
    try:
        return (
            apps.get_model("model_hub", "HubSkillContextCampaign"),
            apps.get_model("model_hub", "HubSkillContextCampaignTarget"),
        ), False
    except LookupError:
        pass

    class HubSkillContextCampaign(ContextRecord):
        name = models.CharField(max_length=120)
        revision = models.PositiveBigIntegerField(default=1)
        target_ids = models.JSONField(default=list)
        status = models.CharField(max_length=16, default="draft")
        manifest_digest = models.CharField(max_length=71, null=True)
        author = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            on_delete=models.PROTECT,
            related_name="hub_skill_campaigns_authored",
        )
        authorizer = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            on_delete=models.SET_NULL,
            null=True,
            related_name="hub_skill_campaigns_authorized",
        )
        authorized_at = models.DateTimeField(null=True)
        parent_campaign = models.ForeignKey(
            "self",
            on_delete=models.PROTECT,
            null=True,
            related_name="hub_skill_reverse_campaigns",
        )
        reason = models.CharField(max_length=500, blank=True)

        class Meta:
            app_label = "model_hub"
            indexes = [
                models.Index(
                    fields=["organization", "workspace", "-created_at"],
                    name="hub_ctx_campaign_scope",
                )
            ]

    class HubSkillContextCampaignTarget(ContextRecord):
        campaign = models.ForeignKey(
            HubSkillContextCampaign,
            on_delete=models.CASCADE,
            related_name="hub_skill_targets",
        )
        binding = models.ForeignKey(
            "model_hub.HubSkillContextBinding",
            on_delete=models.PROTECT,
            related_name="hub_skill_campaign_targets",
        )
        binding_revision = models.PositiveBigIntegerField()
        desired_version = models.ForeignKey(
            "model_hub.HubSkillVersion",
            on_delete=models.PROTECT,
            related_name="hub_skill_campaign_targets",
        )
        plan = models.JSONField(null=True)
        plan_digest = models.CharField(max_length=71, null=True)
        decision = models.CharField(max_length=16, default="pending")
        reason = models.CharField(max_length=500, blank=True)
        attempts = models.JSONField(default=list)

        class Meta:
            app_label = "model_hub"
            constraints = [
                models.UniqueConstraint(
                    fields=["campaign", "binding"], name="hub_ctx_campaign_binding"
                )
            ]

    return (HubSkillContextCampaign, HubSkillContextCampaignTarget), True
