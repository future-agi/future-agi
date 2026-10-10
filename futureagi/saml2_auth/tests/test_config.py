from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from accounts.models.organization_membership import OrganizationMembership
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level

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


def _upload_idp(client, metadata: str, name: str):
    return client.post(
        "/saml2_auth/idp-uploads/",
        {
            "file": SimpleUploadedFile(
                "idp.xml", metadata.encode(), content_type="application/xml"
            ),
            "name": name,
            "identity_type": str(SAMLMetadataModel.IDENTITY_OKTA),
            "is_enabled": "true",
        },
        format="multipart",
    )


def test_each_organization_can_upload_its_own_idp(saml_tenants, idp_metadata):
    """Upload ignores a posted relay_state, so the server must assign a unique one.

    The column is unique; storing the empty default would let only the first
    organization ever configure an IdP.
    """

    OrganizationMembership.no_workspace_objects.filter(
        user=saml_tenants.b_only, organization=saml_tenants.org_b
    ).update(level=Level.ADMIN)

    first = _upload_idp(
        _as_org_owner(saml_tenants.a1), idp_metadata["okta"], "tenant-a-idp"
    )
    second = _upload_idp(
        _as_org_owner(saml_tenants.b_only), idp_metadata["google"], "tenant-b-idp"
    )

    assert first.status_code == 200, first.content
    assert second.status_code == 200, second.content
    relay_states = list(
        SAMLMetadataModel.no_workspace_objects.filter(
            organization__in=[saml_tenants.org_a, saml_tenants.org_b]
        ).values_list("relay_state", flat=True)
    )
    assert len(relay_states) == 2
    assert all(relay_states)
    assert len(set(relay_states)) == 2
