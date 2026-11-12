"""Revocable service grants persist canonical claims, never bearer material."""

from django.db import models

from tfc.service_envelope import REGISTRY_FIELDS


class ServiceGrant(models.Model):
    jti = models.CharField(max_length=32, primary_key=True)
    kind = models.CharField(max_length=24)
    kid = models.CharField(max_length=4)
    aud = models.CharField(max_length=32)
    org = models.UUIDField(db_index=True)
    workspace = models.UUIDField(null=True)
    user = models.UUIDField(null=True, db_index=True)
    via = models.CharField(max_length=8)
    sub = models.CharField(max_length=160)
    scope = models.CharField(max_length=24)
    iat = models.BigIntegerField()
    exp = models.BigIntegerField(db_index=True)
    revoked_at = models.DateTimeField(null=True)
    revoked_reason = models.CharField(max_length=64, blank=True)
    agent_id = models.UUIDField(null=True, db_index=True)
    attempt_id = models.UUIDField(null=True, db_index=True)
    batch_ref = models.UUIDField(null=True, db_index=True)

    class Meta:
        db_table = "accounts_service_grant"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(exp__gt=models.F("iat")),
                name="service_grant_positive_life",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=["sandbox", "batch"]),
                name="service_grant_registered_kind",
            ),
        ]

    def registry_row(self):
        row = {
            f: str(getattr(self, f)) if getattr(self, f) is not None else "-"
            for f in REGISTRY_FIELDS
        }
        row.update(
            jti=self.jti,
            revoked_at=self.revoked_at,
            agent_id=self.agent_id,
            attempt_id=self.attempt_id,
            batch_ref=self.batch_ref,
        )
        return row


class BatchSubmissionIntent(models.Model):
    """Recovery is committed before submit, including if the sender disappears."""

    grant = models.OneToOneField(
        ServiceGrant, on_delete=models.PROTECT, primary_key=True
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    resolved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_batch_submission_intent"


class BatchCancellationIntent(models.Model):
    """Transactional outbox: retried until readback confirms a terminal batch."""

    grant = models.OneToOneField(
        ServiceGrant, on_delete=models.PROTECT, primary_key=True
    )
    gateway_batch_id = models.CharField(max_length=64, blank=True)
    organization_id = models.UUIDField()
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    confirmed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_batch_cancellation_intent"
