"""The idempotency record of an EE agent-key mint (CN22d, AM-C27).

EE sends every mint with an Idempotency-Key, a UUIDv7 it stored before the
call, and sends the same one when it retries a mint whose answer it lost.
The mint writes this record in the same transaction as the key, under the
org lock, so a retry within 24 hours is answered with the key it made (never
its secret) instead of making a second key nobody holds.

The record holds hashes only: the SHA-256 of the Idempotency-Key and of the
mint's request body. It never holds the agent key, its secret or the request.
It is keyed by (org, EE service credential, key hash); the workspace is part
of the body, so the same Idempotency-Key with another workspace is a reuse
(422), not a second key. Records past ``expires_at`` are ignored, and the
org's next mint deletes them.
"""

import uuid

from django.db import models
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.models.mcp_service_provisioning import (
    AgentccEEAgentKey,
    AgentccEEServiceCredential,
)


class AgentccEEAgentKeyMintRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    service_credential = models.ForeignKey(
        AgentccEEServiceCredential, on_delete=models.CASCADE, related_name="+"
    )
    key_sha256 = models.CharField(max_length=64)
    request_sha256 = models.CharField(max_length=64)
    agent_key = models.ForeignKey(
        AgentccEEAgentKey, on_delete=models.CASCADE, related_name="mint_records"
    )
    replays = models.PositiveIntegerField(default=0)
    last_replayed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "agentcc_ee_agent_key_mint_record"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "service_credential", "key_sha256"],
                name="agentcc_ee_one_mint_per_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "expires_at"],
                name="agentcc_ee_mint_record_expiry",
            ),
        ]

    def __str__(self):
        return f"EE agent key mint record {self.key_sha256[:12]} ({self.agent_key_id})"
