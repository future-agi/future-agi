from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models.auth_token import AuthToken
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from saml2_auth.models import (
    SamlLoginAttempt,
    SAMLMetadataModel,
    SamlResponseCandidate,
)
from saml2_auth.services import claim_attempt, resolve_login_idp, store_candidate
from saml2_auth.tests.conftest import bound_attempt, complete, post_acs

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
    candidate_key = post_acs(
        api_client,
        attempt.relay,
        signed_a_response(
            request_id=f"wrong-{attempt.request_id}", email=saml_tenants.a2.email
        ),
    )
    response = complete(api_client, candidate_key)

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
    candidate_key = first["Location"].split("c=", maxsplit=1)[1]
    completion = complete(api_client, candidate_key)
    replay = api_client.post(
        "/saml2_auth/acs/", {"RelayState": attempt.relay, "SAMLResponse": payload}
    )

    assert first.status_code == 303
    assert "sso_token" in completion["Location"]
    assert replay.status_code == 302
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
    candidate_key = candidate_response["Location"].split("c=", maxsplit=1)[1]
    attacker_completion = complete(attacker, candidate_key)
    assert "sso_token" not in attacker_completion["Location"]
    initiator_completion = complete(initiator, candidate_key)
    assert "sso_token" in initiator_completion["Location"]
    assert AuthToken.no_workspace_objects.filter(user=saml_tenants.a2).count() == 1


def test_losing_duplicate_completion_leaves_the_claimed_attempt_alone(
    api_client, idp_a, saml_tenants, signed_a_response
):
    """A duplicate ACS post that loses the claim must not fail the login in flight."""

    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    payload = signed_a_response(
        request_id=attempt.request_id, email=saml_tenants.a2.email
    )
    winner = post_acs(api_client, attempt.relay, payload)
    loser = post_acs(api_client, attempt.relay, payload)
    # The winner has claimed and is still issuing when the loser completes.
    claim_attempt(
        candidate_key=winner,
        binder=api_client.cookies[attempt.binder_cookie].value,
    )

    denied = complete(api_client, loser)

    assert "sso_token" not in denied["Location"]
    assert SamlLoginAttempt.objects.get(relay_key=attempt.relay).state == "claimed"


def test_preferred_idp_is_found_after_two_other_enabled_idps(
    saml_tenants, idp_a, idp_b, idp_metadata
):
    preferred_org = Organization.objects.create(name="SAML Tenant Preferred")
    OrganizationMembership.no_workspace_objects.create(
        user=saml_tenants.a2,
        organization=preferred_org,
        role="Member",
        level=3,
        is_active=True,
    )
    saml_tenants.a2.organization = preferred_org
    saml_tenants.a2.save(update_fields=["organization"])
    preferred_idp = SAMLMetadataModel.no_workspace_objects.create(
        organization=preferred_org,
        identity_type=SAMLMetadataModel.IDENTITY_GOOGLE,
        relay_state="preferred-relay",
        is_enabled=True,
        meta=idp_metadata["google"],
    )

    assert resolve_login_idp(saml_tenants.a2).id == str(preferred_idp.id)


def test_expired_candidates_do_not_consume_capacity(
    api_client, idp_a, saml_tenants, settings
):
    settings.SAML_MAX_CANDIDATE_ROWS = 1
    attempt = bound_attempt(api_client, email=saml_tenants.a2.email)
    attempt_row = SamlLoginAttempt.objects.get(relay_key=attempt.relay)
    expired_at = timezone.now() - timedelta(seconds=1)
    SamlResponseCandidate.objects.bulk_create(
        [
            SamlResponseCandidate(
                candidate_key=f"expired-{index}",
                attempt=attempt_row,
                payload=b"expired",
                payload_bytes=7,
                expires_at=expired_at,
            )
            for index in range(101)
        ]
    )

    candidate = store_candidate(attempt=attempt_row, payload=b"live")

    assert candidate.payload == b"live"


def test_a_flood_of_attempts_cannot_lock_a_user_out(
    api_client, idp_a, saml_tenants, settings
):
    """Initiation needs only an email, so a full per-user window must not refuse."""

    settings.SAML_MAX_ATTEMPTS_PER_USER_PER_TTL = 3
    flood = [
        bound_attempt(APIClient(), email=saml_tenants.a2.email).relay for _ in range(3)
    ]

    own = bound_attempt(api_client, email=saml_tenants.a2.email)

    relays = set(
        SamlLoginAttempt.objects.filter(user_hint=saml_tenants.a2).values_list(
            "relay_key", flat=True
        )
    )
    assert relays == {*flood[1:], own.relay}
