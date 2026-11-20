"""Database-authoritative validation for organization-scoped SAML credentials."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeAlias

from rest_framework.exceptions import AuthenticationFailed

from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthTokenType,
)
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from saml2_auth.models import SAMLMetadataModel

TokenRow: TypeAlias = dict[str, Any]


class SamlCredentialInvalid(AuthenticationFailed):
    """A deliberately non-sensitive SAML credential denial."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SamlCredential:
    token_id: Any
    user_id: Any
    org_id: Any
    idp_id: Any
    generation: int
    validated_at: datetime


def validate_saml_credential(
    token_row: TokenRow | None,
    decoded_user_id: str,
    *,
    now: datetime,
    transport: str,
) -> SamlCredential:
    """Validate each live SAML boundary from the database, never from cache."""

    del transport  # Transport is retained for the caller's safe security event.
    if token_row is None:
        raise SamlCredentialInvalid("token_missing")
    if token_row["auth_type"] != AuthTokenType.ACCESS.value:
        raise SamlCredentialInvalid("token_type")
    if not token_row["is_active"]:
        raise SamlCredentialInvalid("token_inactive")
    last_used_at = token_row["last_used_at"]
    if last_used_at is None or last_used_at < now - timedelta(
        minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES
    ):
        raise SamlCredentialInvalid("token_expired_inactivity")
    if str(token_row["user_id"]) != str(decoded_user_id):
        raise SamlCredentialInvalid("user_binding")
    org_id = token_row["scoped_organization_id"]
    idp_id = token_row["origin_idp_id"]
    generation = token_row["origin_idp_generation"]
    if org_id is None or idp_id is None or generation is None:
        raise SamlCredentialInvalid("scope_incomplete")
    if not User.objects.filter(id=token_row["user_id"], is_active=True).exists():
        raise SamlCredentialInvalid("user_inactive")
    if not OrganizationMembership.no_workspace_objects.filter(
        user_id=token_row["user_id"], organization_id=org_id, is_active=True
    ).exists():
        raise SamlCredentialInvalid("membership_missing")
    current_generation = (
        SAMLMetadataModel.no_workspace_objects.filter(
            id=idp_id,
            is_enabled=True,
            organization_id=org_id,
        )
        .values_list("security_generation", flat=True)
        .first()
    )
    if current_generation is None:
        raise SamlCredentialInvalid("idp_unavailable")
    if current_generation != generation:
        raise SamlCredentialInvalid("idp_generation")
    return SamlCredential(
        token_id=token_row["id"],
        user_id=token_row["user_id"],
        org_id=org_id,
        idp_id=idp_id,
        generation=generation,
        validated_at=now,
    )
