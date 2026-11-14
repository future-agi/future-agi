"""tfc.ee_loader: deployment-mode detection usable while settings load."""

import pytest

from tfc import ee_loader

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV", "us", " US", "eu\n"])
def test_the_cloud_regions_are_cloud(deployment):
    assert ee_loader.is_cloud_env(deployment)


@pytest.mark.parametrize("deployment", ["", " ", "false", "0", "self-hosted", "USA"])
def test_any_other_value_is_self_hosted(deployment):
    assert not ee_loader.is_cloud_env(deployment)


def test_reads_cloud_deployment_by_default(monkeypatch):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "EU")
    assert ee_loader.is_cloud_env()
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "false")
    assert not ee_loader.is_cloud_env()
    monkeypatch.delenv("CLOUD_DEPLOYMENT")
    assert not ee_loader.is_cloud_env()


@pytest.mark.parametrize(
    "deployment, license_key, oss",
    [("US", "", False), ("false", "", True), ("", "key", False), ("", "", True)],
)
def test_oss_mode_uses_the_same_rule(monkeypatch, deployment, license_key, oss):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    monkeypatch.setenv("EE_LICENSE_KEY", license_key)
    assert ee_loader._is_oss_mode() is oss
