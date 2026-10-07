"""SAML_LOGIN_ENABLED is an ops kill switch for initiation, ACS and completion.

It defaults to on (see tfc/tests/test_settings_env.py), so tenant-scoped SAML
keeps working for organizations that already use it; switching it off refuses
every step without issuing a credential.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from accounts.models.auth_token import AuthToken
from saml2_auth.tests.conftest import bound_attempt, complete, post_acs

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _signed_login(api_client, saml_tenants, signed_a_response):
    attempt = bound_attempt(api_client, email=saml_tenants.a1.email)
    payload = signed_a_response(
        request_id=attempt.request_id, email=saml_tenants.a1.email
    )
    return attempt, payload


def test_tenant_login_completes_while_the_switch_is_on(
    api_client, idp_a, saml_tenants, signed_a_response
):
    attempt, payload = _signed_login(api_client, saml_tenants, signed_a_response)

    response = complete(api_client, post_acs(api_client, attempt.relay, payload))

    assert "sso_token=" in response["Location"]


@override_settings(SAML_LOGIN_ENABLED=False)
def test_switched_off_initiation_is_refused(api_client, idp_a, saml_tenants):
    response = api_client.get(
        "/saml2_auth/idp-login/", {"email": saml_tenants.a1.email}
    )

    assert response.status_code == 400
    assert not [name for name in response.cookies if name.startswith("fai_saml_b_")]


def test_switched_off_acs_is_refused(
    api_client, idp_a, saml_tenants, signed_a_response
):
    attempt, payload = _signed_login(api_client, saml_tenants, signed_a_response)

    with override_settings(SAML_LOGIN_ENABLED=False):
        response = api_client.post(
            "/saml2_auth/acs/", {"RelayState": attempt.relay, "SAMLResponse": payload}
        )

    assert response.status_code == 302
    assert "denied=true" in response["Location"]


def test_switching_off_after_acs_refuses_completion(
    api_client, idp_a, saml_tenants, signed_a_response
):
    attempt, payload = _signed_login(api_client, saml_tenants, signed_a_response)
    candidate_key = post_acs(api_client, attempt.relay, payload)
    tokens_before = AuthToken.no_workspace_objects.filter(
        user=saml_tenants.a1
    ).count()

    with override_settings(SAML_LOGIN_ENABLED=False):
        response = complete(api_client, candidate_key)

    assert "sso_token" not in response["Location"]
    assert "denied=true" in response["Location"]
    assert (
        AuthToken.no_workspace_objects.filter(user=saml_tenants.a1).count()
        == tokens_before
    )
