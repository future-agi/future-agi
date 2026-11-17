"""Revocable service grants persist canonical claims, never bearer material."""

from django.db import models

from tfc.service_envelope import REGISTRY_FIELDS

REGISTERED_KINDS = ["sandbox", "batch", "console-run", "console-run-auth"]
# The kinds whose row names models: a console-run grant and its run
# authorization.
MODEL_KINDS = ["console-run", "console-run-auth"]


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
    # A console-run grant's models (fe1c field 20, comma-joined in byte order);
    # empty for every other kind.
    model_ids = models.CharField(max_length=1700, blank=True, default="")
    # A console-run run authorization's agent service deployment (fe1r field
    # 21); empty for every other kind.
    ee = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "accounts_service_grant"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(exp__gt=models.F("iat")),
                name="service_grant_positive_life",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=REGISTERED_KINDS),
                name="service_grant_registered_kind",
            ),
            models.CheckConstraint(
                condition=(models.Q(kind__in=MODEL_KINDS) & ~models.Q(model_ids=""))
                | (~models.Q(kind__in=MODEL_KINDS) & models.Q(model_ids="")),
                name="service_grant_console_run_models",
            ),
            models.CheckConstraint(
                condition=(models.Q(kind="console-run-auth") & ~models.Q(ee=""))
                | (~models.Q(kind="console-run-auth") & models.Q(ee="")),
                name="service_grant_run_authorization_ee",
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
        if self.model_ids:
            row["models"] = self.model_ids
        if self.ee:
            row["ee"] = self.ee
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
