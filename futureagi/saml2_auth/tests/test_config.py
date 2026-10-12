from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient, force_authenticate

from accounts.models.organization_membership import OrganizationMembership
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = [pytest.mark.django_db, pytest.mark.api]


def _bind_org(user, organization):
    """Match API-key auth on the request the handler actually dispatches.

    force_authenticate skips request.organization and the workspace ContextVar.
    WorkspaceContextMiddleware clears that ContextVar in process_request, which
    runs inside super().get_response, so the bind has to happen there too.
    """

    class _Handler(type(APIClient().handler)):
        def get_response(self, request):
            force_authenticate(request, user, None)
            request.organization = organization
            set_workspace_context(organization=organization, user=user, workspace=None)
            try:
                return super().get_response(request)
            finally:
                clear_workspace_context()

    client = APIClient()
    client.handler = _Handler(enforce_csrf_checks=client.handler.enforce_csrf_checks)
    client.force_authenticate(user=user)
    return client


def _as_org_owner(user, organization=None):
    if organization is None:
        organization = user.organization
    return _bind_org(user, organization)


def test_config_retrieve_foreign_is_404_envelope(saml_tenants, idp_b):
    client = _as_org_owner(saml_tenants.a1)

    response = client.get(f"/saml2_auth/idp-uploads/{idp_b.id}/")

    assert response.status_code == 404
    body = response.json()
    assert "is_enabled" not in str(body)
    assert "identity_type" not in str(body)


def test_config_list_excludes_foreign_idp(saml_tenants, idp_a, idp_b):
    """Any authenticated member must not see another org's IdP row."""

    client = _bind_org(saml_tenants.a2, saml_tenants.org_a)
    response = client.get("/saml2_auth/idp-uploads/")

    assert response.status_code == 200
    results = response.json()["result"]["results"]
    ids = {item["id"] for item in results}
    assert str(idp_a.id) in ids
    assert str(idp_b.id) not in ids
    assert str(idp_b.id) not in response.content.decode()


def test_config_delete_foreign_is_404_and_row_stays(saml_tenants, idp_b):
    """A foreign id must not soft-delete another org's IdP."""

    client = _bind_org(saml_tenants.a1, saml_tenants.org_a)
    response = client.delete(f"/saml2_auth/idp-uploads/{idp_b.id}/")
    idp_b.refresh_from_db()

    assert response.status_code == 404
    assert idp_b.deleted is False
    assert idp_b.organization_id == saml_tenants.org_b.id


def test_config_update_foreign_is_404_and_row_stays(saml_tenants, idp_b):
    """A foreign id must not rewrite another org's IdP or move it."""

    original_name = idp_b.name
    original_generation = idp_b.security_generation
    client = _bind_org(saml_tenants.a1, saml_tenants.org_a)
    response = client.put(
        f"/saml2_auth/idp-uploads/{idp_b.id}/",
        {
            "name": "attacker-supplied",
            "identity_type": str(idp_b.identity_type),
            "is_enabled": "true",
            "organization": str(saml_tenants.org_a.id),
        },
        format="multipart",
    )
    idp_b.refresh_from_db()

    assert response.status_code == 404
    assert idp_b.organization_id == saml_tenants.org_b.id
    assert idp_b.name == original_name
    assert idp_b.security_generation == original_generation
    assert idp_b.deleted is False


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


def test_organization_can_upload_an_idp_after_deleting_one(
    saml_tenants, idp_a, idp_metadata
):
    """The deleted IdP still holds the organization's one-to-one row."""

    client = _as_org_owner(saml_tenants.a1)
    assert client.delete(f"/saml2_auth/idp-uploads/{idp_a.id}/").status_code == 200
    idp_a.refresh_from_db(from_queryset=SAMLMetadataModel.all_objects)
    deleted_generation = idp_a.security_generation

    response = _upload_idp(client, idp_metadata["google"], "tenant-a-new-idp")

    assert response.status_code == 200, response.content
    live = SAMLMetadataModel.objects.get(organization=saml_tenants.org_a)
    assert live.meta == idp_metadata["google"]
    assert live.security_generation > deleted_generation


def test_upload_reports_the_invalid_field(saml_tenants, idp_metadata):
    client = _as_org_owner(saml_tenants.a1)

    response = _upload_idp(client, idp_metadata["okta"], "name with spaces")

    assert response.status_code == 400
    assert "name:" in response.content.decode()


def test_deleted_idp_cannot_be_reactivated_without_fresh_metadata(saml_tenants, idp_a):
    client = _as_org_owner(saml_tenants.a1)
    assert client.delete(f"/saml2_auth/idp-uploads/{idp_a.id}/").status_code == 200

    response = client.post(
        "/saml2_auth/idp-uploads/",
        {
            "name": "reactivate-without-metadata",
            "identity_type": str(idp_a.identity_type),
            "is_enabled": "true",
        },
        format="multipart",
    )

    assert response.status_code == 400
    idp_a.refresh_from_db(from_queryset=SAMLMetadataModel.all_objects)
    assert idp_a.deleted is True
    assert idp_a.is_enabled is True
