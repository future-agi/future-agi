"""The keys a Future AGI Eval guardrail gets in the config pushed to the
gateway (agentcc.services.config_push._inject_fi_credentials)."""

import pytest
import structlog
from django.test import override_settings

from accounts.models.user import OrgApiKey
from agentcc.services.config_push import _inject_fi_credentials

CLOUD_URL = "https://api.futureagi.com"
GATEWAY_URL = "http://backend"
TYPED_KEYS = {"api_key": "typed-api-key", "secret_key": "typed-secret-key"}
# A self-hosted install as the compose files and the Helm chart deploy it.
SELF_HOSTED = {
    "CLOUD_DEPLOYMENT": "",
    "BASE_URL": "http://localhost:8000",
    "AGENTCC_GATEWAY_FI_BASE_URL": GATEWAY_URL,
}


@pytest.fixture
def system_key(organization):
    return OrgApiKey.no_workspace_objects.create(
        organization=organization, type="system"
    )


def _inject(organization, base_url):
    config = {"eval_ids": ["76"], **TYPED_KEYS}
    if base_url is not None:
        config["base_url"] = base_url
    checks = {"futureagi-eval": {"enabled": True, "action": "block", "config": config}}
    with structlog.testing.capture_logs() as logs:
        _inject_fi_credentials(checks, organization.id)
    return checks["futureagi-eval"]["config"], [log["event"] for log in logs]


def _assert_org_key(cfg, events, system_key):
    assert cfg["api_key"] == system_key.api_key
    assert cfg["secret_key"] == system_key.secret_key
    assert "fi_credentials_injected" in events


def _assert_typed_keys(cfg, events):
    assert {key: cfg[key] for key in TYPED_KEYS} == TYPED_KEYS
    assert "fi_credentials_skipped_cross_env" in events


@override_settings(**SELF_HOSTED)
def test_self_hosted_check_at_clouds_url_goes_to_this_install_with_its_key(
    organization, system_key
):
    """Self-hosted gateways call this install's API for a check at Cloud's
    URL, the dashboard's default. Keys typed for Cloud fail there, and a check
    set to block then refuses every request. The org's key goes out with this
    install's URL only, never with Cloud's."""
    cfg, events = _inject(organization, CLOUD_URL)

    _assert_org_key(cfg, events, system_key)
    assert cfg["base_url"] == GATEWAY_URL


@override_settings(**{**SELF_HOSTED, "AGENTCC_GATEWAY_FI_BASE_URL": ""})
def test_check_at_clouds_url_keeps_its_url_and_keys_without_the_gateway_url(
    organization, system_key
):
    """A backend image newer than its compose file, say: the check keeps
    Cloud's URL, so it keeps the keys typed for Cloud too."""
    cfg, events = _inject(organization, CLOUD_URL)

    _assert_typed_keys(cfg, events)
    assert cfg["base_url"] == CLOUD_URL


@override_settings(**SELF_HOSTED)
@pytest.mark.parametrize(
    "base_url",
    [
        # The gateway calls Cloud for any spelling of its URL but the exact one.
        "https://api.futureagi.com/",
        "https://dev.api.futureagi.com",
        "https://eval.example.com",
    ],
)
def test_self_hosted_check_elsewhere_keeps_its_url_and_keys(
    organization, system_key, base_url
):
    cfg, events = _inject(organization, base_url)

    _assert_typed_keys(cfg, events)
    assert cfg["base_url"] == base_url


@override_settings(**SELF_HOSTED)
@pytest.mark.parametrize(
    "base_url, pushed",
    [
        # No Base URL: this install's.
        (None, "http://localhost:8000"),
        # BASE_URL, spelled otherwise.
        ("http://LOCALHOST:8000/", "http://LOCALHOST:8000/"),
    ],
)
def test_check_at_this_installs_url_gets_the_orgs_own_key(
    organization, system_key, base_url, pushed
):
    cfg, events = _inject(organization, base_url)

    _assert_org_key(cfg, events, system_key)
    assert cfg["base_url"] == pushed


@override_settings(
    CLOUD_DEPLOYMENT="DEV",
    BASE_URL="https://dev.api.futureagi.com",
    AGENTCC_GATEWAY_FI_BASE_URL=GATEWAY_URL,
)
def test_cloud_keeps_the_typed_keys_of_a_check_at_another_environment(
    organization, system_key
):
    """Unchanged on Cloud, whose gateways call Cloud's URL as given: a check
    in its dev environment aimed at the production API keeps its URL and the
    keys typed for it."""
    cfg, events = _inject(organization, CLOUD_URL)

    _assert_typed_keys(cfg, events)
    assert cfg["base_url"] == CLOUD_URL


@override_settings(
    CLOUD_DEPLOYMENT="DEV",
    BASE_URL="https://dev.api.futureagi.com",
    AGENTCC_GATEWAY_FI_BASE_URL="",
)
@pytest.mark.parametrize(
    "base_url",
    [None, "https://dev.api.futureagi.com", "https://DEV.api.futureagi.com/"],
)
def test_cloud_check_at_its_own_environment_gets_the_orgs_own_key(
    organization, system_key, base_url
):
    cfg, events = _inject(organization, base_url)

    _assert_org_key(cfg, events, system_key)
