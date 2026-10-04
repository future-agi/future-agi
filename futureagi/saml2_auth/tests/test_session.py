from __future__ import annotations

import pytest
from rest_framework.test import APIClient

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
