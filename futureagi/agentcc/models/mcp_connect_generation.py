"""Durable Disconnect fences, independent of consumed OAuth state rows."""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization


class AgentccMCPConnectGeneration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    server_id = models.CharField(max_length=64)
    generation = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "agentcc_mcp_connect_generation"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user", "server_id"],
                name="agentcc_mcp_connect_generation_identity",
            )
        ]


class AgentccMCPSharedConnectGeneration(models.Model):
    """An org/server fence, independent of the admin who started or disconnected.

    Keep personal identities and their uniqueness constraint unchanged. Shared
    generations outlive credential cleanup, state deletion and admin membership.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    server_id = models.CharField(max_length=64)
    generation = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "agentcc_mcp_shared_connect_generation"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "server_id"],
                name="agentcc_mcp_shared_connect_generation_identity",
            )
        ]
