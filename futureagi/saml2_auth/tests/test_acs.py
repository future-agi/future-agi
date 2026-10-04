from __future__ import annotations

import re

import pytest

from accounts.models.auth_token import AuthToken

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_b_only_user_via_a_idp_denied_signed(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """AC-ACS-02: an attempt initiated by a2's client, followed by an A-signed
    assertion naming b_only, must be denied with no credential for b_only.

    On unmodified code the ACS view completes the login for whoever the
    assertion names (no membership check against the IdP's organization), so
    b_only receives an AuthToken. The assertion below fails there, which is the
    RED evidence. The candidate-then-complete hop is not asserted here because
    it does not exist yet; later steps add the jar-based flow.
    """

    from unittest.mock import patch

    with patch("saml2_auth.views.is_work_email", return_value=True):
        initiation = api_client.get(
            "/saml2_auth/idp-login/", {"email": saml_tenants.a2.email}
        )
    assert initiation.status_code == 200, initiation.content
    request_ids = re.findall(r"RequestID=([^&]+)", initiation.json()["result"]["url"])
    request_id = request_ids[0] if request_ids else "unissued-a-request"

    response = api_client.post(
        "/saml2_auth/acs/",
        {
            "RelayState": idp_a.relay_state,
            "SAMLResponse": signed_a_response(
                request_id=request_id, email=saml_tenants.b_only.email
            ),
        },
    )

    assert "sso_token" not in response.get("Location", "")
    assert not AuthToken.no_workspace_objects.filter(
        user=saml_tenants.b_only
    ).exists(), "A-signed assertion naming a B-only user minted an A credential"
