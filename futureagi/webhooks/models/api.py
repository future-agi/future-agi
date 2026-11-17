"""The API's own ledger (WH-2; DESIGN.md sections 7.8, 8.2 and 8.3). Migration ``0002_api``.

One row per management write, test, redelivery and bulk redelivery. It answers two questions the delivery tables do
not: who asked for an attempt (a redelivery's attempt row is written by the worker, which does not know the person),
and how many writes, tests and bulk redeliveries a person, an org or a workspace made recently (the rate limits).
It is not the audit trail: that is ``AuditLog`` with scope ``webhooks``.
"""

from __future__ import annotations

from django.db import models

from webhooks.models.config import Endpoint
from webhooks.models.outbox import Delivery


class ApiAction(models.Model):
    # Org-wide actions have no workspace; FA's thread-local auto-assignment must not invent one.
    disable_auto_workspace_assignment = True

    KIND = [
        ("write", "write"),
        ("test", "test"),
        ("redeliver", "redeliver"),
        ("bulk_redeliver", "bulk_redeliver"),
    ]

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace", null=True, on_delete=models.CASCADE, related_name="+"
    )
    actor = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    kind = models.CharField(max_length=20, choices=KIND)
    endpoint = models.ForeignKey(
        Endpoint, null=True, on_delete=models.CASCADE, related_name="+"
    )
    delivery = models.ForeignKey(
        Delivery, null=True, on_delete=models.CASCADE, related_name="+"
    )
    attempt_n = models.IntegerField(null=True)
    """For ``redeliver``: the attempt number the worker will write for this request."""
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "webhooks_api_action"
        indexes = [
            models.Index(
                fields=["organization", "kind", "created_at"], name="whapi_org_kind"
            ),
            models.Index(fields=["delivery", "attempt_n"], name="whapi_delivery_n"),
        ]
