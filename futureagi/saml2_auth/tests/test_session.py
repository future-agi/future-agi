from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.webauthn_credential import WebAuthnCredential

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _bearer_client(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_saml_token_explicit_b_selector_denied(idp_a, make_saml_token, saml_tenants):
    """A token issued for A cannot choose B through request selectors."""

    _row, token = make_saml_token(saml_tenants.ab, saml_tenants.org_a, idp_a)
    response = _bearer_client(token).get(
        "/accounts/organizations/",
        HTTP_X_ORGANIZATION_ID=str(saml_tenants.org_b.id),
    )

    assert response.status_code == 403
    assert "saml_scope_conflict" in response.content.decode()


def test_saml_session_cannot_rename_passkey(idp_a, make_saml_token, saml_tenants):
    _row, token = make_saml_token(saml_tenants.a1, saml_tenants.org_a, idp_a)
    passkey = WebAuthnCredential.objects.create(
        user=saml_tenants.a1,
        name="Original",
        credential_id="credential-id",
        public_key="public-key",
        sign_count=0,
    )

    response = _bearer_client(token).patch(
        f"/accounts/passkeys/{passkey.id}/",
        {"name": "Renamed"},
        format="json",
    )

    assert response.status_code == 403
    assert "saml_scoped_session" in response.content.decode()
    passkey.refresh_from_db()
    assert passkey.name == "Original"


def test_saml_session_cannot_mint_user_api_key(idp_a, make_saml_token, saml_tenants):
    _row, token = make_saml_token(saml_tenants.a1, saml_tenants.org_a, idp_a)

    response = _bearer_client(token).post(
        "/accounts/key/generate_secret_key/",
        {"key_name": "saml-must-not-mint"},
        format="json",
    )

    assert response.status_code == 403
    assert "saml_scoped_session" in response.content.decode()


def test_revocation_after_issuance(idp_a, make_saml_token, saml_tenants):
    """Warm cache state must not revive an inactive SAML credential."""

    row, token = make_saml_token(saml_tenants.a1, saml_tenants.org_a, idp_a)
    client = _bearer_client(token)
    warm = client.get("/accounts/organizations/current/")
    assert warm.status_code == 200
    row.is_active = False
    row.save(update_fields=["is_active"])

    revoked = client.get("/accounts/organizations/current/")

    assert revoked.status_code == 401
    assert "token_inactive" in revoked.content.decode()


def test_revoke_unscoped_tokens_ends_cached_sessions(saml_tenants):
    """A legacy session already in the token cache stops working once revoked."""

    row = AuthToken.no_workspace_objects.create(
        user=saml_tenants.a1,
        auth_type=AuthTokenType.ACCESS.value,
        last_used_at=timezone.now(),
        is_active=True,
    )
    token = generate_encrypted_message(
        {"user_id": str(saml_tenants.a1.id), "id": str(row.id)}
    )
    client = _bearer_client(token)
    assert client.get("/accounts/organizations/current/").status_code == 200

    call_command(
        "revoke_unscoped_tokens",
        created_before=(timezone.now() + timedelta(seconds=1)).isoformat(),
    )

    assert client.get("/accounts/organizations/current/").status_code == 401
