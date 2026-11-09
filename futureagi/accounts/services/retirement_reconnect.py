"""Validate the complete retirement-reconnect-v1 migration journal interface."""

from uuid import UUID

from django.db.models import CharField, OuterRef, Subquery
from django.db.models.functions import Cast
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

from accounts.models.retirement_manifest import RetirementManifest

FLAGS = {"is_active", "reconnect_required"}
IDENTITIES = {"user_id", "organization_id", "api_key_id"}
APPLIED_FIELDS = FLAGS | IDENTITIES | {"updated_at", "deleted"}


def complete_reconnect_journal(prior, applied, confirmed):
    """Validate provenance, not equality to today's possibly customer-edited row."""
    if (
        confirmed is not True
        or not isinstance(prior, dict)
        or not isinstance(applied, dict)
    ):
        return False
    if set(prior) != FLAGS or any(type(prior[field]) is not bool for field in FLAGS):
        return False
    if set(applied) != APPLIED_FIELDS:
        return False
    if applied["is_active"] is not False or applied["reconnect_required"] is not True:
        return False
    if type(applied["deleted"]) is not bool or not isinstance(
        applied["updated_at"], str
    ):
        return False
    try:
        stamp = parse_datetime(applied["updated_at"])
        if stamp is None or not is_aware(stamp):
            return False
        for field in IDENTITIES:
            value = applied[field]
            if value is not None and (
                not isinstance(value, str) or str(UUID(value)) != value
            ):
                return False
    except (TypeError, ValueError):
        return False
    return True


def unjournaled_connections():
    from mcp_server.models import MCPConnection

    # Producer: mcp_server.0005 (L1-CORE). Depend on accounts.0034; use historical
    # models and schema_editor.connection.alias. Write the two ORIGINAL boolean
    # flags and the complete POST-change fingerprint in the same transaction as
    # the mutation; confirmed=True, applied_at set, reverted_at=NULL,
    # superseded_by=''. Preserve the first prior state on retries. Never infer it.
    # A superseded journal still proves provenance: reverse leaves that object
    # untouched while processing others. Validate its contents just as strictly;
    # supersession must not disguise a missing or incomplete prior state.
    # Correlated subqueries keep memory bounded and avoid one query per object.
    recorded = RetirementManifest.objects.filter(
        service="mcp",
        action="reconnect",
        object_id=OuterRef("journal_id"),
        reverted_at__isnull=True,
    )
    connections = (
        MCPConnection._base_manager.filter(
            deleted=False,
            api_key__retired_reason="733",
            reconnect_required=True,
            is_active=False,
        )
        .annotate(journal_id=Cast("pk", output_field=CharField()))
        .annotate(
            prior=Subquery(recorded.values("prior_state")[:1]),
            applied=Subquery(recorded.values("applied_state")[:1]),
            confirmed=Subquery(recorded.values("confirmed")[:1]),
        )
        .values_list("prior", "applied", "confirmed")
    )
    return sum(not complete_reconnect_journal(*row) for row in connections.iterator())
