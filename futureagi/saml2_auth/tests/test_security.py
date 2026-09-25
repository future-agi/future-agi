from types import SimpleNamespace

import pytest

from accounts.models.auth_token import AuthToken
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.django_db, pytest.mark.api]


def _saml_record(organization, relay_state, *, enabled=True):
    return SAMLMetadataModel.no_workspace_objects.create(
        organization=organization,
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
        relay_state=relay_state,
        is_enabled=enabled,
        meta="",
    )


def test_idp_admin_endpoints_are_scoped_to_request_organization(
    auth_client, organization
):
    own_record = _saml_record(organization, "own-relay")
    foreign_org = Organization.objects.create(name="Foreign Organization")
    foreign_record = _saml_record(foreign_org, "foreign-relay")

    response = auth_client.get("/saml2_auth/idp-uploads/")

    assert response.status_code == 200
    results = response.json()["result"]["results"]
    assert [item["id"] for item in results] == [str(own_record.id)]

    response = auth_client.get(f"/saml2_auth/idp-uploads/{foreign_record.id}/")
    assert response.status_code == 404

    response = auth_client.delete(f"/saml2_auth/idp-uploads/{foreign_record.id}/")
    assert response.status_code == 404
    foreign_record.refresh_from_db()
    assert foreign_record.deleted is False


def test_idp_admin_endpoints_require_org_admin(auth_client, user, organization):
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=organization
    )
    membership.level = Level.MEMBER
    membership.role = OrganizationRoles.MEMBER
    membership.save(update_fields=["level", "role", "updated_at"])

    response = auth_client.get("/saml2_auth/idp-uploads/")

    assert response.status_code == 403


def test_acs_rejects_user_without_membership_in_saml_organization(
    api_client, organization, monkeypatch
):
    saml_record = _saml_record(organization, "trusted-relay")
    foreign_org = Organization.objects.create(name="Victim Organization")
    victim = User.objects.create_user(
        email="victim@example.com",
        password="irrelevant",
        name="Victim",
        organization=foreign_org,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=victim,
        organization=foreign_org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )

    class FakeAuthnResponse:
        def get_identity(self):
            return {
                "email": [victim.email],
                "first_name": ["Victim"],
                "last_name": ["User"],
            }

        def get_subject(self):
            return SimpleNamespace(text=victim.email)

    class FakeSAMLClient:
        def parse_authn_request_response(self, *_args, **_kwargs):
            return FakeAuthnResponse()

    monkeypatch.setattr(
        "saml2_auth.views._get_saml_client",
        lambda record, _acs_url: (FakeSAMLClient(), record.identity_type),
    )

    response = api_client.post(
        "/saml2_auth/acs/",
        {"SAMLResponse": "signed-response", "RelayState": saml_record.relay_state},
    )

    assert response.status_code == 302
    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=victim).exists()
    victim.refresh_from_db()
    assert victim.organization == foreign_org


def test_acs_rejects_disabled_identity_provider(api_client, organization, monkeypatch):
    saml_record = _saml_record(organization, "disabled-relay", enabled=False)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("disabled IdP metadata must not be loaded")

    monkeypatch.setattr("saml2_auth.views._get_saml_client", fail_if_called)

    response = api_client.post(
        "/saml2_auth/acs/",
        {"SAMLResponse": "signed-response", "RelayState": saml_record.relay_state},
    )

    assert response.status_code == 302
    assert "sso_token" not in response["Location"]
