"""Django refuses a malformed callback URL in a guardrail check
(url/endpoint/base_url) or an alert channel on save, the same well-formedness
rule an MCP server URL uses (http or https, a host, no user or password, no
control characters), with a 400 that names the field.

It is well-formedness only, not address class: a self-hosted LAN host name is
well-formed and saved here; the gateway's egress guard refuses a loopback,
private or metadata address at dial time unless the operator lists it in
mcp.egress.private_allow (a metadata address is refused even when listed). So
these tests use malformed URLs, not private ones, and assert a well-formed LAN
URL is accepted.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer
from agentcc.services import config_push, guardrail_urls
from agentcc.views import gateway as gateway_views

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
UPDATE_GUARDRAIL = f"{GATEWAY}/update-guardrail/"
TOGGLE_GUARDRAIL = f"{GATEWAY}/toggle-guardrail/"
ORG_CONFIGS = "/agentcc/org-configs/"
POLICIES = "/agentcc/guardrail-policies/"

# Malformed URLs (not address-class rejections — those are the egress guard's).
MALFORMED = [
    "ftp://example.com/guard",  # not http(s)
    "http:///no-host",  # no host
    "https://user:pass@example.com/guard",  # userinfo
    "http://exa mple.com/guard",  # control/space char
    "not-a-url",  # no scheme, no host
    "",  # empty is "no URL"; a webhook with url="" is caught as missing url below
]
# Well-formed URLs that must be accepted here (the egress guard decides the
# address at runtime), including a self-hosted LAN host name.
WELLFORMED = [
    "https://api.lakera.ai/v2/guard",
    "http://ollama.internal:11434/v1/guard",
    "http://127.0.0.1:5001/analyze",  # loopback is well-formed; egress guard refuses the address
]


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


@pytest.fixture
def active(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )


def _webhook_check(url):
    return {"name": "my-hook", "enabled": True, "action": "log", "config": {"url": url}}


def _external_check(endpoint):
    return {
        "name": "lakera-guard",
        "enabled": True,
        "action": "block",
        "config": {"provider": "lakera", "endpoint": endpoint},
    }


# ----------------------------------------------------------------------------
# Unit: the predicate and the scanners.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("url", WELLFORMED)
def test_is_http_url_accepts_wellformed(url):
    assert guardrail_urls.is_http_url(url) is True


@pytest.mark.parametrize("url", [u for u in MALFORMED if u != ""])
def test_is_http_url_rejects_malformed(url):
    assert guardrail_urls.is_http_url(url) is False


def test_guardrail_scanner_finds_the_field():
    section = {
        "checks": {
            "lakera-guard": {"config": {"provider": "lakera", "endpoint": "ftp://x"}}
        }
    }
    assert (
        guardrail_urls.guardrail_bad_url_path(section)
        == "guardrails.checks.lakera-guard.config.endpoint"
    )


def test_alerting_scanner_finds_the_field():
    alerting = {
        "channels": [{"name": "ops", "type": "webhook", "url": "http:///nohost"}]
    }
    assert guardrail_urls.alerting_bad_url_path(alerting) == "alerting.channels[0].url"


def test_changed_gate_allows_a_stored_bad_url_left_unchanged():
    stored = {"checks": {"h": {"config": {"url": "ftp://old"}}}}
    # Same check, same bad url, plus an unrelated enabled toggle elsewhere.
    section = {"checks": {"h": {"enabled": False, "config": {"url": "ftp://old"}}}}
    guardrail_urls.reject_changed_bad_guardrail_urls(section, stored)  # no raise

    # But changing that url to another malformed one is refused.
    changed = {"checks": {"h": {"config": {"url": "ftp://new"}}}}
    with pytest.raises(guardrail_urls.BadGuardrailURL):
        guardrail_urls.reject_changed_bad_guardrail_urls(changed, stored)


# ----------------------------------------------------------------------------
# Serializer (POST org-configs path): a field-named 400.
# ----------------------------------------------------------------------------


def test_write_serializer_rejects_malformed_guardrail_url():
    s = AgentccOrgConfigWriteSerializer(
        data={"guardrails": {"checks": {"my-hook": {"config": {"url": "ftp://x"}}}}}
    )
    assert not s.is_valid()
    assert "guardrails" in s.errors


def test_write_serializer_rejects_malformed_alert_url():
    s = AgentccOrgConfigWriteSerializer(
        data={
            "alerting": {
                "channels": [
                    {"name": "ops", "type": "webhook", "url": "http:///nohost"}
                ]
            }
        }
    )
    assert not s.is_valid()
    assert "alerting" in s.errors


def test_write_serializer_accepts_wellformed_lan_url():
    s = AgentccOrgConfigWriteSerializer(
        data={
            "guardrails": {
                "checks": {
                    "lakera-guard": {
                        "config": {
                            "provider": "lakera",
                            "endpoint": "http://ollama.internal:11434/guard",
                        }
                    }
                }
            }
        }
    )
    assert s.is_valid(), s.errors


# ----------------------------------------------------------------------------
# update-config (the console save path; it runs the URL check itself).
# ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@patch.object(
    gateway_views.AgentccGatewayViewSet, "_push_current_config", return_value=True
)
class TestUpdateConfigURLs:
    def test_update_config_rejects_malformed_guardrail_url(
        self, push, auth_client, organization, active, gateway
    ):
        r = auth_client.post(
            UPDATE_CONFIG,
            {
                "guardrails": {
                    "checks": {"my-hook": {"config": {"url": "ftp://evil/guard"}}}
                }
            },
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.json()
        assert r.json()["code"] == "guardrail_bad_url"
        assert r.json()["param"] == "guardrails.checks.my-hook.config.url"
        # No new version, nothing pushed.
        assert list(
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=organization
            ).values_list("version", flat=True)
        ) == [1]

    def test_update_config_rejects_malformed_alert_url(
        self, push, auth_client, organization, active, gateway
    ):
        r = auth_client.post(
            UPDATE_CONFIG,
            {
                "alerting": {
                    "channels": [
                        {"name": "ops", "type": "webhook", "url": "https://u:p@h/x"}
                    ]
                }
            },
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.json()
        assert r.json()["code"] == "alerting_bad_url"
        assert r.json()["param"] == "alerting.channels[0].url"

    def test_update_config_accepts_wellformed_lan_guardrail_url(
        self, push, auth_client, organization, active, gateway
    ):
        r = auth_client.post(
            UPDATE_CONFIG,
            {
                "guardrails": {
                    "checks": {
                        "lakera-guard": {
                            "config": {
                                "provider": "lakera",
                                "endpoint": "http://ollama.internal:11434/guard",
                            }
                        }
                    }
                }
            },
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.json()

    def test_update_config_accepts_wellformed_alert_url(
        self, push, auth_client, organization, active, gateway
    ):
        r = auth_client.post(
            UPDATE_CONFIG,
            {
                "alerting": {
                    "channels": [
                        {
                            "name": "ops",
                            "type": "webhook",
                            "url": "https://hooks.example.com/x",
                        }
                    ]
                }
            },
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.json()


# ----------------------------------------------------------------------------
# update-guardrail (stores one rule; names the field).
# ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@patch.object(
    gateway_views.AgentccGatewayViewSet, "_push_current_config", return_value=True
)
def test_update_guardrail_rejects_malformed_endpoint(
    push, auth_client, organization, active, gateway
):
    r = auth_client.post(
        UPDATE_GUARDRAIL,
        {
            "name": "lakera-guard",
            "config": {
                "config": {"provider": "lakera", "endpoint": "ftp://evil/guard"}
            },
        },
        format="json",
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST, r.json()
    assert r.json()["code"] == "guardrail_bad_url"
    assert "param" in r.json()


# ----------------------------------------------------------------------------
# toggle-guardrail of a check whose stored url is malformed: not blocked
# (the save changes only `enabled`, so the stored url is not the save's
# doing — a working option is not removed).
# ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@patch.object(
    gateway_views.AgentccGatewayViewSet, "_push_current_config", return_value=True
)
def test_toggle_off_a_check_with_a_stored_bad_url_is_allowed(
    push, auth_client, organization, gateway
):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        guardrails={
            "rules": [
                {"name": "my-hook", "enabled": True, "config": {"url": "ftp://old"}}
            ]
        },
    )
    r = auth_client.post(
        TOGGLE_GUARDRAIL, {"name": "my-hook", "enabled": False}, format="json"
    )
    assert r.status_code == status.HTTP_200_OK, r.json()


# ----------------------------------------------------------------------------
# Guardrail policy serializer (the policy save path runs the URL check).
# ----------------------------------------------------------------------------


def test_policy_serializer_rejects_malformed_check_url(organization):
    s = AgentccGuardrailPolicySerializer(
        data={
            "name": "p",
            "checks": [
                {
                    "name": "lakera-guard",
                    "config": {"provider": "lakera", "endpoint": "ftp://x"},
                }
            ],
        }
    )
    assert not s.is_valid()
    assert "checks" in s.errors


def test_policy_serializer_accepts_wellformed_check_url(organization):
    s = AgentccGuardrailPolicySerializer(
        data={
            "name": "p",
            "checks": [
                {"name": "my-hook", "config": {"url": "https://hooks.example.com/x"}}
            ],
        }
    )
    assert s.is_valid(), s.errors
