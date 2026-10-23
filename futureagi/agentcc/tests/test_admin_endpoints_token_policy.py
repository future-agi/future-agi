"""Django's gateway admin endpoints hold AGENTCC_ADMIN_TOKEN to the gateway's
admin-token policy.

The gateway refuses to start on a weak token, and the backend's admin
endpoints hold the same policy on their own: a configured token the policy
refuses counts as no token, so every admin request is denied.
"""

import secrets
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from agentcc.models.org_config import AgentccOrgConfig

COMPOSE_DEFAULT = "local-dev-only-admin-token-replace-me"
ADMIN_ENDPOINTS = [
    "/agentcc/org-configs/bulk/",
    "/agentcc/api-keys/bulk/",
    "/agentcc/spend-summary/",
]
WEAK_TOKENS = [
    pytest.param(COMPOSE_DEFAULT, id="compose-default"),
    pytest.param("agentcc-admin-secret", id="published-default"),
    pytest.param("agentcc-admin-secret000000000000", id="published-default-padded"),
    pytest.param("q7Vd9kLm2Xp4Rt8Zw1Ny6Bc3Hf5Js0G", id="31-characters"),
]


def _get(url, configured, presented, allow_insecure=False):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {presented}")
    with (
        patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", configured),
        patch("agentcc.permissions.AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", allow_insecure),
    ):
        return client.get(url)


@pytest.fixture(autouse=True)
def _fresh_warnings():
    with patch("agentcc.admin_token._warned", set()):
        yield


def test_org_configs_bulk_refuses_the_compose_default_and_answers_a_generated_token(
    organization,
):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )
    url = "/agentcc/org-configs/bulk/"

    refused = _get(url, COMPOSE_DEFAULT, COMPOSE_DEFAULT)
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert str(organization.id) not in refused.content.decode()

    token = secrets.token_hex(32)
    answered = _get(url, token, token)
    assert answered.status_code == status.HTTP_200_OK
    assert str(organization.id) in answered.json()["result"]


@pytest.mark.parametrize("configured", WEAK_TOKENS)
@pytest.mark.parametrize("url", ADMIN_ENDPOINTS)
def test_a_weak_configured_token_denies_even_the_matching_bearer(db, url, configured):
    assert _get(url, configured, configured).status_code == (status.HTTP_403_FORBIDDEN)


@pytest.mark.parametrize("url", ADMIN_ENDPOINTS)
def test_a_generated_token_is_accepted_and_only_itself(db, url):
    token = secrets.token_hex(32)
    assert _get(url, token, token).status_code == status.HTTP_200_OK
    assert _get(url, token, secrets.token_hex(32)).status_code == (
        status.HTTP_403_FORBIDDEN
    )


@pytest.mark.parametrize("url", ADMIN_ENDPOINTS)
def test_the_dev_opt_in_accepts_the_compose_default(db, url):
    # The E2E stack runs the compose default with the opt-in, on the gateway
    # and on the backend alike.
    response = _get(url, COMPOSE_DEFAULT, COMPOSE_DEFAULT, allow_insecure=True)
    assert response.status_code == status.HTTP_200_OK


def test_the_dev_opt_in_never_accepts_an_empty_token(db):
    url = "/agentcc/org-configs/bulk/"
    assert _get(url, "", "", allow_insecure=True).status_code == (
        status.HTTP_403_FORBIDDEN
    )


def test_a_refused_token_is_logged_once_by_problem_never_by_value(db):
    url = "/agentcc/api-keys/bulk/"
    with capture_logs() as logs:
        _get(url, COMPOSE_DEFAULT, COMPOSE_DEFAULT)
        _get(url, COMPOSE_DEFAULT, COMPOSE_DEFAULT)

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1, logs
    assert "published" in warnings[0]["problem"]
    assert COMPOSE_DEFAULT not in str(warnings[0])
