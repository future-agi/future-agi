"""Revocation: a client revokes its own refresh token (RFC 7009), and a person
revokes a consent they gave.

Revoking a refresh token revokes its whole grant family (every live refresh
token of that person, client, organization and resource), so nothing derived
from it can refresh again. Revoking a consent does the same for the consent's
family. Access tokens carry no revocation list (decision 37): they stop
within their 15-minute life, because the next refresh is refused. A token
that is unknown, already revoked, an access token, or another client's is
answered 200 and left as it is (RFC 7009 section 2.2), so the answer says
nothing about tokens the caller does not hold.
"""

from django.db import transaction
from django.utils import timezone

from agentcc.mcp_oauth import tokens
from agentcc.models.mcp_oauth import (
    AgentccMCPOAuthConsent,
    AgentccMCPOAuthRefreshToken,
)


def revoke_token(client, token) -> bool:
    """Returns whether anything was revoked; the caller answers 200 either
    way."""
    if not isinstance(token, str) or not token or len(token) > 512:
        return False
    row = AgentccMCPOAuthRefreshToken.objects.filter(
        token_hash=tokens.hash_token(token), client_id=client.row.pk
    ).first()
    if row is None:
        return False
    return (
        tokens.revoke_family(
            user_id=row.user_id,
            client_pk=row.client_id,
            org_id=row.organization_id,
            resource=row.resource,
        )
        > 0
    )


def consents_for(user, organization, *, org_wide=False) -> list[dict]:
    """The selected org only; the API checks active membership and admin scope."""
    rows = (
        AgentccMCPOAuthConsent.objects.filter(
            organization=organization, revoked_at__isnull=True
        )
        .select_related("client", "organization", "user")
        .order_by("-granted_at", "-pk")
    )
    if not org_wide:
        rows = rows.filter(user=user)
    out = []
    for row in rows:
        metadata = row.client.metadata if isinstance(row.client.metadata, dict) else {}
        out.append(
            {
                "id": row.pk,
                "client_id": row.client.client_id,
                "client_name": str(metadata.get("client_name") or row.client.client_id)[
                    :200
                ],
                "client_kind": row.client.kind,
                "organization": {
                    "id": str(row.organization_id),
                    "name": row.organization.name,
                },
                "resource": row.resource,
                "scopes": list(row.scopes or []),
                "granted_at": row.granted_at,
                "user": {"id": str(row.user_id), "name": row.user.name or "Member"},
                "can_revoke": row.user_id == user.pk,
            }
        )
    return out


@transaction.atomic
def revoke_consent(user, consent_id, organization) -> bool:
    row = (
        AgentccMCPOAuthConsent.objects.select_for_update()
        .filter(
            pk=consent_id, user=user, organization=organization, revoked_at__isnull=True
        )
        .first()
    )
    if row is None:
        return False
    row.revoked_at = timezone.now()
    row.save(update_fields=["revoked_at"])
    tokens.revoke_family(
        user_id=row.user_id,
        client_pk=row.client_id,
        org_id=row.organization_id,
        resource=row.resource,
    )
    return True
