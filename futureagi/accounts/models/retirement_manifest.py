"""A reversible state journal. Credentials and their digests never belong here."""

from django.db import models


class RetirementManifest(models.Model):
    service = models.CharField(max_length=24)
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=32)
    prior_state = models.JSONField(default=dict)
    applied_state = models.JSONField(default=dict)
    applied_at = models.DateTimeField()
    reverted_at = models.DateTimeField(null=True)
    superseded_by = models.CharField(max_length=64, blank=True)
    confirmed = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_retirement_manifest"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "object_id", "action"],
                name="retirement_object_action_unique",
            )
        ]
