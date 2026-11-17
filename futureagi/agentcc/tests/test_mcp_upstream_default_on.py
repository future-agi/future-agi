"""Upstream OAuth alone uses a direct deployment environment switch."""

import pytest

from agentcc import mcp_features


UPSTREAM = mcp_features.UPSTREAM_OAUTH
HOSTED = ("US", "EU", "DEV", " us ", "eu", "\tdev\n")
OTHER_FLAGS = (
    mcp_features.CLIENT_OAUTH,
    mcp_features.K2_APPS,
    mcp_features.CLIENT_INSTALLS,
    mcp_features.TOOL_CALL_EXPLORER,
    mcp_features.TOOL_CALL_DOWNLOAD,
    mcp_features.CATALOG,
    mcp_features.CREDENTIAL_VAULT,
    mcp_features.VIRTUAL_SERVERS,
)


@pytest.mark.parametrize("deployment", ("", "local", "self-hosted", "staging", "US-east"))
def test_unset_selfhosted_enabled_despite_false_setting(settings, monkeypatch, deployment):
    monkeypatch.delenv(UPSTREAM, raising=False)
    settings.CLOUD_DEPLOYMENT = deployment
    setattr(settings, UPSTREAM, False)
    assert mcp_features.enabled(UPSTREAM) is True


@pytest.mark.parametrize("deployment", HOSTED)
def test_unset_hosted_disabled_despite_true_setting(settings, monkeypatch, deployment):
    monkeypatch.delenv(UPSTREAM, raising=False)
    settings.CLOUD_DEPLOYMENT = deployment
    setattr(settings, UPSTREAM, True)
    assert mcp_features.enabled(UPSTREAM) is False


@pytest.mark.parametrize("deployment", ("local", "US", "EU", "DEV"))
@pytest.mark.parametrize("value", ("false", "0", "off", "no", "", " FALSE ", " Off ", " NO ", "  "))
def test_explicit_false_disables(settings, monkeypatch, deployment, value):
    monkeypatch.setenv(UPSTREAM, value)
    settings.CLOUD_DEPLOYMENT = deployment
    setattr(settings, UPSTREAM, True)
    assert mcp_features.enabled(UPSTREAM) is False


@pytest.mark.parametrize("deployment", ("local",) + HOSTED)
@pytest.mark.parametrize("value", ("true", "1", "yes", "on", " TRUE ", " Yes ", " ON ", " 1 "))
def test_explicit_true_enables_despite_false_setting(settings, monkeypatch, deployment, value):
    monkeypatch.setenv(UPSTREAM, value)
    settings.CLOUD_DEPLOYMENT = deployment
    setattr(settings, UPSTREAM, False)
    assert mcp_features.enabled(UPSTREAM) is True


@pytest.mark.parametrize("deployment", ("local", "US", "EU", "DEV"))
@pytest.mark.parametrize("value", ("garbage", "2", "truthy", "true,false", "enable", "null"))
def test_malformed_disables(settings, monkeypatch, deployment, value):
    monkeypatch.setenv(UPSTREAM, value)
    settings.CLOUD_DEPLOYMENT = deployment
    setattr(settings, UPSTREAM, True)
    assert mcp_features.enabled(UPSTREAM) is False


@pytest.mark.parametrize("name", OTHER_FLAGS)
@pytest.mark.parametrize("value", (True, False, 1, 0, "true", "false", "", None))
def test_other_flags_keep_strict_settings(settings, monkeypatch, name, value):
    monkeypatch.delenv(UPSTREAM, raising=False)
    monkeypatch.setenv(name, "false" if value is True else "true")
    setattr(settings, name, value)
    assert mcp_features.enabled(name) is (value is True)


@pytest.mark.parametrize("name", OTHER_FLAGS)
def test_other_flags_missing_stay_disabled(settings, monkeypatch, name):
    monkeypatch.delenv(UPSTREAM, raising=False)
    monkeypatch.setenv(name, "true")
    if hasattr(settings, name):
        delattr(settings, name)
    assert mcp_features.enabled(name) is False


def test_upstream_reads_environment_each_time(settings, monkeypatch):
    monkeypatch.delenv(UPSTREAM, raising=False)
    settings.CLOUD_DEPLOYMENT = "local"
    setattr(settings, UPSTREAM, False)
    assert mcp_features.enabled(UPSTREAM) is True
    monkeypatch.setenv(UPSTREAM, "false")
    assert mcp_features.enabled(UPSTREAM) is False
    settings.CLOUD_DEPLOYMENT = "US"
    monkeypatch.setenv(UPSTREAM, "true")
    assert mcp_features.enabled(UPSTREAM) is True
    monkeypatch.delenv(UPSTREAM)
    setattr(settings, UPSTREAM, True)
    assert mcp_features.enabled(UPSTREAM) is False
