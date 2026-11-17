"""The API's locks, in the one order every API write takes them (DESIGN.md sections 7.7 and 7.8).

1. **The organization's API lock** (:func:`lock_org`): a transaction-scoped Postgres advisory lock per organization.
   The limits and rates are counted rows (50 endpoints per workspace, 200 per org, 120 writes a minute, the test and
   bulk-redelivery rates): a count and the insert it allows must not interleave with another replica's, so every
   API write takes this lock before it counts.
2. **The endpoint's state row** (:func:`lock_state`), as ``store_settle.disable_endpoint`` and every settle do, so a
   claim waiting on it sees the change.
3. **The endpoint row** itself (:func:`locked_endpoint`), re-read under the lock: a write applies its change to the
   row as it is now, never to a copy read before the request's transaction.

The dispatcher, the offboarding check and a policy's re-check never take the organization lock, and nothing that holds
a state row waits for it, so the order has no cycle.
"""

from __future__ import annotations

from django.db import connection

from webhooks.api_errors import WebhookApiError
from webhooks.models import Endpoint, EndpointState

_ORG_LOCK_NAMESPACE = "webhooks.api.org:"


def lock_org(organization_id) -> None:
    """Hold the organization's API lock until the surrounding transaction ends."""
    if not connection.in_atomic_block:
        raise RuntimeError(
            "lock_org needs a transaction: the advisory lock is held until it ends"
        )
    with connection.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"{_ORG_LOCK_NAMESPACE}{organization_id}"],
        )


def lock_state(endpoint_id: str) -> None:
    """Lock the endpoint's state row (created when missing)."""
    EndpointState.objects.get_or_create(
        endpoint_id=endpoint_id,
        defaults={
            "organization_id": Endpoint.objects.values_list(
                "organization_id", flat=True
            ).get(id=endpoint_id)
        },
    )
    list(
        EndpointState.objects.select_for_update()
        .filter(endpoint_id=endpoint_id)
        .values_list("endpoint_id", flat=True)
    )


def locked_endpoint(endpoint_id: str, organization_id) -> Endpoint:
    """The endpoint as it is now, its state row and its own row locked; 404 when it was deleted meanwhile."""
    if not Endpoint.objects.filter(
        id=endpoint_id, organization_id=organization_id, deleted=False
    ).exists():
        raise WebhookApiError("WEBHOOK_NOT_FOUND")
    lock_state(endpoint_id)
    ep = (
        Endpoint.objects.select_for_update()
        .filter(id=endpoint_id, organization_id=organization_id, deleted=False)
        .first()
    )
    if ep is None:
        raise WebhookApiError("WEBHOOK_NOT_FOUND")
    return ep
