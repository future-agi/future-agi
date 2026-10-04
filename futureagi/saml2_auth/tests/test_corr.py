from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from accounts.models.auth_token import AuthToken
from saml2_auth.tests.conftest import bound_attempt

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_unsolicited_response_denied(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """A signed response without an issued attempt is never a login."""

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


def test_mismatched_in_response_to_denied(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """The response must correlate to the exact request, not just an IdP row."""

    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    response = api_client.post(
        "/saml2_auth/acs/",
        {
            "RelayState": attempt.relay,
            "SAMLResponse": signed_a_response(
                request_id=f"wrong-{attempt.request_id}", email=saml_tenants.a2.email
            ),
        },
    )

    assert "sso_token" not in response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=saml_tenants.a2).exists()


def test_replay_of_consumed_attempt_denied(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """A response becomes unusable after the bound attempt is consumed."""

    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    payload = signed_a_response(
        request_id=attempt.request_id, email=saml_tenants.a2.email
    )
    first = api_client.post(
        "/saml2_auth/acs/", {"RelayState": attempt.relay, "SAMLResponse": payload}
    )
    replay = api_client.post(
        "/saml2_auth/acs/", {"RelayState": attempt.relay, "SAMLResponse": payload}
    )

    assert first.status_code == 303
    assert replay.status_code == 400
    assert AuthToken.no_workspace_objects.filter(user=saml_tenants.a2).count() == 1


def test_initiating_jar_bound_two_jars(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """A different browser cannot consume a response copied from the initiator."""

    initiator = APIClient()
    attacker = APIClient()
    attempt = bound_attempt(initiator, email=saml_tenants.a2.email)
    payload = signed_a_response(
        request_id=attempt.request_id, email=saml_tenants.a2.email
    )
    candidate_response = attacker.post(
        "/saml2_auth/acs/",
        {"RelayState": attempt.relay, "SAMLResponse": payload},
    )

    assert candidate_response.status_code == 303
    assert "sso_token" not in candidate_response["Location"]
    assert not AuthToken.no_workspace_objects.filter(user=saml_tenants.a2).exists()
