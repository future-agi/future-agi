import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def saml_rows(db, organization):
    other_organization = Organization.objects.create(name="Other SAML Organization")
    return (
        SAMLMetadataModel.objects.create(
            organization=organization,
            identity_type=SAMLMetadataModel.IDENTITY_AWS,
            relay_state="test-org-a-relay",
            meta="<metadata>org-a</metadata>",
        ),
        SAMLMetadataModel.objects.create(
            organization=other_organization,
            identity_type=SAMLMetadataModel.IDENTITY_AWS,
            relay_state="test-org-b-relay",
            meta="<metadata>org-b</metadata>",
        ),
    )


def _update_payload():
    return {
        "identity_type": str(SAMLMetadataModel.IDENTITY_AWS),
        "is_enabled": "true",
        "name": "Updated test IdP",
    }


@pytest.mark.django_db
def test_idp_uploads_are_scoped_to_the_resolved_organization(
    auth_client, saml_rows
):
    _, other_row = saml_rows

    responses = [
        auth_client.get(f"/saml2_auth/idp-uploads/{other_row.id}/"),
        auth_client.put(
            f"/saml2_auth/idp-uploads/{other_row.id}/",
            _update_payload(),
            format="multipart",
        ),
        auth_client.delete(f"/saml2_auth/idp-uploads/{other_row.id}/"),
    ]

    assert [response.status_code for response in responses] == [400, 400, 400]
    assert responses[1].content == responses[0].content
    assert responses[2].content == responses[0].content
    other_row.refresh_from_db()
    assert other_row.deleted is False


@pytest.mark.django_db
def test_idp_upload_members_cannot_modify_their_own_row(auth_client, user, saml_rows):
    own_row, _ = saml_rows
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user,
        organization=own_row.organization,
    )
    membership.role = OrganizationRoles.MEMBER
    membership.level = Level.MEMBER
    membership.save(update_fields=["role", "level"])

    response = auth_client.put(
        f"/saml2_auth/idp-uploads/{own_row.id}/",
        _update_payload(),
        format="multipart",
    )

    assert response.status_code == 403
