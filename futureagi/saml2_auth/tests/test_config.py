from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = [pytest.mark.django_db, pytest.mark.api]


def _as_org_owner(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_config_retrieve_foreign_is_404_envelope(saml_tenants, idp_b):
    client = _as_org_owner(saml_tenants.a1)

    response = client.get(f"/saml2_auth/idp-uploads/{idp_b.id}/")

    assert response.status_code == 404
    body = response.json()
    assert "is_enabled" not in str(body)
    assert "identity_type" not in str(body)


def test_config_update_ignores_posted_organization(saml_tenants, idp_b):
    """A caller must not use a global lookup to move a foreign IdP into A."""

    client = _as_org_owner(saml_tenants.a1)
    response = client.put(
        f"/saml2_auth/idp-uploads/{idp_b.id}/",
        {
            "name": "attacker supplied value",
            "identity_type": str(idp_b.identity_type),
            "is_enabled": "true",
            "organization": str(saml_tenants.org_a.id),
        },
        format="multipart",
    )
    idp_b.refresh_from_db()

    assert response.status_code == 404
    assert idp_b.organization_id == saml_tenants.org_b.id
