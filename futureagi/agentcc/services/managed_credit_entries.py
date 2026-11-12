"""Organization-scoped ledger reads shared by the wallet HTTP API and EE.

Only persisted ledger entries are read; Redis and the gateway settlement outbox
are not sources here. EE callers supply an already-authorized organization and
read after settlement commits. This is not a new HTTP authentication path.
"""

from types import ModuleType

from django.db.models import Q, QuerySet

from accounts.models.organization import Organization
from agentcc.serializers.managed_credits import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ManagedCreditEntriesPageSerializer,
    decode_cursor,
    encode_cursor,
)


def credit_entries(ledger: ModuleType, org: Organization, query: dict) -> QuerySet:
    """The org's entries matching a validated query, newest first after its cursor."""
    entries = ledger.ledger_entries(org, start=query.get("from"), end=query.get("to"))
    if query.get("kind"):
        entries = entries.filter(kind__in=query["kind"])
    if query.get("cursor"):
        at, entry_id = query["cursor"]
        entries = entries.filter(
            Q(occurred_at__lt=at) | Q(occurred_at=at, id__lt=entry_id)
        )
    return entries.order_by("-occurred_at", "-id")


def credit_entries_page(entries: QuerySet, limit: int) -> dict:
    """Serialize one JSON page, given a positive, bounded page size."""
    page = list(entries[: limit + 1])
    last = page[limit - 1] if len(page) > limit else None
    return ManagedCreditEntriesPageSerializer(
        {
            "entries": page[:limit],
            "next_cursor": encode_cursor(last.occurred_at, last.id) if last else None,
        }
    ).data


def settled_spend_entries(
    organization: Organization, *, cursor: str | None = None, limit: int = DEFAULT_PAGE_SIZE
) -> dict:
    """Read settled spend using the HTTP route's fields, ordering and cursors.

    ``limit`` is an integer clamped to 1..200 (default 50). Malformed cursors,
    including an empty string, raise ValueError before opening the ledger.
    Missing ledger support raises the same BillingUnavailable as the route.
    The supplied organization is mandatory; this function does not authorize a
    user or inspect pending gateway state. Call it after settlement commits.
    """
    if organization is None or organization.pk is None:
        raise ValueError("organization is required")
    query = {"kind": ["spend"]}
    if cursor is not None:
        query["cursor"] = decode_cursor(cursor)
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    # Reuse the existing optional-EE boundary. Import lazily because the HTTP
    # module itself uses the query/page helpers above; importing is not a read
    # of the gateway's hot state and does not resolve EE until _ledger() runs.
    from agentcc.views.managed_credits import _ledger

    entries = credit_entries(_ledger(), organization, query)
    return credit_entries_page(entries, limit)
