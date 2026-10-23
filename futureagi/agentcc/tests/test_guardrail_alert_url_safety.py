"""Django refuses a
malformed callback URL in a guardrail check (url, endpoint, base_url) or an
alert channel (url) on save, with a 400 that names the field.

The rule is well-formedness only: an absolute http or https URL with a host,
no user or password and no
control characters. It never judges the address: a self-hosted LAN host name
is well-formed and saves, and the gateway's egress guard refuses a loopback,
private or metadata address when it dials (mcp.egress.private_allow opens a
listed loopback or private address, never a metadata one).
So these tests refuse malformed URLs and accept well-formed private ones.

Only a URL a save introduces or changes is checked: a save that leaves a stored
URL as it is (a toggle, a routing save, the Fallbacks page posting the active
guardrails back, a policy edit of another check) is never refused for it, and
activating an older version (a rollback) is not refused for it either.
"""

import copy
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer
from agentcc.services import guardrail_urls

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
UPDATE_GUARDRAIL = f"{GATEWAY}/update-guardrail/"
TOGGLE_GUARDRAIL = f"{GATEWAY}/toggle-guardrail/"
ORG_CONFIGS = "/agentcc/org-configs/"
POLICIES = "/agentcc/guardrail-policies/"

# Malformed URLs (not address-class rejections: those are the egress guard's).
MALFORMED = [
    "ftp://example.com/guard",  # not http(s)
    "http:///no-host",  # no host
    "https://user:pass@example.com/guard",  # userinfo
    "http://exa mple.com/guard",  # a space
    "http://example.com/\x7fguard",  # DEL, which Go's net/url refuses
    "not-a-url",  # no scheme, no host
    "javascript:alert(1)",  # not http(s), no host
]
# Well-formed URLs that save here: the egress guard decides the address.
WELLFORMED = [
    "https://api.lakera.ai/v2/guard",
    "http://ollama.internal:11434/v1/guard",
    "http://127.0.0.1:5001/analyze",
    "http://[::1]:8080/hook",
]
URL_RULE = "absolute http or https URL"


@contextmanager
def _gateway():
    """The gateway client every push path uses, as a mock that records pushes."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _make_config(organization, version=1, is_active=True, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=version, is_active=is_active, **fields
    )


def _versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _assert_refused_naming(response, field):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = json.dumps(response.json())
    assert field in body, body
    assert URL_RULE in body, body


def _hook(url, enabled=True):
    return {"enabled": enabled, "action": "log", "config": {"url": url}}


def _lakera(endpoint):
    return {
        "enabled": True,
        "action": "block",
        "config": {"provider": "lakera", "endpoint": endpoint, "api_key": "k"},
    }


def _alerting(url, name="ops", kind="webhook"):
    return {"channels": [{"name": name, "type": kind, "url": url}]}


# ----------------------------------------------------------------------------
# The rule and the scanners.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("url", WELLFORMED)
def test_is_http_url_accepts_wellformed(url):
    assert guardrail_urls.is_http_url(url) is True


@pytest.mark.parametrize("url", MALFORMED)
def test_is_http_url_rejects_malformed(url):
    assert guardrail_urls.is_http_url(url) is False


def test_guardrail_scanner_reads_every_stored_shape():
    assert (
        guardrail_urls.guardrail_bad_url_path(
            {"checks": {"lakera-guard": _lakera("ftp://x")}}
        )
        == "guardrails.checks.lakera-guard.config.endpoint"
    )
    assert (
        guardrail_urls.guardrail_bad_url_path(
            {"rules": [{"name": "h", "config": {"url": "http:///x"}}]}
        )
        == "guardrails.rules[0].config.url"
    )
    assert (
        guardrail_urls.guardrail_bad_url_path(
            {"checks": [{"name": "fi", "config": {"base_url": "not-a-url"}}]}
        )
        == "guardrails.checks[0].config.base_url"
    )
    # A region is a host fragment, not a URL; an empty url is "no URL".
    assert (
        guardrail_urls.guardrail_bad_url_path(
            {"checks": {"b": {"config": {"region": "us east", "url": ""}}}}
        )
        is None
    )


def test_alerting_scanner_reads_both_shapes():
    assert (
        guardrail_urls.alerting_bad_url_path(_alerting("http:///nohost"))
        == "alerting.channels[0].url"
    )
    assert (
        guardrail_urls.alerting_bad_url_path(
            {"channels": {"ops": {"type": "slack", "url": "ftp://x"}}}
        )
        == "alerting.channels.ops.url"
    )
    # A log channel has no url.
    assert guardrail_urls.alerting_bad_url_path({"channels": [{"type": "log"}]}) is None


def test_changed_gate_leaves_a_stored_bad_url_alone():
    stored = {"checks": {"h": _hook("ftp://old")}}
    guardrail_urls.reject_changed_bad_guardrail_urls(
        {"checks": {"h": _hook("ftp://old", enabled=False)}}, stored
    )  # switching it off is not refused
    with pytest.raises(guardrail_urls.BadGuardrailURL):
        guardrail_urls.reject_changed_bad_guardrail_urls(
            {"checks": {"h": _hook("ftp://new")}}, stored
        )
    stored_alerting = _alerting("ftp://old")
    guardrail_urls.reject_changed_bad_alerting_urls(
        copy.deepcopy(stored_alerting), stored_alerting
    )
    with pytest.raises(guardrail_urls.BadAlertingURL):
        guardrail_urls.reject_changed_bad_alerting_urls(
            _alerting("ftp://new"), stored_alerting
        )


# ----------------------------------------------------------------------------
# Serializers.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("url", MALFORMED)
def test_write_serializer_refuses_a_malformed_guardrail_url(url):
    s = AgentccOrgConfigWriteSerializer(
        data={"guardrails": {"checks": {"my-hook": _hook(url)}}}
    )
    assert not s.is_valid()
    assert "guardrails.checks.my-hook.config.url" in str(s.errors["guardrails"])


def test_write_serializer_refuses_a_malformed_alert_url():
    s = AgentccOrgConfigWriteSerializer(data={"alerting": _alerting("http:///nohost")})
    assert not s.is_valid()
    assert "alerting.channels[0].url" in str(s.errors["alerting"])


@pytest.mark.parametrize("url", WELLFORMED)
def test_write_serializer_accepts_a_wellformed_url(url):
    s = AgentccOrgConfigWriteSerializer(
        data={
            "guardrails": {"checks": {"lakera-guard": _lakera(url)}},
            "alerting": _alerting(url),
        }
    )
    assert s.is_valid(), s.errors


def test_policy_serializer_refuses_a_malformed_check_url():
    s = AgentccGuardrailPolicySerializer(
        data={"name": "p", "checks": [{"name": "lakera-guard", **_lakera("ftp://x")}]}
    )
    assert not s.is_valid()
    assert "checks[0].config.endpoint" in str(s.errors["checks"])


def test_policy_serializer_accepts_a_wellformed_check_url():
    s = AgentccGuardrailPolicySerializer(
        data={
            "name": "p",
            "checks": [{"name": "my-hook", **_hook("http://hooks.internal/x")}],
        }
    )
    assert s.is_valid(), s.errors


# ----------------------------------------------------------------------------
# Every save path, end to end.
# ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestGatewaySaves:
    """update-config, update-guardrail and toggle-guardrail write through the
    gateway view's one save helper (_update_org_config)."""

    def test_update_config_refuses_a_malformed_guardrail_url(
        self, auth_client, organization
    ):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                UPDATE_CONFIG,
                {"guardrails": {"checks": {"my-hook": _hook("ftp://evil/guard")}}},
                format="json",
            )
        _assert_refused_naming(r, "guardrails.checks.my-hook.config.url")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    def test_update_config_refuses_a_malformed_alert_url(
        self, auth_client, organization
    ):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                UPDATE_CONFIG,
                {"alerting": _alerting("https://u:p@hooks.example.com/x")},
                format="json",
            )
        _assert_refused_naming(r, "alerting.channels[0].url")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    def test_update_config_saves_wellformed_private_urls(
        self, auth_client, organization
    ):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                UPDATE_CONFIG,
                {
                    "guardrails": {
                        "checks": {
                            "lakera-guard": _lakera("http://ollama.internal:11434/g")
                        }
                    },
                    "alerting": _alerting("http://10.0.0.5:9000/alerts"),
                },
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK, r.json()
        assert _versions(organization) == [(1, False), (2, True)]
        gateway.set_org_config.assert_called_once()

    def test_update_guardrail_refuses_a_malformed_endpoint(
        self, auth_client, organization
    ):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                UPDATE_GUARDRAIL,
                {"name": "lakera-guard", "config": _lakera("ftp://evil/guard")},
                format="json",
            )
        _assert_refused_naming(r, "config.endpoint")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    def test_saves_that_leave_a_stored_bad_url_alone_are_not_refused(
        self, auth_client, organization
    ):
        # A stored version with a malformed webhook url and
        # a malformed alert url.
        _make_config(
            organization,
            guardrails={"rules": [{"name": "my-hook", **_hook("ftp://old")}]},
            alerting=_alerting("ftp://old-alert"),
        )
        with _gateway():
            toggled = auth_client.post(
                TOGGLE_GUARDRAIL, {"name": "my-hook", "enabled": False}, format="json"
            )
            routing = auth_client.post(
                UPDATE_CONFIG, {"routing": {"strategy": "round_robin"}}, format="json"
            )
            other = auth_client.post(
                UPDATE_GUARDRAIL,
                {"name": "lakera-guard", "config": _lakera("https://api.lakera.ai/g")},
                format="json",
            )
        for r in (toggled, routing, other):
            assert r.status_code == status.HTTP_200_OK, r.json()
        assert _active(organization).guardrails["rules"][0]["enabled"] is False

    def test_changing_a_stored_bad_url_to_another_bad_one_is_refused(
        self, auth_client, organization
    ):
        _make_config(organization, alerting=_alerting("ftp://old-alert"))
        with _gateway():
            r = auth_client.post(
                UPDATE_CONFIG, {"alerting": _alerting("ftp://new-alert")}, format="json"
            )
        _assert_refused_naming(r, "alerting.channels[0].url")


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigCreate:
    """POST /agentcc/org-configs/ (the whole-config editor and the Fallbacks
    page) validates through the write serializer."""

    def test_refuses_a_malformed_guardrail_url(self, auth_client, organization):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                ORG_CONFIGS,
                {"guardrails": {"rules": [{"name": "my-hook", **_hook("http:///x")}]}},
                format="json",
            )
        _assert_refused_naming(r, "guardrails.rules[0].config.url")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).exists()

    def test_refuses_a_malformed_alert_url(self, auth_client, organization):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                ORG_CONFIGS,
                {
                    "alerting": {
                        "channels": {"ops": {"type": "slack", "url": "ftp://x"}}
                    }
                },
                format="json",
            )
        _assert_refused_naming(r, "alerting.channels.ops.url")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    def test_saves_wellformed_private_urls(self, auth_client, organization):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                ORG_CONFIGS,
                {
                    "guardrails": {
                        "checks": {"my-hook": _hook("http://hooks.internal:8080/x")}
                    },
                    "alerting": _alerting("http://10.0.0.5:9000/alerts"),
                },
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK, r.json()
        assert _versions(organization) == [(1, False), (2, True)]
        gateway.set_org_config.assert_called_once()

    def test_posting_the_stored_sections_back_is_not_refused(
        self, auth_client, organization
    ):
        # The Fallbacks page saves routing by posting the active config back,
        # guardrails and alerting as stored, here with malformed stored urls.
        _make_config(
            organization,
            guardrails={"checks": {"my-hook": _hook("ftp://old")}},
            alerting=_alerting("ftp://old-alert"),
        )
        active = auth_client.get(f"{ORG_CONFIGS}active/").json()["result"]
        with _gateway():
            r = auth_client.post(
                ORG_CONFIGS,
                {
                    "guardrails": active["guardrails"],
                    "alerting": active["alerting"],
                    "routing": {"strategy": "round_robin"},
                    "change_description": "Updated fallback & reliability config",
                },
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK, r.json()
        assert _active(organization).routing == {"strategy": "round_robin"}

    def test_activating_an_older_version_with_a_stored_bad_url_works(
        self, auth_client, organization
    ):
        # A rollback is not a save of the url: it is not refused for it.
        old = _make_config(
            organization,
            version=1,
            is_active=False,
            alerting=_alerting("ftp://old-alert"),
        )
        _make_config(organization, version=2)
        with _gateway():
            r = auth_client.post(f"{ORG_CONFIGS}{old.id}/activate/", format="json")
        assert r.status_code == status.HTTP_200_OK, r.json()
        assert _active(organization).version == 1


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailPolicies:
    """A policy's checks reach the org config through policy sync, which runs
    outside the save helper, so the policy serializer checks them."""

    def test_create_refuses_a_malformed_check_url(self, auth_client, organization):
        _make_config(organization)
        with _gateway() as gateway:
            r = auth_client.post(
                POLICIES,
                {
                    "name": "p",
                    "checks": [{"name": "lakera-guard", **_lakera("ftp://evil")}],
                },
                format="json",
            )
        _assert_refused_naming(r, "checks[0].config.endpoint")
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="p"
        ).exists()
        gateway.set_org_config.assert_not_called()

    def test_update_of_another_check_keeps_a_stored_bad_url(
        self, auth_client, organization
    ):
        _make_config(organization)
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="p",
            checks=[{"name": "my-hook", **_hook("ftp://old")}],
        )
        keep = [
            {"name": "my-hook", **_hook("ftp://old")},
            {"name": "lakera-guard", **_lakera("https://api.lakera.ai/g")},
        ]
        change = [{"name": "my-hook", **_hook("ftp://new")}]
        with _gateway():
            kept = auth_client.patch(
                f"{POLICIES}{policy.id}/", {"checks": keep}, format="json"
            )
            changed = auth_client.patch(
                f"{POLICIES}{policy.id}/", {"checks": change}, format="json"
            )
        assert kept.status_code == status.HTTP_200_OK, kept.json()
        _assert_refused_naming(changed, "checks[0].config.url")
        policy.refresh_from_db()
        assert [c["name"] for c in policy.checks] == ["my-hook", "lakera-guard"]
