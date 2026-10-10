from __future__ import annotations

import pytest

from accounts.models.auth_token import AuthToken
from saml2_auth.tests.conftest import bound_attempt, complete, post_acs

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_b_only_user_via_a_idp_denied_signed(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """AC-ACS-02: an attempt initiated by a2's client, followed by an A-signed
    assertion naming b_only, must be denied with no credential for b_only.

    The browser-bound flow must still reject the cross-tenant assertion at
    completion, after its candidate is claimed.
    """

    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    candidate_key = post_acs(
        api_client,
        attempt.relay,
        signed_a_response(
            request_id=attempt.request_id, email=saml_tenants.b_only.email
        ),
    )
    response = complete(api_client, candidate_key)

    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(
        user=saml_tenants.b_only
    ).exists(), "A-signed assertion naming a B-only user minted an A credential"
