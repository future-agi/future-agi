"""Regression pins for the SAML findings in contributor PR #2126.

Each test exercises a vulnerability the PR describes in the pre-rework SAML
code and shows that current dev refuses it. Most drive the endpoints the way
the pre-rework code accepted them (a configuration relay_state posted straight
to ACS, an org-name-prefixed IdP lookup, global idp-uploads lookups, on-disk
metadata), so restoring the old views makes them fail. The bound-flow tests
(``bound_attempt``) pin the same property on the current candidate-then-complete
protocol and have no pre-rework equivalent.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from accounts.models.auth_token import AuthToken
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from saml2_auth.models import SamlLoginAttempt, SAMLMetadataModel
from saml2_auth.tests.conftest import bound_attempt, complete, post_acs
from saml2_auth.tests.saml_fixtures import build_idp_metadata
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _idp_host(login_response):
    return urlparse(login_response.json()["result"]["url"]).hostname


def _member(user, organization, level=Level.MEMBER, role=OrganizationRoles.MEMBER):
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=organization, role=role, level=level, is_active=True
    )


# --- C: cross-tenant ACS takeover -------------------------------------------


def test_c_foreign_idp_assertion_by_relay_state_neither_logs_in_nor_rebinds(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """Org A's IdP asserts a B-only user, posted with A's configuration relay_state.

    The pre-rework ACS looked the IdP up by that relay_state, accepted the
    unsolicited assertion, set ``user.organization = org A`` and minted a token.
    """

    victim = saml_tenants.b_only
    response = api_client.post(
        "/saml2_auth/acs/",
        {
            "RelayState": idp_a.relay_state,
            "SAMLResponse": signed_a_response(
                request_id="never-issued", email=victim.email
            ),
        },
    )

    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=victim).exists()
    victim.refresh_from_db()
    assert victim.organization_id == saml_tenants.org_b.id


def test_c_bound_flow_foreign_assertion_does_not_rebind_victim(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """Even a correctly correlated A-signed assertion cannot reach a B-only user."""

    victim = saml_tenants.b_only
    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    candidate_key = post_acs(
        api_client,
        attempt.relay,
        signed_a_response(request_id=attempt.request_id, email=victim.email),
    )
    response = complete(api_client, candidate_key)

    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=victim).exists()
    victim.refresh_from_db()
    assert victim.organization_id == saml_tenants.org_b.id
    row = SamlLoginAttempt.objects.get(relay_key=attempt.relay)
    assert (row.state, row.deny_reason) == ("failed", "no_membership")


# --- C2: disabled / deleted configurations ----------------------------------


def test_c2_disabled_idp_relay_state_posted_to_acs_cannot_log_in(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """The pre-rework ACS loaded the row by relay_state alone, ignoring is_enabled."""

    SAMLMetadataModel.no_workspace_objects.filter(id=idp_a.id).update(is_enabled=False)
    response = api_client.post(
        "/saml2_auth/acs/",
        {
            "RelayState": idp_a.relay_state,
            "SAMLResponse": signed_a_response(
                request_id="never-issued", email=saml_tenants.a2.email
            ),
        },
    )

    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=saml_tenants.a2).exists()


@pytest.mark.parametrize("change", [{"is_enabled": False}, {"deleted": True}])
def test_c2_disabled_or_deleted_idp_cannot_start_login(
    api_client, idp_a, saml_tenants, change
):
    SAMLMetadataModel.no_workspace_objects.filter(id=idp_a.id).update(**change)

    response = api_client.get(
        "/saml2_auth/idp-login/", {"email": saml_tenants.a2.email}
    )

    assert response.status_code == 400
    assert not SamlLoginAttempt.objects.filter(user_hint=saml_tenants.a2).exists()


@pytest.mark.parametrize("change", [{"is_enabled": False}, {"deleted": True}])
def test_c2_idp_disabled_or_deleted_mid_login_cannot_complete(
    api_client, idp_a, saml_tenants, signed_a_response, change
):
    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    candidate_key = post_acs(
        api_client,
        attempt.relay,
        signed_a_response(request_id=attempt.request_id, email=saml_tenants.a2.email),
    )
    SAMLMetadataModel.all_objects.filter(id=idp_a.id).update(**change)

    response = complete(api_client, candidate_key)

    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=saml_tenants.a2).exists()


# --- D: IdP chosen by org-name prefix ---------------------------------------


def test_d_login_never_uses_an_idp_of_an_org_whose_name_extends_ours(
    api_client, idp_metadata
):
    """An org named Acme (no IdP) must not start SSO through AcmeCorp's IdP."""

    suffix = uuid.uuid4().hex[:8]
    acme = Organization.objects.create(name=f"Acme{suffix}")
    acme_corp = Organization.objects.create(name=f"Acme{suffix}Corp")
    user = User.objects.create_user(
        email=f"alice-{suffix}@acme-prefix.test",
        password="test-password-not-a-secret",
        name="alice",
        organization=acme,
        organization_role=OrganizationRoles.MEMBER,
    )
    _member(user, acme)
    SAMLMetadataModel.no_workspace_objects.create(
        organization=acme_corp,
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
        relay_state=f"{acme_corp.name}-relay",
        is_enabled=True,
        meta=idp_metadata["okta"],
    )

    response = api_client.get("/saml2_auth/idp-login/", {"email": user.email})

    assert response.status_code == 400
    assert not SamlLoginAttempt.objects.filter(user_hint=user).exists()


def test_d_f_login_uses_the_users_own_idp_when_another_org_has_same_type(
    api_client, idp_a, saml_tenants, idp_keypairs
):
    """Two tenants on Okta: A's user must be sent to A's IdP, never B's.

    The pre-rework metadata loader picked ``filter(identity_type=...).first()``,
    i.e. whichever tenant's Okta row sorted first (newest), not the caller's.
    """

    SAMLMetadataModel.no_workspace_objects.create(
        organization=saml_tenants.org_b,
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
        relay_state=f"b-okta-{uuid.uuid4().hex}",
        is_enabled=True,
        meta=build_idp_metadata(idp_keypairs["b"], "okta"),
    )

    response = api_client.get(
        "/saml2_auth/idp-login/", {"email": saml_tenants.a2.email}
    )

    assert response.status_code == 200, response.content
    assert _idp_host(response) == "idp-a.saml.test"


# --- E: idp-uploads IDOR and missing admin requirement ----------------------


def test_e_list_only_shows_the_callers_organization(saml_tenants, idp_a, idp_b):
    response = _client(saml_tenants.a1).get("/saml2_auth/idp-uploads/")

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["result"]["results"]]
    assert ids == [str(idp_a.id)]


def test_e_foreign_delete_is_404_and_leaves_row_alive(saml_tenants, idp_a, idp_b):
    response = _client(saml_tenants.a1).delete(f"/saml2_auth/idp-uploads/{idp_b.id}/")

    assert response.status_code == 404
    idp_b.refresh_from_db(from_queryset=SAMLMetadataModel.all_objects)
    assert idp_b.deleted is False


def test_e_non_admin_member_cannot_change_the_idp(saml_tenants, idp_a):
    member = _client(saml_tenants.a2)

    put = member.put(
        f"/saml2_auth/idp-uploads/{idp_a.id}/",
        {
            "name": "member-edit",
            "identity_type": str(idp_a.identity_type),
            "is_enabled": "false",
        },
        format="multipart",
    )
    delete = member.delete(f"/saml2_auth/idp-uploads/{idp_a.id}/")

    assert put.status_code == 403
    assert delete.status_code == 403
    idp_a.refresh_from_db(from_queryset=SAMLMetadataModel.all_objects)
    assert (idp_a.deleted, idp_a.is_enabled, idp_a.name) == (False, True, None)


def test_e_non_admin_member_cannot_upload_an_idp(saml_tenants, idp_metadata):
    response = _client(saml_tenants.a2).post(
        "/saml2_auth/idp-uploads/",
        {
            "file": SimpleUploadedFile(
                "idp.xml", idp_metadata["okta"].encode(), content_type="text/xml"
            ),
            "name": "member-upload",
            "identity_type": str(SAMLMetadataModel.IDENTITY_OKTA),
            "is_enabled": "true",
        },
        format="multipart",
    )

    assert response.status_code == 403
    assert not SAMLMetadataModel.all_objects.filter(
        organization=saml_tenants.org_a
    ).exists()


# --- F: stale on-disk metadata named by relay_state -------------------------


def test_f_rotated_metadata_is_used_immediately_and_nothing_is_written_to_disk(
    idp_a, saml_tenants, idp_keypairs, tmp_path
):
    """After an IdP key/entity rotation the next login must use the new metadata.

    The pre-rework loader wrote ``BASE_DIR/metadata/<relay_state>.xml`` once and
    reused it forever. ``conftest`` points ``saml2_auth.views.BASE_DIR`` at this
    test's ``tmp_path``.
    """

    def login_host():
        response = APIClient().get(
            "/saml2_auth/idp-login/", {"email": saml_tenants.a2.email}
        )
        assert response.status_code == 200, response.content
        return _idp_host(response)

    assert login_host() == "idp-a.saml.test"
    SAMLMetadataModel.no_workspace_objects.filter(id=idp_a.id).update(
        meta=build_idp_metadata(idp_keypairs["b"], "okta")
    )

    assert login_host() == "idp-b.saml.test"
    assert not (tmp_path / "metadata").exists()


def test_f_assertion_signed_by_rotated_out_key_is_refused(
    api_client, idp_a, saml_tenants, idp_keypairs, signed_a_response
):
    SAMLMetadataModel.no_workspace_objects.filter(id=idp_a.id).update(
        meta=build_idp_metadata(idp_keypairs["b"], "okta")
    )
    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    candidate_key = post_acs(
        api_client,
        attempt.relay,
        signed_a_response(request_id=attempt.request_id, email=saml_tenants.a2.email),
    )

    response = complete(api_client, candidate_key)

    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=saml_tenants.a2).exists()
